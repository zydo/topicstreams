"""News retrieval endpoints for API v1."""

from typing import List

from fastapi import APIRouter, Query, Path
from pydantic import BaseModel, Field

from common import database as db
from common.model import NewsEntry
from common.utils import normalize_topic


router = APIRouter(prefix="/news", tags=["news"])


class NewsListResponse(BaseModel):
    topic: str = Field(..., description="Normalized topic name")
    entries: List[NewsEntry] = Field(..., description="List of news entries")
    total: int = Field(..., description="Total number of news entries for this topic")
    limit: int = Field(..., description="Number of entries returned per page")
    offset: int = Field(..., description="Current offset (cursor position)")


@router.get("/{topic_name}")
async def get_news(
    topic_name: str = Path(..., min_length=1, max_length=100),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> NewsListResponse:
    normalized_name = normalize_topic(topic_name)
    entries = db.get_news_entries(normalized_name, limit, offset)
    total = db.get_news_count(normalized_name)

    return NewsListResponse(
        topic=normalized_name,
        entries=entries,
        total=total,
        limit=limit,
        offset=offset,
    )
