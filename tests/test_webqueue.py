"""Tests for the web-search queues (scraper/webqueue.py).

The queue is the producer→worker handoff for on-demand web search. In-process
(``WebSearchQueue``): a producer ``submit``s a query and gets a Future; the worker
``poll``s FCFS and resolves it. Cross-process (``DbWebSearchQueue``): the worker
claims a job from the shared table and writes the result back for the API to read.
"""

import pytest

from common.model import ScraperLog, WebResult, WebResultKind
from scraper.webqueue import (
    DbWebSearchJob,
    DbWebSearchQueue,
    WebSearchQueue,
    classify_outcome,
)


def _ok_log() -> ScraperLog:
    return ScraperLog.create_new(topic="q", success=True, entry_count=1)


def _failed_log() -> ScraperLog:
    return ScraperLog.create_new(
        topic="q", success=False, http_status_code=429, error_message="blocked"
    )


def _result() -> WebResult:
    return WebResult.create(WebResultKind.ORGANIC, title="t", url="https://e.com/a")


def test_poll_empty_returns_none():
    q = WebSearchQueue()
    assert q.poll() is None
    assert q.pending() == 0


def test_submit_returns_pending_future():
    q = WebSearchQueue()
    fut = q.submit("us iran")
    assert not fut.done()
    assert q.pending() == 1


def test_poll_is_fcfs():
    q = WebSearchQueue()
    q.submit("first")
    q.submit("second")
    assert q.poll().query == "first"
    assert q.poll().query == "second"
    assert q.poll() is None


def test_worker_resolves_future_through_the_job():
    # Simulates the worker side: poll a job, then set its result; the producer
    # reads it back off the future it was handed at submit time.
    q = WebSearchQueue()
    fut = q.submit("weather")

    job = q.poll()
    assert job.query == "weather"
    job.future.set_result(["a", "b"])

    assert fut.done()
    assert fut.result() == ["a", "b"]


def test_worker_can_propagate_failure():
    q = WebSearchQueue()
    fut = q.submit("boom")
    job = q.poll()
    job.future.set_exception(RuntimeError("blocked"))

    import pytest

    with pytest.raises(RuntimeError, match="blocked"):
        fut.result()


def test_pending_tracks_drain():
    q = WebSearchQueue()
    q.submit("a")
    q.submit("b")
    assert q.pending() == 2
    q.poll()
    assert q.pending() == 1


# ── outcome classification (drives the producer's fallback decision) ──────────


def test_classify_ok_when_results_and_clean_logs():
    assert classify_outcome([_result()], [_ok_log()]) == "ok"


def test_classify_empty_when_clean_but_no_results():
    assert classify_outcome([], [_ok_log()]) == "empty"


def test_classify_blocked_when_any_log_failed():
    # A block surfaces as an unsuccessful log even though no results parsed —
    # this is what lets the dispatcher tell a block from a genuinely empty SERP.
    assert classify_outcome([], [_ok_log(), _failed_log()]) == "blocked"
    assert classify_outcome([_result()], [_failed_log()]) == "blocked"


# ── DB-backed cross-process queue ─────────────────────────────────────────────


def test_db_queue_poll_claims_engine_job(monkeypatch):
    seen = {}

    def fake_claim(engine, max_age_seconds=None):
        seen["engine"] = engine
        seen["max_age"] = max_age_seconds
        return {"id": 7, "query": "us iran"}

    monkeypatch.setattr("scraper.webqueue.db.claim_web_search_job", fake_claim)
    job = DbWebSearchQueue("bing", max_age_seconds=25.0).poll()
    assert seen["engine"] == "bing"
    assert seen["max_age"] == 25.0  # the producer's request timeout is passed through
    assert isinstance(job, DbWebSearchJob)
    assert job.id == 7 and job.query == "us iran"


def test_db_queue_poll_returns_none_when_empty(monkeypatch):
    monkeypatch.setattr(
        "scraper.webqueue.db.claim_web_search_job",
        lambda engine, max_age_seconds=None: None,
    )
    assert DbWebSearchQueue("google").poll() is None


def test_db_queue_poll_swallows_db_errors(monkeypatch):
    def boom(engine, max_age_seconds=None):
        raise RuntimeError("db down")

    monkeypatch.setattr("scraper.webqueue.db.claim_web_search_job", boom)
    # A DB blip must not propagate into the worker loop.
    assert DbWebSearchQueue("google").poll() is None


def test_db_job_resolve_writes_results_and_outcome(monkeypatch):
    captured = {}

    def fake_complete(job_id, outcome, results, error):
        captured.update(job_id=job_id, outcome=outcome, results=results, error=error)

    monkeypatch.setattr("scraper.webqueue.db.complete_web_search_job", fake_complete)
    DbWebSearchJob(id=3, query="q").resolve([_result()], [_ok_log()])
    assert captured["job_id"] == 3
    assert captured["outcome"] == "ok"
    assert captured["error"] is None
    assert captured["results"][0]["url"] == "https://e.com/a"


def test_db_job_fail_writes_error_outcome(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "scraper.webqueue.db.complete_web_search_job",
        lambda job_id, outcome, results, error: captured.update(
            outcome=outcome, results=results, error=error
        ),
    )
    DbWebSearchJob(id=3, query="q").fail(RuntimeError("boom"))
    assert captured["outcome"] == "error"
    assert captured["results"] is None
    assert "boom" in captured["error"]


def test_db_job_resolve_never_raises_into_worker(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("write failed")

    monkeypatch.setattr("scraper.webqueue.db.complete_web_search_job", boom)
    # Best-effort: a failed write is logged, not raised.
    DbWebSearchJob(id=3, query="q").resolve([_result()], [_ok_log()])
