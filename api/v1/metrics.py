"""Operational metrics for monitoring the scraper and feed."""

from fastapi import APIRouter
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from common import database as db

router = APIRouter(prefix="/metrics", tags=["metrics"])

_LOG_WINDOW = 50


class MetricsResponse(BaseModel):
    active_topics: int = Field(..., description="Number of watched (active) topics")
    total_news: int = Field(..., description="Total feed events across active topics")
    scrape_success_rate: float | None = Field(
        None, description="Fraction of recent scrapes that succeeded (null if none)"
    )
    feed_freshness_seconds: float | None = Field(
        None, description="Age of the newest feed event in seconds (null if empty)"
    )


@router.get("")
async def get_metrics() -> MetricsResponse:
    topics = await run_in_threadpool(db.get_topics)
    logs = await run_in_threadpool(db.get_scraper_logs, _LOG_WINDOW)
    total_news = await run_in_threadpool(db.get_active_feed_count)
    freshness = await run_in_threadpool(db.get_feed_freshness_seconds)

    success_rate = (
        round(sum(1 for log in logs if log.success) / len(logs), 4) if logs else None
    )
    return MetricsResponse(
        active_topics=len(topics),
        total_news=total_news,
        scrape_success_rate=success_rate,
        feed_freshness_seconds=freshness,
    )
