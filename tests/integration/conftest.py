"""Integration-test fixtures: an ephemeral Postgres via testcontainers.

The whole integration suite is skipped if Docker isn't available.
"""

from pathlib import Path

import pytest

_INIT_SQL = Path(__file__).resolve().parents[2] / "postgres" / "init.sql"


@pytest.fixture(scope="session")
def _postgres():
    postgres_mod = pytest.importorskip("testcontainers.postgres")
    container = postgres_mod.PostgresContainer(
        "postgres:18-alpine",
        username="newsuser",
        password="newspass",
        dbname="newsdb",
    )
    try:
        container.start()
    except Exception as exc:  # Docker daemon not available, image pull failed, etc.
        pytest.skip(f"Postgres testcontainer unavailable: {exc}")
    yield container
    container.stop()


@pytest.fixture(scope="session")
def db(_postgres):
    """Point the app's settings at the test container, load the schema, and
    return the database module. The connection pool is lazy, so overriding
    settings before the first call is enough."""
    from common import database as database
    from common import settings as settings_mod

    s = settings_mod.settings
    s.postgres_host = _postgres.get_container_host_ip()
    s.postgres_port = int(_postgres.get_exposed_port(5432))
    s.postgres_db = _postgres.dbname
    s.postgres_user = _postgres.username
    s.postgres_password = _postgres.password

    database.close_pool()
    with database._Connection() as conn:
        conn.cursor().execute(_INIT_SQL.read_text())

    yield database
    database.close_pool()


@pytest.fixture(autouse=True)
def _clean(db):
    """Reset all rows (and serial counters) before each test for isolation."""
    with db._Connection() as conn:
        conn.cursor().execute(
            "TRUNCATE topic_news, news, scraper_logs, topics RESTART IDENTITY CASCADE"
        )
    yield
