"""Integration tests for the API endpoints against a real Postgres.

Uses the real FastAPI app but constructs TestClient WITHOUT the context
manager, so the lifespan (which starts the Postgres LISTEN/NOTIFY task) does
not run — the route handlers and exception handlers are still exercised.
"""

import pytest
from fastapi.testclient import TestClient

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


def test_status_endpoint_shape_and_idle(client, db):
    db.add_topic("alpha")

    body = client.get("/api/v1/status").json()
    assert set(body) >= {"state", "label", "detail", "active_topics", "total_news"}
    assert body["state"] == "idle"  # no scrapes recorded
    assert body["active_topics"] == 1
    assert body["total_news"] == 0
