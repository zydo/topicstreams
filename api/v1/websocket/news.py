"""WebSocket endpoints for real-time news updates."""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from common import database as db
from common.utils import normalize_topic
from .manager import manager

router = APIRouter(prefix="/ws/news")


@router.websocket("/{topic_name}")
async def websocket_news_topic(websocket: WebSocket, topic_name: str) -> None:
    normalized_topic = normalize_topic(topic_name)
    if not normalized_topic:
        await websocket.close(code=1008, reason="Invalid topic name")
        return

    # Do NOT create the topic here. Auto-creating on connect let any
    # unauthenticated client add scraper targets (more Google requests ->
    # CAPTCHA/cost risk). Only stream topics that already exist; creation is
    # done via the authenticated POST /topics.
    if not await run_in_threadpool(db.topic_exists, normalized_topic):
        await websocket.close(code=1008, reason="Unknown topic")
        return

    await manager.connect(websocket, normalized_topic)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, normalized_topic)
    except Exception:
        manager.disconnect(websocket, normalized_topic)
