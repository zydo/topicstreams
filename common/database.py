"""PostgreSQL database connection and operations module."""

import threading
import time
from functools import wraps

import psycopg2
from psycopg2.extensions import connection as PgConnection
from psycopg2.extras import RealDictCursor, execute_values
from psycopg2.pool import ThreadedConnectionPool

from common.settings import settings
from common.model import Topic, NewsEntry, ScraperLog
from common.utils import news_id_for_url

# A feed entry is a topic_news (match) row joined to its news content. The
# exposed id is the topic_news id (the feed cursor); scraped_at is matched_at.
# `engines` aggregates every engine that surfaced this feed event (LEFT JOIN so
# events with no engine row still appear, with an empty list).
_FEED_SELECT = (
    "SELECT tn.id, tn.topic, n.title, n.url, n.domain, n.source, n.snippet, "
    "tn.matched_at AS scraped_at, "
    "COALESCE(array_agg(tne.engine ORDER BY tne.engine) "
    "FILTER (WHERE tne.engine IS NOT NULL), '{}') AS engines "
    "FROM topic_news tn JOIN news n ON n.id = tn.news_id "
    "LEFT JOIN topic_news_engines tne ON tne.topic_news_id = tn.id"
)

# Grouped by both PKs so every selected news column is functionally dependent.
_FEED_GROUP_BY = " GROUP BY tn.id, n.id"

# Restrict the feed to events a specific engine surfaced, without dropping the
# other engines from each row's aggregated `engines` list.
_ENGINE_EXISTS = (
    " AND EXISTS (SELECT 1 FROM topic_news_engines f "
    "WHERE f.topic_news_id = tn.id AND f.engine = %s)"
)

# Package-level connection pool singleton
_pool: ThreadedConnectionPool | None = None
# Guards lazy init: concurrent first requests (FastAPI threadpool) could
# otherwise create two pools and return connections to the wrong one,
# raising "trying to put unkeyed connection".
_pool_lock = threading.Lock()
# ThreadedConnectionPool.getconn raises "connection pool exhausted" instead
# of waiting; this caps concurrent checkouts so burst requests queue.
_conn_slots = threading.BoundedSemaphore(settings.db_pool_max_conn)


# Transient errors that should trigger retry
TRANSIENT_ERRORS = (
    psycopg2.OperationalError,  # Connection issues, server restart
    psycopg2.InterfaceError,  # Connection lost during operation
)


def retry_on_transient_error(
    max_attempts: int | None = None, delay_seconds: float | None = None
):
    """Decorator to retry database operations on transient failures.

    Args:
        max_attempts: Retry attempts; defaults to settings.db_retry_max_attempts.
        delay_seconds: Initial backoff; defaults to settings.db_retry_delay_seconds.
                      Uses exponential backoff: delay * (2 ** attempt)
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            attempts = (
                max_attempts
                if max_attempts is not None
                else settings.db_retry_max_attempts
            )
            delay = (
                delay_seconds
                if delay_seconds is not None
                else settings.db_retry_delay_seconds
            )
            for attempt in range(attempts):
                try:
                    return func(*args, **kwargs)
                except TRANSIENT_ERRORS:
                    if attempt < attempts - 1:
                        # Exponential backoff
                        sleep_time = delay * (2**attempt)
                        time.sleep(sleep_time)
                    else:
                        # Final attempt failed, re-raise
                        raise

        return wrapper

    return decorator


def _get_pool() -> ThreadedConnectionPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadedConnectionPool(
                minconn=settings.db_pool_min_conn,
                maxconn=settings.db_pool_max_conn,
                host=settings.postgres_host,
                port=settings.postgres_port,
                database=settings.postgres_db,
                user=settings.postgres_user,
                password=settings.postgres_password,
                connect_timeout=settings.db_connect_timeout,
                keepalives=1,
                keepalives_idle=settings.db_keepalives_idle,
                keepalives_interval=settings.db_keepalives_interval,
                keepalives_count=settings.db_keepalives_count,
            )
        return _pool


def close_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None


class _Connection:
    def __init__(self) -> None:
        self._pool: ThreadedConnectionPool | None = None
        self._conn: PgConnection | None = None

    def __enter__(self):
        _conn_slots.acquire()
        try:
            # Remember which pool the connection came from: re-resolving the
            # global in __exit__ could return a different pool instance.
            self._pool = _get_pool()
            self._conn = self._pool.getconn()
        except BaseException:
            _conn_slots.release()
            raise
        return self

    def __exit__(self, exc_type, *_):
        if self._conn is None:
            return
        try:
            if exc_type:
                self._conn.rollback()
            else:
                self._conn.commit()
        finally:
            try:
                self._pool.putconn(self._conn)
            finally:
                _conn_slots.release()

    def cursor(self):
        return self._conn.cursor(cursor_factory=RealDictCursor)


@retry_on_transient_error()
def get_topics(include_inactive: bool = False) -> list[Topic]:
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
def topic_exists(name: str) -> bool:
    with _Connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM topics WHERE name = %s AND is_active = TRUE LIMIT 1", (name,)
        )
        return cursor.fetchone() is not None


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
def get_news_entries(
    topic: str,
    limit: int = 20,
    before_id: int | None = None,
    engine: str | None = None,
) -> list[NewsEntry]:
    """Get news entries for a topic, newest first, via id-cursor pagination.

    Args:
        topic: The topic name to fetch news for.
        limit: Maximum number of entries to return (default: 20).
        before_id: Return only entries older than this id (exclusive). None
            starts from the newest entry. Cursor pagination is immune to the
            offset drift that live insertions cause at the top of the feed.
        engine: If set, only entries surfaced by this engine (e.g. 'bing').

    Returns:
        List of NewsEntry objects (topic matches) ordered by feed id DESC
        (newest match first).
    """
    with _Connection() as conn:
        sql = _FEED_SELECT + " WHERE tn.topic = %s"
        params: list = [topic]
        if engine:
            sql += _ENGINE_EXISTS
            params.append(engine)
        if before_id is not None:
            sql += " AND tn.id < %s"
            params.append(before_id)
        sql += _FEED_GROUP_BY + " ORDER BY tn.id DESC LIMIT %s"
        params.append(limit)

        cursor = conn.cursor()
        cursor.execute(sql, tuple(params))
        rows = cursor.fetchall()
        return [NewsEntry.from_db_row(dict(row)) for row in rows]


@retry_on_transient_error()
def get_news_entries_all(
    limit: int = 20, before_id: int | None = None, engine: str | None = None
) -> list[NewsEntry]:
    """Get news entries across all active topics, newest first.

    Mirrors get_news_entries but spans every active topic, so the feed's
    "All topics" view is a single chronological stream of topic matches.
    Matches on soft-deleted topics are excluded to track the active set. An
    article matched by several topics appears once per match, by design.
    ``engine`` optionally restricts to events a given engine surfaced.
    """
    with _Connection() as conn:
        sql = (
            _FEED_SELECT
            + " JOIN topics t ON t.name = tn.topic WHERE t.is_active = TRUE"
        )
        params: list = []
        if engine:
            sql += _ENGINE_EXISTS
            params.append(engine)
        if before_id is not None:
            sql += " AND tn.id < %s"
            params.append(before_id)
        sql += _FEED_GROUP_BY + " ORDER BY tn.id DESC LIMIT %s"
        params.append(limit)

        cursor = conn.cursor()
        cursor.execute(sql, tuple(params))
        rows = cursor.fetchall()
        return [NewsEntry.from_db_row(dict(row)) for row in rows]


@retry_on_transient_error()
def get_news_entry(entry_id: str) -> NewsEntry | None:
    """Resolve a single feed entry by its topic_news (feed) id."""
    with _Connection() as conn:
        cursor = conn.cursor()
        cursor.execute(_FEED_SELECT + " WHERE tn.id = %s" + _FEED_GROUP_BY, (entry_id,))
        row = cursor.fetchone()
        return NewsEntry.from_db_row(dict(row)) if row else None


@retry_on_transient_error()
def get_news_count(topic: str, engine: str | None = None) -> int:
    with _Connection() as conn:
        sql = "SELECT COUNT(*) as count FROM topic_news tn WHERE tn.topic = %s"
        params: list = [topic]
        if engine:
            sql += _ENGINE_EXISTS
            params.append(engine)
        cursor = conn.cursor()
        cursor.execute(sql, tuple(params))
        result = cursor.fetchone()
        return result["count"] if result else 0


# The engine filter only offers engines seen within this window, so one that
# stops producing (disabled, or long rate-limited) ages out of the dropdown on
# its own instead of lingering until retention purges its rows. Configurable via
# FEED_ENGINES_WINDOW_DAYS in the environment.
FEED_ENGINES_WINDOW_DAYS = settings.feed_engines_window_days


@retry_on_transient_error()
def get_feed_engines() -> list[str]:
    """Engines that have surfaced a feed event within the recency window, sorted.

    Powers the UI engine filter so it offers only engines with *recent* data.
    """
    with _Connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT engine FROM topic_news_engines "
            "WHERE seen_at > NOW() - make_interval(days => %s) ORDER BY engine",
            (FEED_ENGINES_WINDOW_DAYS,),
        )
        return [row["engine"] for row in cursor.fetchall()]


@retry_on_transient_error()
def insert_news_entries(entries: list[NewsEntry]) -> int:
    """Store scraped articles and their topic matches.

    Each article is upserted once into `news` (keyed on a URL-derived content
    id), then a `topic_news` match row is upserted per (topic, article).

    Args:
        entries: List of NewsEntry objects (each carries a topic).

    Returns:
        Number of new feed events (topic_news rows actually inserted).
    """
    if not entries:
        return 0

    # First occurrence of each article wins for url/title/domain/source; the
    # snippet instead keeps the *longest* seen (engines excerpt differently).
    news_by_id: dict = {}  # news_id -> [url, title, domain, source, snippet]
    match_pairs: set = set()  # distinct (topic, news_id)
    # (topic, news_id, engine) facts to attribute each match to its engine(s).
    engine_facts: set = set()
    for entry in entries:
        news_id = str(news_id_for_url(entry.url))
        row = news_by_id.get(news_id)
        if row is None:
            news_by_id[news_id] = [
                entry.url,
                entry.title,
                entry.domain,
                entry.source,
                entry.snippet,
            ]
        elif entry.snippet and (not row[4] or len(entry.snippet) > len(row[4])):
            row[4] = entry.snippet  # keep the longest snippet within the batch
        match_pairs.add((entry.topic, news_id))
        if entry.engine:
            engine_facts.add((entry.topic, news_id, entry.engine))

    news_values = [(nid, *vals) for nid, vals in news_by_id.items()]

    with _Connection() as conn:
        cursor = conn.cursor()
        execute_values(
            cursor,
            "INSERT INTO news (id, url, title, domain, source, snippet) VALUES %s "
            # Keep the longest snippet across re-scrapes/engines; identity-bearing
            # columns are immutable so they stay DO NOTHING in spirit.
            "ON CONFLICT (id) DO UPDATE SET snippet = EXCLUDED.snippet "
            "WHERE EXCLUDED.snippet IS NOT NULL AND ("
            "news.snippet IS NULL "
            "OR length(EXCLUDED.snippet) > length(news.snippet))",
            news_values,
        )
        execute_values(
            cursor,
            "INSERT INTO topic_news (topic, news_id) VALUES %s "
            "ON CONFLICT (topic, news_id) DO NOTHING",
            list(match_pairs),
        )
        new_events = cursor.rowcount

        # Attribute matches to the engines that surfaced them. We need the
        # topic_news id for every (topic, news_id) in this batch — both rows
        # just inserted and ones from earlier cycles (a new engine can find an
        # already-known article) — so resolve them all, then upsert the facts.
        if engine_facts:
            cursor.execute(
                "SELECT id, topic, news_id::text AS news_id FROM topic_news "
                "WHERE (topic, news_id) IN %s",
                (tuple(match_pairs),),
            )
            id_by_pair = {
                (r["topic"], r["news_id"]): r["id"] for r in cursor.fetchall()
            }
            engine_values = [
                (id_by_pair[(topic, news_id)], engine)
                for (topic, news_id, engine) in engine_facts
                if (topic, news_id) in id_by_pair
            ]
            if engine_values:
                execute_values(
                    cursor,
                    "INSERT INTO topic_news_engines (topic_news_id, engine) "
                    "VALUES %s ON CONFLICT (topic_news_id, engine) DO NOTHING",
                    engine_values,
                )

        return new_events


# No retry: scraper_logs has no unique constraint, so a retry after a commit
# that actually landed (e.g. connection dropped on confirmation) would
# duplicate rows. Losing a diagnostic log batch on a transient error is fine.
def insert_scraper_logs(logs: list[ScraperLog]) -> int:
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
            INSERT INTO scraper_logs
                (topic, scraped_at, success, http_status_code, error_message, entry_count, engine)
            VALUES %s
        """

        values = [
            (
                log.topic,
                log.scraped_at,
                log.success,
                log.http_status_code,
                log.error_message,
                log.entry_count,
                log.engine,
            )
            for log in logs
        ]

        cursor = conn.cursor()
        execute_values(cursor, dml, values)
        return cursor.rowcount


@retry_on_transient_error()
def purge_old_news_entries(days: int) -> int:
    """Delete feed events older than `days` days, then drop now-orphaned
    articles. Returns the number of feed events deleted."""
    with _Connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM topic_news WHERE matched_at < NOW() - %s * INTERVAL '1 day'",
            (days,),
        )
        deleted = cursor.rowcount
        cursor.execute(
            "DELETE FROM news WHERE NOT EXISTS "
            "(SELECT 1 FROM topic_news tn WHERE tn.news_id = news.id)"
        )
        return deleted


@retry_on_transient_error()
def purge_old_scraper_logs(days: int) -> int:
    """Delete scraper logs older than `days` days. Returns the count deleted.

    Unlike news, scraper_logs has no other retention, so without this it grows
    monotonically (one row per topic per scrape cycle)."""
    with _Connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM scraper_logs WHERE scraped_at < NOW() - %s * INTERVAL '1 day'",
            (days,),
        )
        return cursor.rowcount


@retry_on_transient_error()
def get_scraper_logs(limit: int = 10) -> list[ScraperLog]:
    """Get recent scraper log entries ordered by scraped_at DESC.

    Args:
        limit: Maximum number of log entries to return (default: 10).

    Returns:
        List of ScraperLog objects ordered by scraped_at DESC (newest first).
    """
    with _Connection() as conn:
        sql = """
            SELECT id, topic, scraped_at, success, http_status_code, error_message, entry_count, engine
            FROM scraper_logs
            ORDER BY scraped_at DESC
            LIMIT %s
        """
        cursor = conn.cursor()
        cursor.execute(sql, (limit,))
        rows = cursor.fetchall()
        return [ScraperLog.from_db_row(dict(row)) for row in rows]


@retry_on_transient_error()
def get_active_feed_count() -> int:
    """Total feed events (topic matches) across active topics — the 'filed' count."""
    with _Connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT count(*) AS count FROM topic_news tn "
            "JOIN topics t ON t.name = tn.topic WHERE t.is_active = TRUE"
        )
        result = cursor.fetchone()
        return result["count"] if result else 0


@retry_on_transient_error()
def get_feed_freshness_seconds() -> float | None:
    """Seconds since the newest feed event across active topics, or None if empty."""
    with _Connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT EXTRACT(EPOCH FROM (NOW() - MAX(tn.matched_at))) AS age "
            "FROM topic_news tn JOIN topics t ON t.name = tn.topic "
            "WHERE t.is_active = TRUE"
        )
        result = cursor.fetchone()
        age = result["age"] if result else None
        return float(age) if age is not None else None
