"""One engine = one worker.

Each worker owns a Playwright instance and a persistent browser context for a
single search engine, and runs an event-driven scheduler over one per-engine
``EngineTaskQueue`` (``scraper/tasks.py``). Every "web page access" is a Task; the
queue merges three sources — an on-demand web search (highest priority), a due
news scrape (a timer heap keyed by each topic's *next eligible scrape time*), and
an idle keep-alive warm-up — and the worker runs the highest-priority ready task,
then re-arms the news topic for ``scrape_interval`` later. When nothing is ready
it sleeps until the soonest task comes due. Workers don't know about each other —
the only shared state is the DB (cross-engine duplicates resolve there, via the
URL-derived news id) and a ``SharedEngineState`` the main thread reads for the
saturation signal.

What happens to a task's result is the task's own business: a tracked news scrape
feeds the metrics window → Postgres; a one-off web search is handed straight back
to its waiting caller and not persisted. The worker doesn't branch on that — it
calls the task's ``deliver_*`` hook.

Two independent throttles, deliberately distinct:
    - **Per-topic interval** (the heap key): a topic is never re-scraped by this
      engine sooner than ``scrape_interval`` (plus jitter). This is the freshness
      cadence, decoupled from how many topics there are.
    - **Per-request pace floor** (the ``_Pacer``): a minimum gap between *any* two
      requests this engine makes. When many topics come due together they are
      scraped back-to-back, and the pacer throttles that burst to a known-safe
      rate. The adaptive cooldown (scraper/cooldown.py) is the reactive backstop:
      on a block the engine benches itself and the scheduler stops handing it work
      until the backoff window expires, then sends a single probe.

Throughput model: with few topics the heap head is usually in the future and the
worker idles until it comes due. With many topics the pace floor dominates, the
worker scrapes continuously at its safe rate, and topics simply cycle slower than
``scrape_interval`` (best effort) — which, if the robust engines start blocking,
is the cue the exit IP is saturated.

Boot stagger + per-reschedule jitter keep the access pattern from being a
perfectly regular tick. Topics added/removed in the DB are reconciled on a timer:
new active topics are scheduled, deactivated ones are dropped lazily when their
heap entry surfaces.
"""

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from playwright.sync_api import sync_playwright

from common import database as db
from common.config import (
    FingerprintProfile,
    scraper_config,
)
from common.model import NewsEntry, ScraperLog

from .browser import new_context, profile_dir_for
from .cooldown import EngineCooldownTracker
from .keepalive import DEFAULT_QUERIES, KeepAliveHeartbeat
from .saturation import SharedEngineState
from .scraper import scrape_pages, scrape_topic
from .sources import SearchSource, SearchVertical
from .tasks import (
    WEB_POLL_INTERVAL,
    EngineTaskQueue,
    KeepAliveGenerator,
    NewsScrapeTask,
    NewsTaskGenerator,
    Task,
    WebSearchSource,
    _TopicSchedule,
)
from .webqueue import DbWebSearchQueue, WebSearchQueue

logger = logging.getLogger(__name__)

# Cap how many benched one-offs to fail-fast per loop turn, so a flood of queued
# web searches against a cooling engine can't spin the loop draining them.
_MAX_COOLING_DRAIN = 50


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class _Pacer:
    """Proactive floor on the interval between an engine's requests.

    ``wait()`` blocks until at least ``min_interval`` (plus a little jitter, so
    the cadence isn't perfectly regular) has elapsed since the previous request
    start, and is interruptible by the worker's stop event for prompt shutdown.
    """

    def __init__(
        self,
        min_interval: float,
        jitter_ratio: float,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._min_interval = min_interval
        self._jitter_ratio = max(0.0, jitter_ratio)
        self._clock = clock
        self._last: float | None = None

    def wait(self, stop_event: threading.Event) -> None:
        now = self._clock()
        if self._last is not None:
            interval = self._min_interval * (
                1.0 + random.uniform(0.0, self._jitter_ratio)
            )
            remaining = self._last + interval - now
            if remaining > 0:
                stop_event.wait(remaining)
        self._last = self._clock()


@dataclass
class _CycleWindow:
    """Rolling accumulator for one engine's metrics window.

    The event-driven scheduler has no sweep boundary, so instead of a cycle per
    sweep we flush one ``scraper_cycles`` row per engine every ``scrape_interval``
    (see ``_flush_window``). Entries and logs are buffered and flushed on the same
    cadence to keep DB writes to one batch per window rather than per topic.
    """

    started_at: datetime = field(default_factory=_naive_utc_now)
    topics_count: int = 0
    entries: list[NewsEntry] = field(default_factory=list)
    logs: list[ScraperLog] = field(default_factory=list)
    success: bool = True
    error: str | None = None

    def add(self, entries: list[NewsEntry], logs: list[ScraperLog]) -> None:
        self.topics_count += 1
        self.entries.extend(entries)
        self.logs.extend(logs)

    def empty(self) -> bool:
        return self.topics_count == 0


def _flush_window(
    window: _CycleWindow, engine: str, start_mono: float, clock: Callable[[], float]
) -> None:
    """Persist a window's buffered entries/logs and one cycle summary row, then
    leave the caller to start a fresh window. Best-effort; never raises."""
    if window.empty() and window.error is None:
        return
    new_events = 0
    try:
        new_events = db.insert_news_entries(window.entries)
        db.insert_scraper_logs(window.logs)
    except Exception:
        logger.exception("[%s] failed to persist window entries/logs", engine)
        window.success = False
    try:
        db.insert_cycle(
            started_at=window.started_at,
            finished_at=_naive_utc_now(),
            duration_seconds=clock() - start_mono,
            topics_count=window.topics_count,
            entries_parsed=len(window.entries),
            new_events=new_events,
            success=window.success,
            error=window.error,
            engine=engine,
        )
    except Exception:
        logger.exception("[%s] failed to record window cycle", engine)
    logger.info(
        f"[{engine}] window: scraped {window.topics_count} topics, "
        f"{len(window.entries)} entries, {new_events} new events"
    )


def _active_topic_names() -> set[str]:
    return {topic.name for topic in db.get_topics()}


def _serve_oneoff(
    context,
    source: SearchSource,
    query: str,
    vertical: SearchVertical,
    *,
    pacer: _Pacer,
    stop_event: threading.Event,
    cooldown: EngineCooldownTracker | None,
    shared_state: SharedEngineState,
    max_pages: int | None,
) -> tuple[list[NewsEntry], list[ScraperLog]]:
    """Pace, then run one ad-hoc query on the engine's live context for a
    non-tracked vertical: an on-demand WEB search, or a NEWS keep-alive warm-up.

    Shares the engine's pace floor and feeds any block signal into the same
    cooldown tracker as a normal scrape (these requests ride one budget), but
    deliberately does NOT touch the metrics window — they aren't tracked-topic
    content. Returns the parsed entries and the per-page logs.
    """
    pacer.wait(stop_event)
    if stop_event.is_set():
        return [], []
    page = context.new_page()
    try:
        entries, logs = scrape_pages(
            page, source, query, vertical=vertical, max_result_pages=max_pages
        )
    finally:
        page.close()
    if cooldown is not None:
        cooldown.record(source.name, logs)
        for snap in cooldown.snapshot():
            shared_state.update(snap)
    return entries, logs


def _execute_task(
    context,
    source: SearchSource,
    task: Task,
    *,
    pacer: _Pacer,
    stop_event: threading.Event,
    cooldown: EngineCooldownTracker | None,
    shared_state: SharedEngineState,
) -> tuple[list[NewsEntry], list[ScraperLog]]:
    """Run one task on the engine's live context and return its (entries, logs).

    A news task goes through ``scrape_topic`` (single source, strategy="all") so
    the tracked-topic path is byte-identical to before; a one-off web search or a
    keep-alive warm-up goes through ``_serve_oneoff``. Both ride the same pace
    floor and feed their outcome into the same cooldown tracker.
    """
    if isinstance(task, NewsScrapeTask):
        pacer.wait(stop_event)
        if stop_event.is_set():
            return [], []
        entries, logs = scrape_topic(
            context.new_page,
            [source],
            task.topic,
            strategy="all",
            max_result_pages=task.max_pages,
            cooldown=cooldown,
        )
        # scrape_topic recorded the outcome into the cooldown tracker already;
        # publish the refreshed snapshot for the saturation reader.
        if cooldown is not None:
            for snap in cooldown.snapshot():
                shared_state.update(snap)
        return entries, logs
    # One-off web search or keep-alive warm-up (_serve_oneoff paces + records).
    return _serve_oneoff(
        context,
        source,
        task.query,
        task.vertical,
        pacer=pacer,
        stop_event=stop_event,
        cooldown=cooldown,
        shared_state=shared_state,
        max_pages=task.max_pages,
    )


def run_engine_worker(
    source: SearchSource,
    profile: FingerprintProfile,
    proxy: dict | None,
    shared_state: SharedEngineState,
    stop_event: threading.Event,
    web_queue: "WebSearchQueue | DbWebSearchQueue | None" = None,
) -> None:
    """Run one engine's scheduler loop until ``stop_event`` is set.

    Owns its own Playwright instance and browser context (so the sync API stays
    single-threaded per worker) and its own cooldown tracker and topic schedule
    (single writer, so no locking needed around them).

    The one context services three kinds of work on one shared pace+cooldown
    budget, in priority order: an on-demand WEB search from ``web_queue``
    (user-facing, preempts news) → a due news topic → an idle NEWS keep-alive
    warm-up. Web search and keep-alive are off unless wired: pass a ``web_queue``
    to enable web search, and set ``scraper.keepalive.enabled`` to fire the
    heartbeat.
    """
    interval = scraper_config.scrape_interval
    jitter = scraper_config.pacing_jitter_ratio
    min_interval = scraper_config.min_interval_for(source.name)
    pacer = _Pacer(min_interval, jitter)
    cooldown = (
        EngineCooldownTracker(
            base_seconds=scraper_config.cooldown_base_seconds,
            max_seconds=scraper_config.cooldown_max_seconds,
        )
        if scraper_config.cooldown_enabled
        else None
    )
    heartbeat = (
        KeepAliveHeartbeat(
            interval=scraper_config.keepalive_interval_seconds,
            jitter_ratio=scraper_config.keepalive_jitter_ratio,
            queries=scraper_config.keepalive_queries or DEFAULT_QUERIES,
        )
        if scraper_config.keepalive_enabled
        else None
    )
    # Assemble the engine's single task queue from its sources: scheduled news (a
    # timer heap), the optional on-demand web search, and the optional idle
    # keep-alive warm-up.
    queue = EngineTaskQueue(
        NewsTaskGenerator(
            _TopicSchedule(interval, jitter), max_pages=scraper_config.max_pages
        ),
        # Cap a web search at the same page budget as a news scrape: one keyword
        # lookup shouldn't hammer the engine across many pages (an unbounded crawl
        # invites a block — exactly what the refactor must not regress).
        web=(
            WebSearchSource(web_queue, max_pages=scraper_config.max_pages)
            if web_queue is not None
            else None
        ),
        keepalive=KeepAliveGenerator(heartbeat) if heartbeat is not None else None,
    )
    logger.info(
        f"[{source.name}] worker starting (topic interval {interval}s, pace floor "
        f"{min_interval:.1f}s/request, cooldown {'on' if cooldown else 'off'}, "
        f"keep-alive {'on' if heartbeat else 'off'}, "
        f"web search {'on' if web_queue is not None else 'off'})"
    )

    try:
        queue.seed(_active_topic_names())
    except Exception:
        logger.exception(
            "[%s] initial topic load failed; relying on reconcile", source.name
        )

    with sync_playwright() as p:
        context = new_context(p, profile_dir_for(source.name), profile, proxy)
        window = _CycleWindow()
        window_start = time.monotonic()
        next_tick = time.monotonic() + interval
        scrapes_since_recycle = 0
        try:
            while not stop_event.is_set():
                now = time.monotonic()

                # Housekeeping tick: reconcile the topic set and flush the metrics
                # window. Runs regardless of cooldown so a benched engine still
                # publishes cycles and picks up topic changes.
                if now >= next_tick:
                    try:
                        queue.reconcile(_active_topic_names())
                    except Exception:
                        logger.exception("[%s] topic reconcile failed", source.name)
                    _flush_window(window, source.name, window_start, time.monotonic)
                    # Publish the queue's backlog health for the supervisor's
                    # falling-behind signal (a lagging complement to cooldown).
                    shared_state.update_health(source.name, queue.health())
                    window = _CycleWindow()
                    window_start = time.monotonic()
                    next_tick = time.monotonic() + interval

                # Engine-level cooldown gate. While benched: reject any queued
                # one-off fast (a user-facing request gets a prompt 'cooling'
                # answer instead of waiting out the bench), run no news (it stays
                # scheduled), and sleep until the backoff window allows a probe —
                # bounded by the web-poll slice so newly-arrived one-offs are
                # rejected promptly too.
                if cooldown is not None and cooldown.decide(source.name) == "skip":
                    rejected = queue.reject_pending_oneoffs(_MAX_COOLING_DRAIN)
                    if rejected:
                        logger.info(
                            "[%s] rejected %d queued web search(es) — cooling",
                            source.name,
                            rejected,
                        )
                    wait = min(
                        cooldown.remaining(source.name), next_tick - time.monotonic()
                    )
                    if queue.has_web:
                        wait = min(wait, WEB_POLL_INTERVAL)
                    stop_event.wait(max(wait, 0.0))
                    continue

                # Run the highest-priority ready task (one-off → due news → idle
                # keep-alive), or idle until the queue next has work. A cooldown
                # probe runs here too: whatever surfaces first while
                # decide()=="probe" is the single probe request.
                task, idle_wait = queue.pop_ready()
                if task is None:
                    waits = [next_tick - time.monotonic()]
                    if idle_wait is not None:
                        waits.append(idle_wait)
                    stop_event.wait(max(min(waits), 0.0))
                    continue

                # Execute it; the task delivers its own result — a one-off back to
                # its caller, a tracked news scrape into the metrics window below.
                # The worker doesn't branch on the kind beyond that.
                try:
                    entries, logs = _execute_task(
                        context,
                        source,
                        task,
                        pacer=pacer,
                        stop_event=stop_event,
                        cooldown=cooldown,
                        shared_state=shared_state,
                    )
                except Exception as e:
                    logger.exception(
                        "[%s] %s task '%s' failed",
                        source.name,
                        task.kind,
                        task.query,
                    )
                    task.deliver_failure(e)
                    if task.tracked:
                        window.error = f"{type(e).__name__}: {e}"
                        window.success = False
                else:
                    task.deliver_success(entries, logs)
                    if task.tracked:
                        window.add(entries, logs)
                queue.on_completed(task)
                scrapes_since_recycle += 1

                if stop_event.is_set():
                    break

                if scrapes_since_recycle >= scraper_config.browser_recycle_cycles:
                    logger.info(
                        f"[{source.name}] recycling context after "
                        f"{scrapes_since_recycle} scrapes to release memory"
                    )
                    context.close()
                    context = new_context(
                        p, profile_dir_for(source.name), profile, proxy
                    )
                    scrapes_since_recycle = 0
        finally:
            # Don't lose the in-flight window's entries/metrics on shutdown.
            _flush_window(window, source.name, window_start, time.monotonic)
            context.close()
            logger.info(f"[{source.name}] worker stopped")
