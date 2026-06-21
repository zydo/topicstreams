"""Entry point for the TopicStreams news scraper.

Each configured search engine runs in its own worker thread (see
``scraper/worker.py``): its own Playwright instance, its own persistent browser
context/identity, scheduling each topic on a per-topic interval via a min-heap
and scraping due topics at its own proactively-paced rate. The
workers share only the database (cross-engine duplicates resolve there via the
URL-derived news id) and a small in-memory snapshot the supervisor reads.

This module is the supervisor: it resolves the shared fingerprint/proxy once,
launches one worker per engine, and then loops doing the cross-engine
housekeeping that must happen exactly once — purging old rows, publishing the
per-engine cooldown snapshot for the monitor, and evaluating the exit-IP
saturation signal (``scraper/saturation.py``).

Anti-detection strategy:
    - One persistent browser context *per engine* (cookies/cache survive
      container restarts), each a single consistent identity on the shared IP.
    - Proactive per-engine pacing as the primary throttle; the adaptive cooldown
      is the reactive backstop.

Scraping strategy:
    - Scrapes a configurable number of result pages (max_pages, default 1).
      Since results are sorted by recency and filtered to the past hour, new
      articles always appear on the first few pages.

IMPORTANT assumption:
    Each engine re-scrapes a given topic about once per scrape_interval. If more
    new articles are published for a topic between two of an engine's scrapes than
    fit on the configured pages, older articles will be missed for that engine.
"""

import atexit
import logging
import threading

from playwright.sync_api import sync_playwright

from common import database as db
from common.config import scraper_config
from common.logging_config import configure_logging
from common.settings import settings

from .browser import build_proxy, detect_fingerprint
from .saturation import (
    SharedEngineState,
    evaluate_saturation,
    log_saturation,
)
from .sources import get_source
from .webqueue import DbWebSearchQueue
from .worker import run_engine_worker

atexit.register(db.close_pool)

configure_logging(settings.log_format)
logger = logging.getLogger(__name__)

# How long to wait for workers to wind down on shutdown before giving up.
_WORKER_JOIN_TIMEOUT = 30.0


def _purge_old() -> None:
    """Drop rows past the retention window. Shared across engines, so the
    supervisor runs it once rather than each worker."""
    deleted = db.purge_old_news_entries(settings.news_retention_days)
    if deleted:
        logger.info(
            f"Purged {deleted} news entries older than "
            f"{settings.news_retention_days} days"
        )
    deleted_logs = db.purge_old_scraper_logs(settings.news_retention_days)
    if deleted_logs:
        logger.info(
            f"Purged {deleted_logs} scraper logs older than "
            f"{settings.news_retention_days} days"
        )
    deleted_cycles = db.purge_old_cycles(settings.news_retention_days)
    if deleted_cycles:
        logger.info(
            f"Purged {deleted_cycles} cycle rows older than "
            f"{settings.news_retention_days} days"
        )
    # Sweep web-search jobs abandoned by a producer that timed out/crashed before
    # deleting its row (independent of the news retention window — these are an
    # ephemeral handoff, purged on a much shorter TTL).
    if scraper_config.web_search_enabled:
        deleted_jobs = db.purge_stale_web_search_jobs(
            scraper_config.web_search_job_ttl_seconds
        )
        if deleted_jobs:
            logger.info(f"Purged {deleted_jobs} stale web-search jobs")


def _supervise(shared_state: SharedEngineState, stop_event: threading.Event) -> None:
    """Cross-engine housekeeping + the saturation signal, on a fixed cadence."""
    canary = scraper_config.saturation_canary_engines
    threshold = scraper_config.saturation_robust_threshold

    while not stop_event.is_set():
        try:
            _purge_old()
        except Exception:
            logger.exception("Housekeeping purge failed")

        snapshots = shared_state.all()
        if snapshots:
            # Publish the per-engine cooldown snapshot so the API/monitor can
            # show which engines are benched. Best-effort.
            try:
                db.upsert_engine_cooldowns(
                    [(s.engine, s.failures, s.remaining_seconds) for s in snapshots]
                )
            except Exception:
                logger.exception("Failed to publish engine cooldown state")

            # Weighted saturation: ignore canary engines (they trip first), flag
            # the IP only when enough robust engines are cooling at once.
            verdict = evaluate_saturation(
                snapshots, canary_engines=canary, robust_threshold=threshold
            )
            log_saturation(verdict)

        stop_event.wait(scraper_config.scrape_interval)


def main():
    # Evolve the schema for an existing volume (adds scraper_cycles.engine, etc).
    # Idempotent; shared with the API process.
    db.ensure_schema()

    # Resolve configured engines once (fails fast on an unknown name).
    sources = [get_source(name) for name in scraper_config.engines]
    logger.info(f"Starting per-engine workers for {[s.name for s in sources]}")

    # Detect the fingerprint once (a throwaway browser reads the real Chromium
    # version) and resolve the proxy once, so every worker presents the same
    # real browser version from the same exit IP.
    with sync_playwright() as p:
        profile = detect_fingerprint(p)
    proxy = build_proxy()
    if proxy:
        # Never log credentials, only the server endpoint.
        logger.info(f"Routing browser traffic through proxy: {proxy['server']}")

    shared_state = SharedEngineState()
    stop_event = threading.Event()

    # On-demand web search: give each worker its own cross-process queue so the
    # API can dispatch a query to a specific (healthy) engine. Off unless enabled.
    web_search_on = scraper_config.web_search_enabled
    logger.info(f"On-demand web search: {'on' if web_search_on else 'off'}")

    workers: list[threading.Thread] = []
    for source in sources:
        web_queue = DbWebSearchQueue(source.name) if web_search_on else None
        thread = threading.Thread(
            target=run_engine_worker,
            args=(source, profile, proxy, shared_state, stop_event, web_queue),
            name=f"worker-{source.name}",
            daemon=True,
        )
        thread.start()
        workers.append(thread)

    try:
        _supervise(shared_state, stop_event)
    except KeyboardInterrupt:
        logger.info("Scraper interrupted by user")
    finally:
        stop_event.set()
        for thread in workers:
            thread.join(timeout=_WORKER_JOIN_TIMEOUT)


if __name__ == "__main__":
    main()
