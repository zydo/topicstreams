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

router = APIRouter(prefix="/status", tags=["status"])

_STALE_MIN_S = 5 * 60
_STALE_MAX_S = 30 * 60
_LOG_WINDOW = 30


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
        return 15 * 60
    return min(_STALE_MAX_S, max(_STALE_MIN_S, max_gap * 3))


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

    # Latest scrape outcome per active topic.
    latest: dict[str, ScraperLog] = {}
    for log in logs:  # newest first
        if active_names and log.topic not in active_names:
            continue
        latest.setdefault(log.topic, log)
    tracked = list(latest.values())
    if not tracked:
        return "live", "live", "Scraping cleanly"

    failed = [log for log in tracked if not log.success]
    if len(failed) == len(tracked):
        return (
            "errors",
            "errors",
            f"All {len(tracked)} feeds failing — {_fail_reason(failed[0])}",
        )

    # Selector rot: scrapes succeed but parse nothing across the recent window.
    # Requiring *every* recent success to parse 0 makes a quiet hour (one topic
    # with no news) safe, while a real markup change (all topics, sustained)
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
            "Scrapes return 200 but parse 0 items — Google markup may have changed",
        )

    if failed:
        names = ", ".join(log.topic for log in failed)
        return (
            "degraded",
            "degraded",
            f"{len(failed)} of {len(tracked)} feeds failing: {names}",
        )

    return "live", "live", f"All {len(tracked)} feeds scraping cleanly"


@router.get("")
async def get_status() -> StatusResponse:
    topics = await run_in_threadpool(db.get_topics)  # active only
    active_names = {t.name for t in topics}
    logs = await run_in_threadpool(db.get_scraper_logs, _LOG_WINDOW)
    total_news = await run_in_threadpool(db.get_active_feed_count)

    state, label, detail = compute_health(logs, active_names)
    return StatusResponse(
        state=state,
        label=label,
        detail=detail,
        active_topics=len(active_names),
        total_news=total_news,
    )
