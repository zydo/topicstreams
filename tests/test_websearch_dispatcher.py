"""Tests for the API-side web-search dispatcher (api/websearch.py).

Covers healthy-engine selection from the cooldown snapshot and the fallback path
across engines on block/empty/timeout — the two behaviours that make a search
succeed while one engine is benched. The cross-process DB calls are faked so the
logic is exercised without a live Postgres. Coroutines are driven with
``asyncio.run`` (the repo has no pytest-asyncio plugin).
"""

import asyncio
from datetime import datetime, timedelta, timezone

from api import websearch
from api.websearch import _healthy_engines, dispatch_web_search


def _run(coro):
    return asyncio.run(coro)


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── healthy-engine selection ──────────────────────────────────────────────────


def test_healthy_includes_engines_with_no_snapshot():
    assert _healthy_engines(["google", "bing"], {}) == ["google", "bing"]


def test_healthy_excludes_benched_engine():
    cooldowns = {
        "bing": {"next_probe_at": _naive_utc_now() + timedelta(minutes=5)},
    }
    assert _healthy_engines(["google", "bing", "yahoo"], cooldowns) == [
        "google",
        "yahoo",
    ]


def test_healthy_includes_engine_whose_probe_time_passed():
    cooldowns = {
        "google": {"next_probe_at": _naive_utc_now() - timedelta(seconds=1)},
        "bing": {"next_probe_at": None},
    }
    assert _healthy_engines(["google", "bing"], cooldowns) == ["google", "bing"]


def test_healthy_preserves_configured_priority_order():
    assert _healthy_engines(["yahoo", "google"], {}) == ["yahoo", "google"]


# ── dispatch + fallback ───────────────────────────────────────────────────────


class _FakeBridge:
    """In-memory stand-in for the web_search_jobs table. ``outcomes`` maps an
    engine to the terminal row its worker would write (or None to simulate a job
    that never completes — i.e. a producer-side timeout)."""

    def __init__(self, outcomes: dict[str, dict | None]):
        self.outcomes = outcomes
        self.enqueued: list[tuple[int, str, str]] = []
        self.deleted: list[int] = []
        self._next_id = 1
        self._rows: dict[int, dict] = {}

    def enqueue(self, query, engine):
        job_id = self._next_id
        self._next_id += 1
        self.enqueued.append((job_id, query, engine))
        terminal = self.outcomes.get(engine)
        if terminal is not None:
            self._rows[job_id] = {"status": "done", **terminal}
        else:
            self._rows[job_id] = {
                "status": "pending",
                "outcome": None,
                "results": None,
                "error": None,
            }
        return job_id

    def fetch(self, job_id):
        return self._rows.get(job_id)

    def delete(self, job_id):
        self.deleted.append(job_id)
        self._rows.pop(job_id, None)


def _set_prop(monkeypatch, name, value):
    monkeypatch.setattr(
        type(websearch.scraper_config), name, property(lambda self: value)
    )


def _install(monkeypatch, bridge: _FakeBridge, engines: list[str], cooldowns=None):
    monkeypatch.setattr(websearch.db, "get_engine_cooldowns", lambda: cooldowns or {})
    monkeypatch.setattr(websearch.db, "enqueue_web_search", bridge.enqueue)
    monkeypatch.setattr(websearch.db, "fetch_web_search_result", bridge.fetch)
    monkeypatch.setattr(websearch.db, "delete_web_search_job", bridge.delete)
    _set_prop(monkeypatch, "engines", engines)
    # Tight, deterministic timing so timeout cases don't actually wait long.
    _set_prop(monkeypatch, "web_search_request_timeout_seconds", 0.05)
    _set_prop(monkeypatch, "web_search_poll_interval_seconds", 0.01)
    _set_prop(monkeypatch, "web_search_max_engine_attempts", 3)


def _ok_row(url="https://e.com/a"):
    return {
        "outcome": "ok",
        "results": [{"kind": "organic", "title": "t", "url": url, "domain": "e.com"}],
        "error": None,
    }


def test_blank_query_short_circuits():
    res = _run(dispatch_web_search("   "))
    assert res.status == "empty"


def test_no_healthy_engine_is_unavailable(monkeypatch):
    bridge = _FakeBridge({})
    benched = {"google": {"next_probe_at": _naive_utc_now() + timedelta(minutes=5)}}
    _install(monkeypatch, bridge, ["google"], cooldowns=benched)
    res = _run(dispatch_web_search("us iran"))
    assert res.status == "unavailable"
    assert bridge.enqueued == []  # nothing dispatched to a benched engine


def test_first_healthy_engine_serves(monkeypatch):
    bridge = _FakeBridge({"google": _ok_row()})
    _install(monkeypatch, bridge, ["google", "bing"])
    res = _run(dispatch_web_search("us iran"))
    assert res.status == "ok"
    assert res.engine == "google"
    assert res.attempts == ["google"]
    assert [r.url for r in res.results] == ["https://e.com/a"]
    assert bridge.deleted  # the job row was cleaned up


def test_falls_back_past_blocked_engine(monkeypatch):
    bridge = _FakeBridge(
        {
            "google": {"outcome": "blocked", "results": None, "error": None},
            "bing": _ok_row("https://b.com/x"),
        }
    )
    _install(monkeypatch, bridge, ["google", "bing"])
    res = _run(dispatch_web_search("us iran"))
    assert res.status == "ok"
    assert res.engine == "bing"
    assert res.attempts == ["google", "bing"]


def test_falls_back_past_empty_engine(monkeypatch):
    bridge = _FakeBridge(
        {
            "google": {"outcome": "empty", "results": [], "error": None},
            "bing": _ok_row(),
        }
    )
    _install(monkeypatch, bridge, ["google", "bing"])
    res = _run(dispatch_web_search("us iran"))
    assert res.status == "ok"
    assert res.engine == "bing"


def test_timeout_falls_back_to_next_engine(monkeypatch):
    # google never completes (None terminal) -> timeout -> try bing.
    bridge = _FakeBridge({"google": None, "bing": _ok_row()})
    _install(monkeypatch, bridge, ["google", "bing"])
    res = _run(dispatch_web_search("us iran"))
    assert res.status == "ok"
    assert res.engine == "bing"
    assert res.attempts == ["google", "bing"]
    assert len(bridge.deleted) == 2  # both jobs cleaned up, incl. the timed-out one


def test_all_engines_blocked_returns_last_status(monkeypatch):
    bridge = _FakeBridge(
        {
            "google": {"outcome": "blocked", "results": None, "error": None},
            "bing": {"outcome": "blocked", "results": None, "error": None},
        }
    )
    _install(monkeypatch, bridge, ["google", "bing"])
    res = _run(dispatch_web_search("us iran"))
    assert res.status == "blocked"
    assert res.engine is None
    assert res.attempts == ["google", "bing"]


def test_max_attempts_bounds_fanout(monkeypatch):
    bridge = _FakeBridge(
        {
            e: {"outcome": "blocked", "results": None, "error": None}
            for e in ["google", "bing", "yahoo", "brave"]
        }
    )
    _install(monkeypatch, bridge, ["google", "bing", "yahoo", "brave"])
    _set_prop(monkeypatch, "web_search_max_engine_attempts", 2)
    res = _run(dispatch_web_search("us iran"))
    assert res.attempts == ["google", "bing"]  # capped at 2 despite 4 healthy
