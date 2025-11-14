"""PostgreSQL database connection and operations module."""

import time
from functools import wraps
from typing import List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from psycopg2.pool import ThreadedConnectionPool

from common.settings import settings
from common.model import Topic, NewsEntry, ScraperLog


# Package-level connection pool singleton
_pool: Optional[ThreadedConnectionPool] = None


# Transient errors that should trigger retry
TRANSIENT_ERRORS = (
    psycopg2.OperationalError,  # Connection issues, server restart
    psycopg2.InterfaceError,  # Connection lost during operation
)


def retry_on_transient_error(max_attempts: int = 3, delay_seconds: float = 0.1):
    """Decorator to retry database operations on transient failures.

    Args:
        max_attempts: Maximum number of retry attempts (default: 3)
        delay_seconds: Initial delay between retries in seconds (default: 0.1)
                      Uses exponential backoff: delay * (2 ** attempt)
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except TRANSIENT_ERRORS:
                    if attempt < max_attempts - 1:
                        # Exponential backoff
                        sleep_time = delay_seconds * (2**attempt)
                        time.sleep(sleep_time)
                    else:
                        # Final attempt failed, re-raise
                        raise

        return wrapper

    return decorator


def _get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
            host=settings.postgres_host,
            port=settings.postgres_port,
            database=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
            connect_timeout=10,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


class _Connection:
    def __init__(self) -> None:
        self._conn = _get_pool().getconn()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        if exc_type:
            self._conn.rollback()
        else:
            self._conn.commit()
        _get_pool().putconn(self._conn)

    def cursor(self):
        return self._conn.cursor(cursor_factory=RealDictCursor)


@retry_on_transient_error()
def get_topics(include_inactive: bool = False) -> List[Topic]:
    if include_inactive:
        sql = "SELECT id, name, created_at, is_active FROM topics ORDER BY created_at DESC"
    else:
        sql = "SELECT id, name, created_at, is_active FROM topics WHERE is_active = TRUE ORDER BY created_at DESC"

    with _Connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall()
        return [Topic.from_db_row(dict(row)) for row in rows]


@retry_on_transient_error()
def add_topic(name: str) -> None:
    """Add a new topic or reactivate an existing (but deactivated) one."""
    dml = (
        "INSERT INTO topics (name, is_active) "
        "VALUES (%s, TRUE) "
        "ON CONFLICT (name) "
        "DO UPDATE SET is_active = TRUE"
    )
    with _Connection() as conn:
        cursor = conn.cursor()
        cursor.execute(dml, (name,))


@retry_on_transient_error()
def delete_topic(name: str) -> None:
    """Deactivate (soft delete) a topic.

    This operation is idempotent - calling it multiple times has the same effect.
    If the topic doesn't exist, the operation succeeds silently.
    """
    with _Connection() as conn:
        cursor = conn.cursor()
        dml = "UPDATE topics SET is_active = FALSE WHERE name = %s"
        cursor.execute(dml, (name,))


@retry_on_transient_error()
def get_news_entries(topic: str, limit: int = 20, offset: int = 0) -> List[NewsEntry]:
    """Get news entries for a specific topic with pagination.

    Args:
        topic: The topic name to fetch news for.
        limit: Maximum number of entries to return (default: 20).
        offset: Number of entries to skip for pagination (default: 0).

    Returns:
        List of NewsEntry objects ordered by scraped_at DESC (newest first).
    """
    with _Connection() as conn:
        sql = (
            "SELECT id, topic, title, url, domain, source, scraped_at "
            "FROM news_entries WHERE topic = %s "
            "ORDER BY scraped_at DESC LIMIT %s OFFSET %s"
        )
        cursor = conn.cursor()
        cursor.execute(sql, (topic, limit, offset))
        rows = cursor.fetchall()
        return [NewsEntry.from_db_row(dict(row)) for row in rows]


@retry_on_transient_error()
def get_news_entry(entry_id: str) -> Optional[NewsEntry]:
    with _Connection() as conn:
        sql = """
            SELECT id, topic, title, url, domain, source, scraped_at
            FROM news_entries
            WHERE id = %s
        """
        cursor = conn.cursor()
        cursor.execute(sql, (entry_id,))
        row = cursor.fetchone()
        return NewsEntry.from_db_row(dict(row)) if row else None


@retry_on_transient_error()
def get_news_count(topic: str) -> int:
    with _Connection() as conn:
        sql = "SELECT COUNT(id) as count FROM news_entries WHERE topic = %s"
        cursor = conn.cursor()
        cursor.execute(sql, (topic,))
        result = cursor.fetchone()
        return result["count"] if result else 0


@retry_on_transient_error()
def insert_news_entries(entries: List[NewsEntry]) -> int:
    """Insert news entries into database with automatic deduplication.

    Args:
        entries: List of NewsEntry objects to insert.

    Returns:
        Number of entries actually inserted.
    """
    if not entries:
        return 0

    with _Connection() as conn:
        dml = """
            INSERT INTO news_entries (topic, title, url, domain, source)
            VALUES %s
            ON CONFLICT (topic, title, domain) DO NOTHING
        """

        values = [
            (
                entry.topic,
                entry.title,
                entry.url,
                entry.domain,
                entry.source,
            )
            for entry in entries
        ]

        cursor = conn.cursor()
        execute_values(cursor, dml, values)
        return cursor.rowcount


@retry_on_transient_error()
def insert_scraper_logs(logs: List[ScraperLog]) -> int:
    """Insert scraper log entries into the database.

    Args:
        logs: List of ScraperLog objects to insert.

    Returns:
        Number of logs actually inserted.
    """
    if not logs:
        return 0

    with _Connection() as conn:
        dml = """
            INSERT INTO scraper_logs (topic, scraped_at, success, http_status_code, error_message)
            VALUES %s
        """

        values = [
            (log.topic, log.scraped_at, log.success, log.http_status_code, log.error_message)
            for log in logs
        ]

        cursor = conn.cursor()
        execute_values(cursor, dml, values)
        return cursor.rowcount


@retry_on_transient_error()
def get_scraper_logs(limit: int = 10) -> List[ScraperLog]:
    """Get recent scraper log entries ordered by scraped_at DESC.

    Args:
        limit: Maximum number of log entries to return (default: 10).

    Returns:
        List of ScraperLog objects ordered by scraped_at DESC (newest first).
    """
    with _Connection() as conn:
        sql = """
            SELECT id, topic, scraped_at, success, http_status_code, error_message
            FROM scraper_logs
            ORDER BY scraped_at DESC
            LIMIT %s
        """
        cursor = conn.cursor()
        cursor.execute(sql, (limit,))
        rows = cursor.fetchall()
        return [ScraperLog.from_db_row(dict(row)) for row in rows]
