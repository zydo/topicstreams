"""In-process FCFS queue of on-demand web searches for one engine.

On-demand web search reuses each engine's existing warm worker (see
``scraper/worker.py``): a producer submits a query and gets a ``Future``, the
engine's worker drains the queue between news scrapes — preempting due news —
and hands the parsed results back through the future.

Today the producer and the worker live in the *same* process (this is the
internal, testable milestone), so a thread-safe ``queue.Queue`` + ``Future`` is
all that's needed. When web search is wired to the API/WebSocket the request
originates in a different process, at which point this in-process queue is
replaced by a cross-process bridge (a job queue + result hand-back) behind the
same ``submit``/``poll`` shape — see TODO.md.
"""

import queue
from concurrent.futures import Future
from dataclasses import dataclass, field

from common.model import NewsEntry


@dataclass
class WebSearchJob:
    """One pending web search and the future its result is delivered through."""

    query: str
    future: "Future[list[NewsEntry]]" = field(default_factory=Future)


class WebSearchQueue:
    """Thread-safe FCFS handoff of web searches from producers to one engine's
    worker. ``submit`` is called by producers; ``poll`` by the worker thread."""

    def __init__(self) -> None:
        self._jobs: "queue.Queue[WebSearchJob]" = queue.Queue()

    def submit(self, query: str) -> "Future[list[NewsEntry]]":
        """Enqueue a web search; returns a future that resolves to its results
        (or raises if the worker failed to serve it)."""
        job = WebSearchJob(query=query)
        self._jobs.put(job)
        return job.future

    def poll(self) -> WebSearchJob | None:
        """The next pending job (FCFS), or None if the queue is empty. Never
        blocks — the worker polls between its other work."""
        try:
            return self._jobs.get_nowait()
        except queue.Empty:
            return None

    def pending(self) -> int:
        """Approximate number of queued jobs (for metrics/tests)."""
        return self._jobs.qsize()
