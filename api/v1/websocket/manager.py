"""WebSocket manager for handling real-time connections and Postgres notifications."""

import asyncio
import json
from collections import defaultdict
from typing import Dict, List, Optional, Set

import psycopg2
from fastapi import WebSocket
from psycopg2.extensions import connection as PgConnection, ISOLATION_LEVEL_AUTOCOMMIT

from api.exceptions import NotificationError
from common import database as db
from common.model import NewsEntry
from common.settings import settings


class WebSocketManager:
    _instance: Optional[WebSocketManager] = None

    def __new__(cls) -> WebSocketManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._conn: Optional[PgConnection] = None
        self._listener_task: Optional[asyncio.Task[None]] = None
        self._topic_subscribers: defaultdict[str, Set[WebSocket]] = defaultdict(set)
        self._initialized = True

    def _get_conn(self) -> PgConnection:
        if self._conn:
            return self._conn

        self._conn = psycopg2.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            database=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
        )

        self._conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        self._conn.cursor().execute("LISTEN news_updates;")
        return self._conn

    async def _handle_notification(self, payload: str) -> None:
        try:
            topic, entry_id = payload.rsplit(":", 1)
            entry = db.get_news_entry(entry_id)
            if entry:
                await self._broadcast_to_topic(topic, entry)

        except ValueError as e:
            raise NotificationError(f"Invalid payload format: {payload}", payload)
        except Exception as e:
            raise NotificationError(f"Failed to handle notification: {e}", payload)

    async def _postgres_listener(self, conn: PgConnection) -> None:
        while True:
            conn.poll()
            while conn.notifies:
                notify = conn.notifies.pop(0)
                await self._handle_notification(notify.payload)
            # CRITICAL: This await yields control back to the event loop, preventing CPU
            # busy-wait. Without this, the infinite while loop would block FastAPI 
            # startup and other async tasks. 100ms provides good balance between 
            # responsiveness and CPU efficiency
            await asyncio.sleep(0.1)

    def start_listener(self) -> None:
        if self._listener_task is not None:
            return

        conn = self._get_conn()
        self._listener_task = asyncio.create_task(self._postgres_listener(conn))

    async def stop_listener(self) -> None:
        if self._listener_task is None:
            return

        self._listener_task.cancel()
        try:
            await self._listener_task
        except asyncio.CancelledError:  # noqa: S7497
            pass

        self._listener_task = None
        if self._conn:
            self._conn.close()
            self._conn = None

    # TODO: This broadcasting does not scale with subscribers growth.
    #       Use Redis Pub/Sub or Kafka Producer.
    async def _broadcast_to_topic(self, topic: str, entry: NewsEntry) -> None:
        if topic not in self._topic_subscribers:
            return

        message: Dict = entry.model_dump(mode="json")

        formatted_json = json.dumps(
            message,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            separators=(", ", ": "),
        )

        disconnected: List[WebSocket] = []

        for connection in self._topic_subscribers[topic]:
            try:
                await connection.send_text(formatted_json)
            except (RuntimeError, TypeError):
                disconnected.append(connection)
            except Exception:
                disconnected.append(connection)

        for connection in disconnected:
            self._topic_subscribers[topic].discard(connection)

    async def connect(self, websocket: WebSocket, topic: str) -> None:
        await websocket.accept()
        self._topic_subscribers[topic].add(websocket)

    def disconnect(self, websocket: WebSocket, topic: str) -> None:
        self._topic_subscribers[topic].discard(websocket)


_websocket_manager = WebSocketManager()


def get_websocket_manager() -> WebSocketManager:
    return _websocket_manager


def start_listener() -> None:
    _websocket_manager.start_listener()


async def stop_listener() -> None:
    await _websocket_manager.stop_listener()
