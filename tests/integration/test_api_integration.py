"""Integration tests for the API endpoints against a real Postgres.

Uses the real FastAPI app but constructs TestClient WITHOUT the context
manager, so the lifespan (which starts the Postgres LISTEN/NOTIFY task) does
not run — the route handlers and exception handlers are still exercised.
"""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from common.model import NewsEntry


@pytest.fixture
def client(db):
    from api.main import app

    return TestClient(app)


def _seed(db, topic, n):
    db.add_topic(topic)
    db.insert_news_entries(
        [
            NewsEntry.create_new(topic, f"T{i}", f"https://example.com/{topic}/{i}")
            for i in range(n)
        ]
    )


def test_create_list_delete_topic(client):
    assert client.post("/api/v1/topics", json={"name": "Bitcoin"}).status_code == 201

    listed = client.get("/api/v1/topics").json()
    assert any(t["name"] == "bitcoin" for t in listed)

    assert client.delete("/api/v1/topics/bitcoin").status_code == 200
    assert all(t["name"] != "bitcoin" for t in client.get("/api/v1/topics").json())


def test_create_topic_empty_after_normalization_is_400(client):
    r = client.post("/api/v1/topics", json={"name": "!!!"})
    assert r.status_code == 400
    assert r.json()["error"] == "INVALID_TOPIC_NAME"


def test_news_for_unknown_topic_is_400(client):
    r = client.get("/api/v1/news/does-not-exist")
    assert r.status_code == 400
    assert r.json()["error"] == "TOPIC_NOT_FOUND"


def test_per_topic_feed_with_cursor(client, db):
    _seed(db, "alpha", 3)

    page1 = client.get("/api/v1/news/alpha?limit=2").json()
    assert page1["topic"] == "alpha"
    assert page1["total"] == 3
    assert len(page1["entries"]) == 2
    assert page1["next_before_id"] is not None

    page2 = client.get(
        f"/api/v1/news/alpha?limit=2&before_id={page1['next_before_id']}"
    ).json()
    assert len(page2["entries"]) == 1
    assert page2["next_before_id"] is None


def test_all_topics_feed(client, db):
    _seed(db, "alpha", 2)
    _seed(db, "beta", 1)

    body = client.get("/api/v1/news?limit=10").json()
    assert body["topic"] is None
    assert body["total"] is None
    assert len(body["entries"]) == 3


def test_engines_endpoint_lists_engines_with_data(client, db):
    db.add_topic("alpha")
    url = "https://example.com/a"
    db.insert_news_entries(
        [
            NewsEntry.create_new("alpha", "A", url, engine="google"),
            NewsEntry.create_new("alpha", "A", url, engine="bing"),
        ]
    )

    assert client.get("/api/v1/news/engines").json() == ["bing", "google"]


def test_feed_engine_filter(client, db):
    db.add_topic("alpha")
    db.insert_news_entries(
        [
            NewsEntry.create_new(
                "alpha", "A", "https://example.com/a", engine="google"
            ),
            NewsEntry.create_new("alpha", "B", "https://example.com/b", engine="bing"),
        ]
    )

    google = client.get("/api/v1/news/alpha?engine=google").json()
    assert google["total"] == 1
    assert {e["title"] for e in google["entries"]} == {"A"}
    assert google["entries"][0]["engines"] == ["google"]

    all_feed = client.get("/api/v1/news?engine=bing").json()
    assert {e["title"] for e in all_feed["entries"]} == {"B"}


def test_status_endpoint_shape_and_idle(client, db):
    db.add_topic("alpha")

    body = client.get("/api/v1/status").json()
    assert set(body) >= {"state", "label", "detail", "active_topics", "total_news"}
    assert body["state"] == "idle"  # no scrapes recorded
    assert body["active_topics"] == 1
    assert body["total_news"] == 0


def test_ws_accepts_existing_topic(client, db):
    db.add_topic("alpha")
    with client.websocket_connect("/api/v1/ws/news/alpha"):
        pass  # handshake accepted = connection allowed


def test_ws_rejects_unknown_topic_and_does_not_create_it(client, db):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/v1/ws/news/ghost"):
            pass
    assert exc.value.code == 1008
    # the unauthenticated connect must NOT have created the topic
    assert not db.topic_exists("ghost")
    assert "ghost" not in [t.name for t in db.get_topics(include_inactive=True)]


def test_ws_rejects_soft_deleted_topic(client, db):
    db.add_topic("alpha")
    db.delete_topic("alpha")  # now inactive
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/v1/ws/news/alpha"):
            pass
    assert exc.value.code == 1008


def test_metrics_endpoint(client, db):
    _seed(db, "alpha", 3)

    body = client.get("/api/v1/metrics").json()
    assert body["active_topics"] == 1
    assert body["total_news"] == 3
    assert body["feed_freshness_seconds"] is not None
    assert body["feed_freshness_seconds"] >= 0
    assert body["scrape_success_rate"] is None  # no scraper logs inserted
    # Richer fields are present even with no scrape activity.
    assert body["window_seconds"] == 3600
    assert body["engines"] == []
    assert body["recent_cycles"] == []
    assert body["recent_failures"] == []
    assert body["overall"]["scrapes"] == 0


def test_metrics_with_scraper_activity(client, db):
    from datetime import datetime, timezone

    db.add_topic("alpha")
    # Insert with NOW() (the DB clock) so the rows land inside the metrics
    # window. (ScraperLog.create_new stamps datetime.now(), which is local-naive
    # and would skew from the testcontainer's UTC NOW(); in production both the
    # scraper and the DB run in UTC containers so they agree.)
    with db._Connection() as conn:
        conn.cursor().execute(
            "INSERT INTO scraper_logs "
            "(topic, scraped_at, success, http_status_code, entry_count, engine, duration_ms) "
            "VALUES "
            "('alpha', NOW(), TRUE, 200, 5, 'google', 2000), "
            "('alpha', NOW(), TRUE, 200, 0, 'google', 2100), "
            "('alpha', NOW(), FALSE, 429, 0, 'bing', 500)"
        )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.insert_cycle(
        started_at=now,
        finished_at=now,
        duration_seconds=12.0,
        topics_count=1,
        entries_parsed=5,
        new_events=3,
        success=True,
    )

    body = client.get("/api/v1/metrics?window=3600").json()
    by_engine = {e["engine"]: e for e in body["engines"]}
    assert set(by_engine) == {"google", "bing"}

    google = by_engine["google"]
    assert google["scrapes"] == 2 and google["successes"] == 2
    assert google["entries_parsed"] == 5
    assert google["avg_latency_ms"] == 2050  # (2000 + 2100) / 2
    # 2 scrapes is under the >=3 threshold for selector rot, and rate is 1.0.
    assert google["health"] == "healthy"

    bing = by_engine["bing"]
    assert bing["scrapes"] == 1 and bing["successes"] == 0
    assert bing["health"] == "blocked"  # latest scrape was a 429

    assert body["overall"]["entries_parsed"] == 5
    assert len(body["recent_cycles"]) == 1
    assert body["recent_cycles"][0]["new_events"] == 3
    assert len(body["recent_failures"]) == 1
    assert body["recent_failures"][0]["engine"] == "bing"


def test_response_has_timing_header(client):
    r = client.get("/api/v1/topics")
    assert "x-process-time-ms" in {k.lower() for k in r.headers}
