"""Tests for the worker's task-execution dispatch (scraper.worker._execute_task).

The full run_engine_worker loop is integration-only (needs Playwright + DB), but
the one piece of new branching — routing a news task through scrape_topic and a
one-off/keep-alive through _serve_oneoff — is unit-testable with the two scrape
calls stubbed out.
"""

import threading

from scraper import worker
from scraper.saturation import SharedEngineState
from scraper.tasks import KeepAliveTask, NewsScrapeTask, WebSearchTask


class _FakeContext:
    def new_page(self):  # passed through to scrape_topic (stubbed); never called
        raise AssertionError("new_page should not be invoked by the stub")


class _Source:
    name = "google"


class _Job:
    def __init__(self, query):
        self.query = query
        self.delivered = None

    def resolve(self, results, logs=None):
        self.delivered = (results, logs)

    def fail(self, exc):
        self.delivered = exc

    def cooling(self):
        self.delivered = "cooling"


def _run(task, monkeypatch, **stubs):
    for name, fn in stubs.items():
        monkeypatch.setattr(worker, name, fn)
    return worker._execute_task(
        _FakeContext(),
        _Source(),
        task,
        pacer=worker._Pacer(0.0, 0.0),
        stop_event=threading.Event(),
        cooldown=None,
        shared_state=SharedEngineState(),
    )


def test_news_task_routes_to_scrape_topic(monkeypatch):
    seen = {}

    def fake_scrape_topic(make_page, sources, topic, **kw):
        seen.update(
            topic=topic,
            engine=sources[0].name,
            strategy=kw.get("strategy"),
            max_pages=kw.get("max_result_pages"),
        )
        return (["entry"], ["log"])

    entries, logs = _run(
        NewsScrapeTask("trump", max_pages=2),
        monkeypatch,
        scrape_topic=fake_scrape_topic,
    )
    assert entries == ["entry"] and logs == ["log"]
    assert seen == {
        "topic": "trump",
        "engine": "google",
        "strategy": "all",
        "max_pages": 2,
    }


def test_keepalive_task_routes_to_serve_oneoff(monkeypatch):
    seen = {}

    def fake_serve_oneoff(context, source, query, vertical, **kw):
        seen.update(query=query, vertical=vertical, max_pages=kw.get("max_pages"))
        return (["e"], ["l"])

    task = KeepAliveTask("weather tomorrow", max_pages=1)
    entries, logs = _run(task, monkeypatch, _serve_oneoff=fake_serve_oneoff)
    assert (entries, logs) == (["e"], ["l"])
    assert seen == {
        "query": "weather tomorrow",
        "vertical": task.vertical,
        "max_pages": 1,
    }


def test_web_task_routes_to_serve_oneoff_with_web_vertical(monkeypatch):
    seen = {}

    def fake_serve_oneoff(context, source, query, vertical, **kw):
        seen.update(query=query, vertical=vertical)
        return ([], [])

    task = WebSearchTask(_Job("us iran"), max_pages=3)
    _run(task, monkeypatch, _serve_oneoff=fake_serve_oneoff)
    assert seen["query"] == "us iran"
    assert seen["vertical"].value == "web"
