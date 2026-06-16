"""WebSocket manager for handling real-time connections and Postgres notifications."""

import asyncio
import json
import logging
from collections import defaultdict

import psycopg2
from fastapi import WebSocket
from psycopg2.extensions import connection as PgConnection, ISOLATION_LEVEL_AUTOCOMMIT
from starlette.concurrency import run_in_threadpool

from common import database as db
from common.model import NewsEntry
from common.settings import settings

logger = logging.getLogger(__name__)


class WebSocketManager:
    def __init__(self):
        self._conn: PgConnection | None = None
        self._listener_task: asyncio.Task[None] | None = None
        self._topic_subscribers: defaultdict[str, set[WebSocket]] = defaultdict(set)

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

    def _reconnect(self) -> PgConnection:
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        return self._get_conn()

    async def _handle_notification(self, payload: str) -> None:
        topic, entry_id = payload.rsplit(":", 1)
        # Threadpool: a sync psycopg2 call here would block the event loop
        # (all HTTP/WS traffic) on every notification.
        entry = await run_in_threadpool(db.get_news_entry, entry_id)
        if entry:
            await self._broadcast_to_topic(topic, entry)

    async def _postgres_listener(self, conn: PgConnection) -> None:
        while True:
            try:
                conn.poll()
                while conn.notifies:
                    notify = conn.notifies.pop(0)
                    try:
                        await self._handle_notification(notify.payload)
                    except Exception:
                        # A bad payload or transient DB error must not tear
                        # down a healthy LISTEN connection.
                        logger.exception(
                            "Failed to handle notification: %s", notify.payload
                        )
            except Exception:
                logger.exception("Postgres listener error, reconnecting...")
                conn = await self._reconnect_with_backoff()
            await asyncio.sleep(1)

    async def _reconnect_with_backoff(self) -> PgConnection:
        # Keep retrying until Postgres is back: an unhandled exception here
        # would silently kill the listener task until the API restarts.
        delay = 1.0
        while True:
            try:
                return await run_in_threadpool(self._reconnect)
            except Exception:
                logger.exception("Reconnect failed, retrying in %.0fs", delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)

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
        except asyncio.CancelledError:
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

        message: dict = entry.model_dump(mode="json")

        formatted_json = json.dumps(
            message,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            separators=(", ", ": "),
        )

        disconnected: list[WebSocket] = []

        for connection in self._topic_subscribers[topic]:
            try:
                await connection.send_text(formatted_json)
            except Exception as e:
                logger.debug("WebSocket send failed, marking as disconnected: %s", e)
                disconnected.append(connection)

        for connection in disconnected:
            self._topic_subscribers[topic].discard(connection)

    async def connect(self, websocket: WebSocket, topic: str) -> None:
        await websocket.accept()
        self._topic_subscribers[topic].add(websocket)

    def disconnect(self, websocket: WebSocket, topic: str) -> None:
        self._topic_subscribers[topic].discard(websocket)


# Single module-level instance — avoids fragile __new__ singleton
manager = WebSocketManager()
