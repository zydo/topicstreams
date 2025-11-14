"""Scraper logs API endpoints."""

from typing import List

from fastapi import APIRouter, Query

from common import database as db
from common.model import ScraperLog

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("")
async def get_logs(limit: int = Query(20, ge=1, le=100)) -> List[ScraperLog]:
    """Get recent scraper log entries.

    Args:
        limit: Maximum number of log entries to return (1-100, default: 20).

    Returns:
        List of scraper logs ordered by scraped_at DESC (newest first).
    """
    return db.get_scraper_logs(limit)
