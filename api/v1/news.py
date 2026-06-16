"""News retrieval endpoints for API v1."""

from typing import List, Optional

from fastapi import APIRouter, Query, Path
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from api.exceptions import TopicStreamsException
from common import database as db
from common.model import NewsEntry
from common.utils import normalize_topic

router = APIRouter(prefix="/news", tags=["news"])


class NewsListResponse(BaseModel):
    entries: List[NewsEntry] = Field(..., description="News entries, newest first")
    limit: int = Field(..., description="Number of entries requested per page")
    next_before_id: Optional[int] = Field(
        None,
        description="Cursor for the next (older) page; null when none remain",
    )
    topic: Optional[str] = Field(
        None, description="Normalized topic name, or null for the all-topics feed"
    )
    total: Optional[int] = Field(
        None, description="Total entries for this topic (single-topic feed only)"
    )


def _next_cursor(entries: List[NewsEntry], limit: int) -> Optional[int]:
    # A short page means we hit the earliest entry — no older page follows.
    if len(entries) < limit:
        return None
    return entries[-1].id


@router.get("")
async def list_all_news(
    limit: int = Query(20, ge=1, le=100),
    before_id: Optional[int] = Query(None, ge=1),
) -> NewsListResponse:
    entries = await run_in_threadpool(db.get_news_entries_all, limit, before_id)
    return NewsListResponse(
        entries=entries,
        limit=limit,
        next_before_id=_next_cursor(entries, limit),
    )


@router.get("/{topic_name}")
async def get_news(
    topic_name: str = Path(..., min_length=1, max_length=100),
    limit: int = Query(20, ge=1, le=100),
    before_id: Optional[int] = Query(None, ge=1),
) -> NewsListResponse:
    normalized_name = normalize_topic(topic_name)

    if not await run_in_threadpool(db.topic_exists, normalized_name):
        raise TopicStreamsException(
            f"Topic '{normalized_name}' not found", "TOPIC_NOT_FOUND"
        )

    entries = await run_in_threadpool(
        db.get_news_entries, normalized_name, limit, before_id
    )
    total = await run_in_threadpool(db.get_news_count, normalized_name)

    return NewsListResponse(
        entries=entries,
        limit=limit,
        next_before_id=_next_cursor(entries, limit),
        topic=normalized_name,
        total=total,
    )
