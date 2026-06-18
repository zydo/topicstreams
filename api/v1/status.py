"""Server-side scrape-health for the dashboard masthead.

Derives a single health signal from recent scraper logs so the UI doesn't have
to re-compute it. Crucially, it flags *selector rot* — when scrapes return HTTP
200 but parse 0 items across the board (Google changed its markup), which a
plain success/recency check would miss and the feed would silently go quiet.
"""

from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from common import database as db
from common.model import ScraperLog
from common.settings import settings

router = APIRouter(prefix="/status", tags=["status"])


class StatusResponse(BaseModel):
    state: str = Field(
        ..., description="live | degraded | errors | parsing | stalled | idle"
    )
    label: str = Field(..., description="Short label for the masthead")
    detail: str = Field(..., description="Human-readable explanation (tooltip)")
    active_topics: int = Field(..., description="Number of watched (active) topics")
    total_news: int = Field(..., description="Total feed events across active topics")


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _stale_threshold_s(logs: list[ScraperLog]) -> float:
    """Adapt to the observed cadence: ~3x the largest recent inter-scrape gap,
    clamped, so a long scrape interval doesn't false-trip 'stalled'."""
    max_gap = 0.0
    for newer, older in zip(logs, logs[1:]):
        gap = (newer.scraped_at - older.scraped_at).total_seconds()
        max_gap = max(max_gap, gap)
    if max_gap <= 0:
        return settings.health_stale_default_seconds
    return min(
        settings.health_stale_max_seconds,
        max(settings.health_stale_min_seconds, max_gap * 3),
    )


def _fail_reason(log: ScraperLog) -> str:
    return log.error_message or (
        f"HTTP {log.http_status_code}" if log.http_status_code else "scrape failed"
    )


def compute_health(
    logs: list[ScraperLog], active_names: set[str]
) -> tuple[str, str, str]:
    """Return (state, label, detail) from recent logs for active topics."""
    if not logs:
        return "idle", "idle", "No scrapes recorded yet"

    age = (_naive_utc_now() - logs[0].scraped_at).total_seconds()
    if age > _stale_threshold_s(logs):
        return "stalled", "stalled", f"No scrape in ~{round(age / 60)} min"

    # Latest scrape outcome per (topic, engine). A topic is "served" if any
    # engine's latest scrape succeeded, so one engine breaking doesn't blank a
    # feed another engine still covers.
    latest: dict[tuple[str, str], ScraperLog] = {}
    for log in logs:  # newest first
        if active_names and log.topic not in active_names:
            continue
        latest.setdefault((log.topic, log.engine), log)
    if not latest:
        return "live", "live", "Scraping cleanly"

    served: dict[str, bool] = {}
    a_failure: ScraperLog | None = None
    for (topic, _engine), log in latest.items():
        served[topic] = served.get(topic, False) or log.success
        if not log.success and a_failure is None:
            a_failure = log
    failed_topics = [topic for topic, ok in served.items() if not ok]

    if failed_topics and len(failed_topics) == len(served):
        reason = _fail_reason(a_failure) if a_failure else "scrape failed"
        return "errors", "errors", f"All {len(served)} feeds failing — {reason}"

    # Selector rot: successful scrapes parse nothing across the recent window
    # (all engines). If any engine is producing entries, the feed flows.
    # Requiring *every* recent success to parse 0 makes a quiet hour (one topic
    # with no news) safe, while a real markup change (sustained, everywhere)
    # trips it.
    successes = [
        log
        for log in logs
        if log.success and (not active_names or log.topic in active_names)
    ]
    if successes and sum(log.entry_count for log in successes) == 0:
        return (
            "parsing",
            "no items",
            "Scrapes return 200 but parse 0 items — search markup may have changed",
        )

    if failed_topics:
        names = ", ".join(sorted(failed_topics))
        return (
            "degraded",
            "degraded",
            f"{len(failed_topics)} of {len(served)} feeds failing: {names}",
        )

    return "live", "live", f"All {len(served)} feeds scraping cleanly"


@router.get("")
async def get_status() -> StatusResponse:
    topics = await run_in_threadpool(db.get_topics)  # active only
    active_names = {t.name for t in topics}
    logs = await run_in_threadpool(db.get_scraper_logs, settings.health_log_window)
    total_news = await run_in_threadpool(db.get_active_feed_count)

    state, label, detail = compute_health(logs, active_names)
    return StatusResponse(
        state=state,
        label=label,
        detail=detail,
        active_topics=len(active_names),
        total_news=total_news,
    )
