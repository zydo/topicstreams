"""One engine = one worker.

Each worker owns a Playwright instance and a persistent browser context for a
single search engine, and loops: sweep the topic list, sleep, repeat. Workers
don't know about each other — the only shared state is the DB (cross-engine
duplicates resolve there, via the URL-derived news id) and a ``SharedEngineState``
the main thread reads for the saturation signal.

Pacing is proactive: a per-engine floor on the interval between requests keeps
each engine at a known-safe rate rather than hammering until a 429. The cooldown
tracker (scraper/cooldown.py) remains the *reactive* backstop — if the pace is
still too fast and a block lands, the engine benches itself. When an engine is
benched its topics are skipped without spending the pacing budget, and the
sweep-period fill (below) keeps the worker from busy-spinning.

Throughput model: each worker targets one full sweep per ``scrape_interval``.
With few topics the sweep finishes early and the worker waits out the remainder.
With many topics the per-request floor dominates, the sweep runs longer than the
interval, and the engine simply falls behind at its safe rate (best effort) —
which, if the robust engines start blocking, is the cue the exit IP is saturated.
"""

import logging
import random
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from playwright.sync_api import BrowserContext, sync_playwright

from common import database as db
from common.config import (
    FingerprintProfile,
    anti_detection_config,
    scraper_config,
)

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


def run_engine_worker(
    source: SearchSource,
    profile: FingerprintProfile,
    proxy: dict | None,
    shared_state: SharedEngineState,
    stop_event: threading.Event,
) -> None:
    """Run one engine's sweep-loop until ``stop_event`` is set.

    Owns its own Playwright instance and browser context (so the sync API stays
    single-threaded per worker) and its own cooldown tracker (single writer, so
    no locking needed around it).
    """
    min_interval = scraper_config.min_interval_for(source.name)
    pacer = _Pacer(min_interval, scraper_config.pacing_jitter_ratio)
    cooldown = (
        EngineCooldownTracker(
            base_seconds=scraper_config.cooldown_base_seconds,
            max_seconds=scraper_config.cooldown_max_seconds,
        )
        if scraper_config.cooldown_enabled
        else None
    )
    logger.info(
        f"[{source.name}] worker starting (pace floor {min_interval:.1f}s/request, "
        f"cooldown {'on' if cooldown else 'off'})"
    )

    with sync_playwright() as p:
        context = new_context(p, profile_dir_for(source.name), profile, proxy)
        sweep_count = 0
        try:
            while not stop_event.is_set():
                _run_sweep(context, source, cooldown, shared_state, stop_event, pacer)
                sweep_count += 1
                if sweep_count % scraper_config.browser_recycle_cycles == 0:
                    logger.info(
                        f"[{source.name}] recycling context after {sweep_count} "
                        "sweeps to release memory"
                    )
                    context.close()
                    context = new_context(
                        p, profile_dir_for(source.name), profile, proxy
                    )
        finally:
            context.close()
            logger.info(f"[{source.name}] worker stopped")


def _run_sweep(
    context: BrowserContext,
    source: SearchSource,
    cooldown: EngineCooldownTracker | None,
    shared_state: SharedEngineState,
    stop_event: threading.Event,
    pacer: _Pacer,
) -> None:
    """One paced pass over the topic list for a single engine."""
    started_at = _naive_utc_now()
    sweep_start = time.monotonic()
    success = False
    error: str | None = None
    topics: list[str] = []
    all_entries: list = []
    new_events = 0

    try:
        topics = [topic.name for topic in db.get_topics()]
        # Shuffle each sweep so a benched/slow engine doesn't always cover the
        # head of the list — coverage averages out across sweeps.
        if anti_detection_config.randomized_order_enabled:
            random.shuffle(topics)

        all_logs: list = []
        for topic in topics:
            if stop_event.is_set():
                break
            # When cooling, the topic is skipped without a request — don't spend
            # the pacing budget on it (that's what keeps a benched worker from
            # blocking the next probe and from busy-spinning; the sweep-period
            # fill below absorbs the freed time).
            if cooldown is not None and cooldown.decide(source.name) == "skip":
                continue
            pacer.wait(stop_event)
            entries, logs = scrape_topic(
                context.new_page,
                [source],
                topic,
                strategy="all",
                max_result_pages=scraper_config.max_pages,
                cooldown=cooldown,
            )
            all_entries.extend(entries)
            all_logs.extend(logs)
            if cooldown is not None:
                for snap in cooldown.snapshot():
                    shared_state.update(snap)

        new_events = db.insert_news_entries(all_entries)
        db.insert_scraper_logs(all_logs)
        success = True
        logger.info(
            f"[{source.name}] swept {len(topics)} topics, "
            f"{len(all_entries)} entries, {new_events} new events"
        )
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        logger.exception("[%s] sweep failed", source.name)

    elapsed = time.monotonic() - sweep_start
    try:
        db.insert_cycle(
            started_at=started_at,
            finished_at=_naive_utc_now(),
            duration_seconds=elapsed,
            topics_count=len(topics),
            entries_parsed=len(all_entries),
            new_events=new_events,
            success=success,
            error=error,
            engine=source.name,
        )
    except Exception:
        logger.exception("[%s] failed to record sweep cycle", source.name)

    # Sweep-period fill: aim for one sweep per scrape_interval. If the sweep ran
    # longer (many topics at the pace floor, or a long cooldown), don't wait.
    remaining = scraper_config.scrape_interval - elapsed
    if remaining > 0 and not stop_event.is_set():
        stop_event.wait(remaining)
