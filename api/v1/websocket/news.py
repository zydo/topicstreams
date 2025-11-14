"""WebSocket endpoints for real-time news updates."""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from common import database as db
from common.utils import normalize_topic
from .manager import get_websocket_manager

router = APIRouter(prefix="/ws/news")

manager = get_websocket_manager()


@router.websocket("/{topic_name}")
async def websocket_news_topic(websocket: WebSocket, topic_name: str) -> None:
    normalized_topic = normalize_topic(topic_name)

    db.add_topic(normalized_topic)  # Ensure the topic exists for continuous scraping

    await manager.connect(websocket, normalized_topic)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, normalized_topic)
    except Exception:
        manager.disconnect(websocket, normalized_topic)
