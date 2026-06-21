"""On-demand web-search dispatcher — the API (producer) side of the channel.

The counterpart to ``scraper/webqueue.py``: turns one user query into parsed WEB
results by routing it across the cross-process ``web_search_jobs`` bridge.

For one query it:
  1. picks the healthy (non-cooling) engines from ``scraper.web_search.engines``
     (priority order), using the cooldown snapshot the scraper publishes
     (``engine_cooldowns``);
  2. enqueues a job for the first such engine and polls the row for the result
     its worker writes back;
  3. on a block, empty result, error, or timeout, falls back to the next healthy
     engine — so a search still succeeds while one engine is benched.

``web_search.engines`` defaults to **Google only**, so today this is effectively a
single-engine search; the fan-out/fallback machinery is already here for when
more engines are added to that list.

Stateless and replica-safe: any API process can dispatch, and the job is served
by whichever scraper owns that engine's worker. Job rows are deleted once read
(or on timeout), keeping the bridge an ephemeral handoff rather than a store.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from starlette.concurrency import run_in_threadpool

from common import database as db
from common.config import scraper_config
from common.model import WebResult

logger = logging.getLogger(__name__)


@dataclass
class WebSearchResult:
    """Outcome of dispatching one query across the healthy engines.

    ``status`` is ``ok`` (``results`` populated by ``engine``), ``empty`` (a
    healthy engine returned no results), ``blocked``/``error`` (every attempted
    engine failed), ``timeout`` (no engine answered in time), or ``unavailable``
    (no healthy engine to try). ``attempts`` lists the engines tried, in order.
    """

    query: str
    status: str
    engine: str | None = None
    results: list[WebResult] = field(default_factory=list)
    attempts: list[str] = field(default_factory=list)


def _naive_utc_now() -> datetime:
    # engine_cooldowns.next_probe_at is stored as naive UTC (the DB clock), so
    # compare against a naive-UTC now.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _healthy_engines(configured: list[str], cooldowns: dict[str, dict]) -> list[str]:
    """Configured engines (priority order) that aren't currently benched.

    An engine is benched while its cooldown snapshot has a ``next_probe_at`` in
    the future. An engine with no snapshot yet (never blocked, or a fresh volume)
    is treated as healthy. Dispatching to a benched engine would only CAPTCHA, so
    they're skipped up front rather than discovered by a failed serve.
    """
    now = _naive_utc_now()
    healthy: list[str] = []
    for engine in configured:
        snap = cooldowns.get(engine)
        if snap is None:
            healthy.append(engine)
            continue
        probe = snap.get("next_probe_at")
        if probe is None or probe <= now:
            healthy.append(engine)
    return healthy


async def _await_result(job_id: int, timeout: float, poll: float) -> dict | None:
    """Poll a job row until it is ``done`` or the timeout elapses. Returns the
    terminal row, or None on timeout."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        row = await run_in_threadpool(db.fetch_web_search_result, job_id)
        if row and row["status"] == "done":
            return row
        if asyncio.get_running_loop().time() >= deadline:
            return None
        await asyncio.sleep(poll)


async def dispatch_web_search(query: str) -> WebSearchResult:
    """Dispatch one query to a healthy engine, falling back on failure/timeout."""
    query = query.strip()
    if not query:
        return WebSearchResult(query=query, status="empty")

    cooldowns = await run_in_threadpool(db.get_engine_cooldowns)
    healthy = _healthy_engines(scraper_config.web_search_engines, cooldowns)
    attempts: list[str] = []
    if not healthy:
        logger.warning("web search '%s': no healthy engine available", query)
        return WebSearchResult(query=query, status="unavailable")

    timeout = scraper_config.web_search_request_timeout_seconds
    poll = scraper_config.web_search_poll_interval_seconds
    max_attempts = scraper_config.web_search_max_engine_attempts

    last_status = "unavailable"
    for engine in healthy[:max_attempts]:
        attempts.append(engine)
        job_id = await run_in_threadpool(db.enqueue_web_search, query, engine)
        try:
            row = await _await_result(job_id, timeout, poll)
        finally:
            # The handoff is ephemeral: drop the row whether it resolved, errored,
            # or timed out. A late completion against a deleted row is a harmless
            # no-op; abandoned rows are also swept by purge_stale_web_search_jobs.
            await run_in_threadpool(db.delete_web_search_job, job_id)

        if row is None:
            last_status = "timeout"
            logger.warning("web search '%s' on %s timed out", query, engine)
            continue

        outcome = row["outcome"] or "error"
        if outcome == "ok":
            results = [WebResult(**r) for r in (row["results"] or [])]
            return WebSearchResult(
                query=query,
                status="ok",
                engine=engine,
                results=results,
                attempts=attempts,
            )
        last_status = outcome
        logger.info(
            "web search '%s' on %s -> %s; trying next engine", query, engine, outcome
        )

    return WebSearchResult(query=query, status=last_status, attempts=attempts)
