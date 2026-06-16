"""Scraper logs API endpoints."""


from fastapi import APIRouter, Query
from starlette.concurrency import run_in_threadpool

from common import database as db
from common.model import ScraperLog

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("")
async def get_logs(limit: int = Query(20, ge=1, le=100)) -> list[ScraperLog]:
    """Get recent scraper log entries.

    Args:
        limit: Maximum number of log entries to return (1-100, default: 20).

    Returns:
        List of scraper logs ordered by scraped_at DESC (newest first).
    """
    return await run_in_threadpool(db.get_scraper_logs, limit)
