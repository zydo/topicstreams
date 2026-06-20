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

import heapq
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
from .saturation import SharedEngineState
from .scraper import scrape_topic
from .sources import SearchSource

logger = logging.getLogger(__name__)


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


class _TopicSchedule:
    """Per-engine min-heap of ``(next_eligible_monotonic, topic)`` with lazy
    deletion of deactivated topics.

    The clock is monotonic and injectable for tests. ``_scheduled`` tracks which
    topics currently have a live heap entry so reconciliation never double-pushes
    a topic; ``_active`` is the authoritative set of topics that should be scraped
    (a topic dropped from it is removed from the heap the next time it surfaces).
    """

    def __init__(
        self,
        interval: float,
        jitter_ratio: float,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ):
        self._interval = interval
        self._jitter_ratio = max(0.0, jitter_ratio)
        self._clock = clock
        self._rng = rng or random
        self._heap: list[tuple[float, str]] = []
        self._scheduled: set[str] = set()
        self._active: set[str] = set()

    def _push(self, topic: str, when: float) -> None:
        heapq.heappush(self._heap, (when, topic))
        self._scheduled.add(topic)

    def seed(self, topics: set[str]) -> None:
        """Initial population: stagger every topic across one interval so a fresh
        worker doesn't fire every topic at boot (thundering herd on the engine)."""
        self._active = set(topics)
        now = self._clock()
        for topic in topics:
            self._push(topic, now + self._rng.uniform(0.0, self._interval))

    def reconcile(self, active: set[str]) -> None:
        """Sync to the latest active set: schedule newly-added topics (spread
        across an interval) and drop vanished ones lazily on pop."""
        self._active = set(active)
        now = self._clock()
        for topic in self._active:
            if topic not in self._scheduled:
                self._push(topic, now + self._rng.uniform(0.0, self._interval))

    def reschedule(self, topic: str) -> None:
        """Re-arm a just-scraped topic for ``interval`` (plus jitter) from now."""
        when = self._clock() + self._interval * (
            1.0 + self._rng.uniform(0.0, self._jitter_ratio)
        )
        self._push(topic, when)

    def next_due(self) -> tuple[str | None, float | None]:
        """What the worker should do next.

        Returns ``(topic, 0.0)`` for a topic to scrape now, ``(None, seconds)`` to
        wait that long for the head to come due, or ``(None, None)`` when the heap
        is empty (no active topics). Deactivated topics are dropped here.
        """
        while self._heap:
            when, topic = self._heap[0]
            if topic not in self._active:
                heapq.heappop(self._heap)
                self._scheduled.discard(topic)
                continue
            if when > self._clock():
                return None, when - self._clock()
            heapq.heappop(self._heap)
            self._scheduled.discard(topic)
            return topic, 0.0
        return None, None


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


def run_engine_worker(
    source: SearchSource,
    profile: FingerprintProfile,
    proxy: dict | None,
    shared_state: SharedEngineState,
    stop_event: threading.Event,
) -> None:
    """Run one engine's scheduler loop until ``stop_event`` is set.

    Owns its own Playwright instance and browser context (so the sync API stays
    single-threaded per worker) and its own cooldown tracker and topic schedule
    (single writer, so no locking needed around them).
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
    logger.info(
        f"[{source.name}] worker starting (topic interval {interval}s, pace floor "
        f"{min_interval:.1f}s/request, cooldown {'on' if cooldown else 'off'})"
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
                # sleep until the backoff window allows a probe (or the next tick).
                if cooldown is not None and cooldown.decide(source.name) == "skip":
                    wait = min(
                        cooldown.remaining(source.name), next_tick - time.monotonic()
                    )
                    stop_event.wait(max(wait, 0.0))
                    continue

                topic, sleep_for = schedule.next_due()
                if topic is None:
                    # Nothing due: wait for the head to come due, but never past
                    # the next housekeeping tick so flush/reconcile stay on time.
                    bound = next_tick - time.monotonic()
                    wait = bound if sleep_for is None else min(sleep_for, bound)
                    stop_event.wait(max(wait, 0.0))
                    continue

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
                    logger.exception("[%s] scrape of '%s' failed", source.name, topic)
                finally:
                    schedule.reschedule(topic)

                if cooldown is not None:
                    for snap in cooldown.snapshot():
                        shared_state.update(snap)

                scrapes_since_recycle += 1
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
