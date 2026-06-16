"""Topic management endpoints for API v1."""


from fastapi import APIRouter, Depends, Query, Path
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from api.auth import require_api_key
from api.exceptions import TopicStreamsException
from common import database as db
from common.model import Topic
from common.utils import normalize_topic

router = APIRouter(prefix="/topics", tags=["topics"])


def _normalize_or_400(raw_name: str) -> str:
    # min_length=1 on the raw input doesn't catch names that normalize to
    # nothing (e.g. "!!!"), which would create an empty-named topic.
    normalized = normalize_topic(raw_name)
    if not normalized:
        raise TopicStreamsException(
            f"Topic name '{raw_name}' is empty after normalization",
            "INVALID_TOPIC_NAME",
        )
    return normalized


class TopicCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Topic name")


@router.get("")
async def get_topics(
    all: bool = Query(
        False,
        description="Show all topics including inactive (soft deleted) ones",
    ),
) -> list[Topic]:
    return await run_in_threadpool(db.get_topics, include_inactive=all)


@router.post("", status_code=201, dependencies=[Depends(require_api_key)])
async def add_topic(topic: TopicCreate) -> None:
    normalized_name = _normalize_or_400(topic.name)
    await run_in_threadpool(db.add_topic, normalized_name)


@router.delete("/{topic_name}", dependencies=[Depends(require_api_key)])
async def delete_topic(
    topic_name: str = Path(..., min_length=1, max_length=100)
) -> None:
    """Delete (soft delete) a topic.

    This operation is idempotent - deleting a non-existent topic succeeds.
    """
    normalized_name = _normalize_or_400(topic_name)
    await run_in_threadpool(db.delete_topic, normalized_name)
