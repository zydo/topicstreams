"""On-demand web-search job handoff from a producer to one engine's worker.

On-demand web search reuses each engine's existing warm worker (see
``scraper/worker.py``): a producer submits a query, the engine's worker drains
its queue between news scrapes — preempting due news — runs the WEB-vertical
scrape, and hands the parsed results back to the producer.

Two interchangeable backends share one worker-facing shape — ``poll()`` returns
a job exposing ``query`` plus ``resolve(results, logs)`` / ``fail(exc)``:

- ``WebSearchQueue`` — in-process (``queue.Queue`` + ``Future``). Producer and
  worker live in the same process; used by tests that drive a worker directly.
- ``DbWebSearchQueue`` — cross-process, backed by the shared ``web_search_jobs``
  table. The producer is the API process (see ``api/websearch.py``); the worker
  is the scraper. This is the production path: ``poll()`` claims this engine's
  next job from Postgres and ``resolve``/``fail`` write the result back for the
  API to read. The worker code is identical for both.

The worker reports the per-page ``ScraperLog``s alongside the parsed results so
the DB backend can classify the outcome (a block/CAPTCHA looks like an empty
result otherwise) — which is what lets the API-side dispatcher fall back to
another engine on a block. See TODO.md.
"""

import logging
import queue
from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass, field

from common import database as db
from common.model import NewsEntry, ScraperLog, WebResult

logger = logging.getLogger(__name__)


def classify_outcome(
    results: "list[WebResult]", logs: "list[ScraperLog] | None"
) -> str:
    """Bucket a served web search for the producer's fallback decision.

    ``blocked`` — any page attempt failed (a throttle/block signal or error page;
    these surface as an unsuccessful ScraperLog). ``empty`` — the scrape succeeded
    but parsed nothing. ``ok`` — succeeded with at least one result. The producer
    falls back to another healthy engine on anything but ``ok``.
    """
    if logs and any(not log.success for log in logs):
        return "blocked"
    return "ok" if results else "empty"


@dataclass
class WebSearchJob:
    """One pending web search and the future its result is delivered through
    (in-process backend)."""

    query: str
    future: "Future[list[NewsEntry]]" = field(default_factory=Future)

    def resolve(self, results: list, logs: "list[ScraperLog] | None" = None) -> None:
        """Deliver the served results to the producer's future."""
        try:
            self.future.set_result(results)
        except InvalidStateError:
            pass  # caller already cancelled/timed out

    def fail(self, exc: BaseException) -> None:
        """Propagate a serve failure to the producer's future."""
        try:
            self.future.set_exception(exc)
        except InvalidStateError:
            pass


class WebSearchQueue:
    """Thread-safe FCFS handoff of web searches within one process. ``submit``
    is called by producers; ``poll`` by the worker thread."""

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


@dataclass
class DbWebSearchJob:
    """A job claimed from the ``web_search_jobs`` table. ``resolve``/``fail`` write
    the terminal state back for the API producer to read; both are best-effort
    (a lost write becomes a producer-side timeout) and never raise into the
    worker loop."""

    id: int
    query: str

    def resolve(self, results: list, logs: "list[ScraperLog] | None" = None) -> None:
        outcome = classify_outcome(results, logs)
        payload = [r.model_dump(mode="json") for r in results]
        try:
            db.complete_web_search_job(self.id, outcome, payload, None)
        except Exception:
            logger.exception("failed to record web search result (job %s)", self.id)

    def fail(self, exc: BaseException) -> None:
        try:
            db.complete_web_search_job(self.id, "error", None, str(exc))
        except Exception:
            logger.exception("failed to record web search failure (job %s)", self.id)


class DbWebSearchQueue:
    """Cross-process FCFS queue for one engine, backed by ``web_search_jobs``.

    Worker-facing: ``poll()`` claims this engine's next pending job from Postgres.
    The API-side producer enqueues and reads results through ``api/websearch.py``
    (and ``common.database``), so this class only needs the worker half.
    """

    def __init__(self, engine: str) -> None:
        self._engine = engine

    def poll(self) -> DbWebSearchJob | None:
        """Claim this engine's next pending job, or None if there is none.

        Swallows DB errors (returns None) so a transient Postgres blip can't tear
        down the worker loop — the unclaimed job stays pending for the next poll.
        """
        try:
            claimed = db.claim_web_search_job(self._engine)
        except Exception:
            logger.exception("[%s] failed to claim web search job", self._engine)
            return None
        if claimed is None:
            return None
        return DbWebSearchJob(id=claimed["id"], query=claimed["query"])
