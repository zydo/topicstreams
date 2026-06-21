"""One engine = one worker.

Each worker owns a Playwright instance and a persistent browser context for a
single search engine, and runs an event-driven scheduler: it keeps a per-engine
min-heap of topics keyed by each topic's *next eligible scrape time*, always
scrapes the topic whose time has come, then re-arms it for ``scrape_interval``
later. When nothing is due it sleeps until the heap's head comes due. Workers
don't know about each other — the only shared state is the DB (cross-engine
duplicates resolve there, via the URL-derived news id) and a ``SharedEngineState``
the main thread reads for the saturation signal.

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
from .tasks import _TopicSchedule
from .webqueue import DbWebSearchQueue, WebSearchQueue

logger = logging.getLogger(__name__)

# When a web-search queue is attached, cap idle waits to this slice so a freshly
# submitted (user-facing) web search is picked up promptly rather than slept
# through. Each idle turn does one cheap ``poll()`` — a ``get_nowait`` in-process,
# or one indexed claim query against ``web_search_jobs`` for the DB-backed
# cross-process queue. A LISTEN/NOTIFY wakeup could replace this poll later to
# trim claim latency, but ~1 Hz of indexed claims per engine is negligible.
_WEB_POLL_SLICE = 1.0


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
    schedule = _TopicSchedule(interval, jitter)
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
    logger.info(
        f"[{source.name}] worker starting (topic interval {interval}s, pace floor "
        f"{min_interval:.1f}s/request, cooldown {'on' if cooldown else 'off'}, "
        f"keep-alive {'on' if heartbeat else 'off'}, "
        f"web search {'on' if web_queue is not None else 'off'})"
    )

    try:
        schedule.seed(_active_topic_names())
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
                        schedule.reconcile(_active_topic_names())
                    except Exception:
                        logger.exception("[%s] topic reconcile failed", source.name)
                    _flush_window(window, source.name, window_start, time.monotonic)
                    window = _CycleWindow()
                    window_start = time.monotonic()
                    next_tick = time.monotonic() + interval

                # Engine-level cooldown gate: while benched, schedule nothing —
                # sleep until the backoff window allows a probe (or the next
                # tick). This also defers web/keep-alive: a benched session would
                # only CAPTCHA, and (once wired) the dispatcher routes the web
                # request to a healthy engine instead.
                if cooldown is not None and cooldown.decide(source.name) == "skip":
                    wait = min(
                        cooldown.remaining(source.name), next_tick - time.monotonic()
                    )
                    stop_event.wait(max(wait, 0.0))
                    continue

                # Pick exactly one action this turn, in priority order, then
                # converge on the shared post-scrape (recycle) handling. The
                # idle branch is the only one that sleeps and skips it.
                job = web_queue.poll() if web_queue is not None else None
                topic, sleep_for = (None, None)
                if job is None:
                    topic, sleep_for = schedule.next_due()

                if job is not None:
                    # Priority 1 — on-demand web search preempts news. Serve one
                    # queued job per turn (FCFS) and hand its results back. The job
                    # owns delivery (set a future, or write the cross-process row);
                    # it's passed the per-page logs so the producer can tell a block
                    # from a genuinely empty result and fall back accordingly.
                    try:
                        entries, logs = _serve_oneoff(
                            context,
                            source,
                            job.query,
                            SearchVertical.WEB,
                            pacer=pacer,
                            stop_event=stop_event,
                            cooldown=cooldown,
                            shared_state=shared_state,
                            max_pages=scraper_config.max_pages,
                        )
                    except Exception as e:
                        logger.exception(
                            "[%s] web search '%s' failed", source.name, job.query
                        )
                        job.fail(e)
                    else:
                        job.resolve(entries, logs)
                    if heartbeat is not None:
                        heartbeat.record_activity()
                    scrapes_since_recycle += 1

                elif topic is not None:
                    # Priority 2 — a due news topic.
                    pacer.wait(stop_event)
                    if stop_event.is_set():
                        break
                    try:
                        entries, logs = scrape_topic(
                            context.new_page,
                            [source],
                            topic,
                            strategy="all",
                            max_result_pages=scraper_config.max_pages,
                            cooldown=cooldown,
                        )
                        window.add(entries, logs)
                    except Exception as e:
                        window.error = f"{type(e).__name__}: {e}"
                        window.success = False
                        logger.exception(
                            "[%s] scrape of '%s' failed", source.name, topic
                        )
                    finally:
                        schedule.reschedule(topic)
                    if cooldown is not None:
                        for snap in cooldown.snapshot():
                            shared_state.update(snap)
                    if heartbeat is not None:
                        heartbeat.record_activity()
                    scrapes_since_recycle += 1

                elif heartbeat is not None and heartbeat.due():
                    # Priority 3 — nothing due; fire an idle keep-alive warm-up.
                    query = heartbeat.next_query()
                    logger.info(f"[{source.name}] keep-alive warm-up: '{query}'")
                    _serve_oneoff(
                        context,
                        source,
                        query,
                        SearchVertical.NEWS,
                        pacer=pacer,
                        stop_event=stop_event,
                        cooldown=cooldown,
                        shared_state=shared_state,
                        max_pages=1,  # one page is enough to warm the session
                    )
                    heartbeat.record_activity()
                    scrapes_since_recycle += 1

                else:
                    # Idle: wait for the head topic to come due, but never past
                    # the next housekeeping tick, the next keep-alive, or (with
                    # web search enabled) a short web-poll slice.
                    waits = [next_tick - time.monotonic()]
                    if sleep_for is not None:
                        waits.append(sleep_for)
                    if heartbeat is not None:
                        waits.append(heartbeat.seconds_until_due())
                    if web_queue is not None:
                        waits.append(_WEB_POLL_SLICE)
                    stop_event.wait(max(min(waits), 0.0))
                    continue

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
