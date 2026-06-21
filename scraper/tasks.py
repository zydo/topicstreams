"""Per-engine task model for the scraper scheduler.

Every "web page access" an engine makes is a **Task** — one browser request the
engine's scheduler (``scraper/worker.py``) runs on its single warm context. Tasks
come from **generators** and differ only in *priority* and *what happens to the
result*; the scheduler executes them all on one shared pace + cooldown budget.

Three task kinds, three outcomes (the divergence the scheduler doesn't branch on):

- :class:`NewsScrapeTask` — a tracked-topic scrape. Emitted by
  :class:`NewsTaskGenerator` (a timer heap keyed by each topic's next-eligible
  time, so it's really a *scheduled* task). Its result flows into the scheduler's
  metrics window → Postgres; nothing is handed back to a caller (``tracked``).
- :class:`WebSearchTask` — an on-demand, user-facing web search. Emitted by
  :class:`WebSearchSource` (the cross-process / in-process job queue). Highest
  priority (preempts news); its result is handed straight back to the waiting
  caller and **not** persisted.
- :class:`KeepAliveTask` — an idle NEWS warm-up. Emitted by
  :class:`KeepAliveGenerator` only when nothing else is ready; no result delivery.

A task's ``priority`` is ``(class, when)`` — lowest first: one-off (class 0) before
any scheduled news (class 1, ordered by next-eligible time) before idle
keep-alive (class 2). The :class:`EngineTaskQueue` facade realises that order
across the sources without a single literal heap (one-offs arrive cross-process,
so they're *claimed into* the head rather than living in an in-memory heap).
"""

from __future__ import annotations

import heapq
import logging
import random
import time
from abc import ABC, abstractmethod
from typing import Callable

from common.model import NewsEntry, ScraperLog

from .keepalive import KeepAliveHeartbeat
from .sources import SearchVertical

logger = logging.getLogger(__name__)

# Priority classes (lower runs first); see the module docstring.
CLASS_ONEOFF = 0
CLASS_SCHEDULED = 1
CLASS_IDLE = 2


# ── Tasks ─────────────────────────────────────────────────────────────────────


class Task(ABC):
    """One web-page access for an engine to run, plus how to deliver its result.

    Subclasses set ``kind``/``vertical``/``tracked``/``max_pages`` and implement
    ``query`` and ``priority``. The scheduler executes the task (it owns the
    context, pacer, and cooldown) and then calls exactly one ``deliver_*`` hook —
    which is where the per-kind outcome lives (hand a one-off back to its caller;
    no-op for tracked work, whose result the scheduler routes to its window/DB).
    """

    kind: str
    vertical: SearchVertical
    #: True ⇒ the scheduler buffers this task's result into its metrics window
    #: (and thus to Postgres). False ⇒ transient; nothing persisted.
    tracked: bool = False
    #: Page cap for this request (None ⇒ the engine default).
    max_pages: int | None = None

    @property
    @abstractmethod
    def query(self) -> str:
        """The search query / topic this task fetches."""

    @property
    @abstractmethod
    def priority(self) -> tuple[int, float]:
        """``(class, when)`` ordering key; lower runs first."""

    def deliver_success(self, entries: list[NewsEntry], logs: list[ScraperLog]) -> None:
        """Hand a successful run back to whoever's waiting. No-op for tracked
        tasks (news/keep-alive): their results flow through the scheduler's
        metrics window to Postgres, not back to a caller."""

    def deliver_failure(self, exc: BaseException) -> None:
        """Hand a failed run back to whoever's waiting (no-op for tracked work)."""

    def deliver_cooling(self) -> None:
        """The engine is benched — fail fast *without running*. The scheduler
        calls this instead of executing the task while the engine is cooling, so
        a waiting caller gets a prompt, distinct answer rather than a timeout.
        No-op for tracked work (a news scrape simply stays scheduled and waits)."""


class NewsScrapeTask(Task):
    """A due tracked-topic scrape. Tracked: its result goes to the metrics
    window, not back to a caller."""

    kind = "news"
    vertical = SearchVertical.NEWS
    tracked = True

    def __init__(self, topic: str, *, when: float = 0.0, max_pages: int | None = None):
        self.topic = topic
        self.when = when
        self.max_pages = max_pages

    @property
    def query(self) -> str:
        return self.topic

    @property
    def priority(self) -> tuple[int, float]:
        return (CLASS_SCHEDULED, self.when)


class WebSearchTask(Task):
    """An on-demand, user-facing web search. Highest priority; its result is
    delivered straight back to the producer (a future, or the cross-process job
    row) and never persisted."""

    kind = "web"
    vertical = SearchVertical.WEB
    tracked = False

    def __init__(self, job, *, max_pages: int | None = None):
        # ``job`` is a WebSearchJob / DbWebSearchJob — both expose query +
        # resolve/fail/cooling (see scraper/webqueue.py).
        self._job = job
        self.max_pages = max_pages

    @property
    def query(self) -> str:
        return self._job.query

    @property
    def priority(self) -> tuple[int, float]:
        return (CLASS_ONEOFF, 0.0)

    def deliver_success(self, entries: list[NewsEntry], logs: list[ScraperLog]) -> None:
        self._job.resolve(entries, logs)

    def deliver_failure(self, exc: BaseException) -> None:
        self._job.fail(exc)

    def deliver_cooling(self) -> None:
        self._job.cooling()


class KeepAliveTask(Task):
    """An idle NEWS warm-up to keep the session from going cold. Lowest priority;
    no result delivery (it exists only to make a benign successful request)."""

    kind = "keepalive"
    vertical = SearchVertical.NEWS
    tracked = False

    def __init__(self, query: str, *, max_pages: int | None = 1):
        self._query = query
        self.max_pages = max_pages

    @property
    def query(self) -> str:
        return self._query

    @property
    def priority(self) -> tuple[int, float]:
        return (CLASS_IDLE, 0.0)


# ── News timer schedule (moved here from worker so generators sit with it) ─────


class _TopicSchedule:
    """Per-engine min-heap of ``(next_eligible_monotonic, topic)`` with lazy
    deletion of deactivated topics.

    The clock is monotonic and injectable for tests. ``_scheduled`` tracks which
    topics currently have a live heap entry so reconciliation never double-pushes
    a topic (coalescing: at most one pending entry per topic); ``_active`` is the
    authoritative set of topics that should be scraped (a topic dropped from it is
    removed from the heap the next time it surfaces).
    """

    def __init__(
        self,
        interval: float,
        jitter_ratio: float,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ):
        self._interval = interval
        self._jitter_ratio = max(0.0, jitter_ratio)
        self._clock = clock
        self._rng = rng or random
        self._heap: list[tuple[float, str]] = []
        self._scheduled: set[str] = set()
        self._active: set[str] = set()

    def _push(self, topic: str, when: float) -> None:
        heapq.heappush(self._heap, (when, topic))
        self._scheduled.add(topic)

    def seed(self, topics: set[str]) -> None:
        """Initial population: stagger every topic across one interval so a fresh
        worker doesn't fire every topic at boot (thundering herd on the engine)."""
        self._active = set(topics)
        now = self._clock()
        for topic in topics:
            self._push(topic, now + self._rng.uniform(0.0, self._interval))

    def reconcile(self, active: set[str]) -> None:
        """Sync to the latest active set: schedule newly-added topics (spread
        across an interval) and drop vanished ones lazily on pop."""
        self._active = set(active)
        now = self._clock()
        for topic in self._active:
            if topic not in self._scheduled:
                self._push(topic, now + self._rng.uniform(0.0, self._interval))

    def reschedule(self, topic: str) -> None:
        """Re-arm a just-scraped topic for ``interval`` (plus jitter) from now."""
        when = self._clock() + self._interval * (
            1.0 + self._rng.uniform(0.0, self._jitter_ratio)
        )
        self._push(topic, when)

    def next_due(self) -> tuple[str | None, float | None]:
        """What the worker should do next.

        Returns ``(topic, 0.0)`` for a topic to scrape now, ``(None, seconds)`` to
        wait that long for the head to come due, or ``(None, None)`` when the heap
        is empty (no active topics). Deactivated topics are dropped here.
        """
        while self._heap:
            when, topic = self._heap[0]
            if topic not in self._active:
                heapq.heappop(self._heap)
                self._scheduled.discard(topic)
                continue
            if when > self._clock():
                return None, when - self._clock()
            heapq.heappop(self._heap)
            self._scheduled.discard(topic)
            return topic, 0.0
        return None, None

    def backlog(self) -> tuple[int, float]:
        """``(overdue_count, max_lateness_seconds)`` — active topics whose
        next-eligible time has already passed but that haven't been popped yet.

        This is the real scrape backlog (the topic currently being scraped was
        already popped, so it isn't counted). A healthy engine keeps it near zero;
        sustained growth means the engine is falling behind — a *lagging*
        complement to the direct block/cooldown signal, not a replacement.
        """
        now = self._clock()
        overdue = [
            when
            for (when, topic) in self._heap
            if topic in self._active and when <= now
        ]
        if not overdue:
            return 0, 0.0
        return len(overdue), now - min(overdue)


# ── Generators (sources of tasks) ─────────────────────────────────────────────


class NewsTaskGenerator:
    """Emits :class:`NewsScrapeTask` for due tracked topics, backed by a
    :class:`_TopicSchedule`. ``complete`` re-arms a finished task's topic, so the
    queue self-reschedules and can't pile up duplicates per topic."""

    def __init__(self, schedule: _TopicSchedule, *, max_pages: int | None = None):
        self._schedule = schedule
        self._max_pages = max_pages

    def seed(self, topics: set[str]) -> None:
        self._schedule.seed(topics)

    def reconcile(self, topics: set[str]) -> None:
        self._schedule.reconcile(topics)

    def poll(self) -> tuple[NewsScrapeTask | None, float | None]:
        """``(task, 0.0)`` for a due topic, ``(None, seconds)`` to wait for the
        head, or ``(None, None)`` if there are no active topics."""
        topic, wait = self._schedule.next_due()
        if topic is None:
            return None, wait
        return NewsScrapeTask(topic, max_pages=self._max_pages), 0.0

    def complete(self, task: NewsScrapeTask) -> None:
        """Re-arm a just-run topic for the next interval."""
        self._schedule.reschedule(task.topic)

    def backlog(self) -> tuple[int, float]:
        return self._schedule.backlog()


class WebSearchSource:
    """Emits :class:`WebSearchTask` by claiming one job from a web-search queue
    (in-process ``WebSearchQueue`` or cross-process ``DbWebSearchQueue``)."""

    def __init__(self, queue, *, max_pages: int | None = None):
        self._queue = queue
        self._max_pages = max_pages

    def poll(self) -> WebSearchTask | None:
        job = self._queue.poll()
        if job is None:
            return None
        return WebSearchTask(job, max_pages=self._max_pages)

    def pending(self) -> int:
        """Best-effort count of waiting jobs (0 if the backend can't report it
        cheaply — e.g. the DB-backed queue, which would need a COUNT query)."""
        pending = getattr(self._queue, "pending", None)
        return pending() if callable(pending) else 0


class KeepAliveGenerator:
    """Emits a :class:`KeepAliveTask` when the session has been idle past the
    heartbeat interval, wrapping :class:`KeepAliveHeartbeat`."""

    def __init__(self, heartbeat: KeepAliveHeartbeat):
        self._heartbeat = heartbeat

    def poll(self) -> KeepAliveTask | None:
        if self._heartbeat.due():
            return KeepAliveTask(self._heartbeat.next_query())
        return None

    def seconds_until_due(self) -> float:
        return self._heartbeat.seconds_until_due()

    def record_activity(self) -> None:
        self._heartbeat.record_activity()
