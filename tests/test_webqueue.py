"""Tests for the in-process web-search queue (scraper/webqueue.py).

The queue is the producer→worker handoff for on-demand web search: a producer
``submit``s a query and gets a Future; the worker ``poll``s FCFS and resolves it.
"""

from scraper.webqueue import WebSearchQueue


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
