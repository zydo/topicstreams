"""Tests for the per-engine task model (scraper/tasks.py).

Covers the Task priority/delivery contract and the three generators
(news/web/keepalive), all with an injectable clock + deterministic RNG. The
underlying _TopicSchedule cadence is exercised in test_topic_schedule.py; here we
test the generator wrappers and the new backlog/overdue signal.
"""

import random

from scraper.tasks import (
    CLASS_IDLE,
    CLASS_ONEOFF,
    CLASS_SCHEDULED,
    WEB_POLL_INTERVAL,
    EngineTaskQueue,
    KeepAliveGenerator,
    KeepAliveTask,
    NewsScrapeTask,
    NewsTaskGenerator,
    WebSearchSource,
    WebSearchTask,
    _TopicSchedule,
)
from scraper.webqueue import EngineCoolingError, WebSearchQueue


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class _LowRng(random.Random):
    """uniform() returns its low bound, so stagger/jitter is deterministic 0."""

    def uniform(self, a, b):
        return a


def _schedule(clock, interval=60, jitter=0.0):
    return _TopicSchedule(interval, jitter, clock=clock, rng=_LowRng())


# ── Task priority + delivery ──────────────────────────────────────────────────


def test_priority_ordering_oneoff_before_news_before_idle():
    web = WebSearchTask(_FakeJob("q"))
    news_soon = NewsScrapeTask("a", when=10.0)
    news_later = NewsScrapeTask("b", when=20.0)
    idle = KeepAliveTask("weather")
    ordered = sorted([idle, news_later, web, news_soon], key=lambda t: t.priority)
    assert [t.kind for t in ordered] == ["web", "news", "news", "keepalive"]
    assert web.priority[0] == CLASS_ONEOFF
    assert news_soon.priority == (CLASS_SCHEDULED, 10.0)
    assert idle.priority[0] == CLASS_IDLE


def test_news_task_is_tracked_web_and_keepalive_are_not():
    assert NewsScrapeTask("a").tracked is True
    assert WebSearchTask(_FakeJob("q")).tracked is False
    assert KeepAliveTask("q").tracked is False


def test_news_task_delivery_is_noop():
    # Tracked tasks don't hand results back; deliver_* must be safe no-ops.
    task = NewsScrapeTask("a")
    task.deliver_success([], [])
    task.deliver_failure(RuntimeError("x"))
    task.deliver_cooling()  # nothing to assert beyond "doesn't raise"


class _FakeJob:
    """Minimal job exposing the worker-facing shape (query + resolve/fail/cooling)."""

    def __init__(self, query):
        self.query = query
        self.resolved = None
        self.failed = None
        self.cooled = False

    def resolve(self, results, logs=None):
        self.resolved = (results, logs)

    def fail(self, exc):
        self.failed = exc

    def cooling(self):
        self.cooled = True


def test_web_task_delivers_to_its_job():
    job = _FakeJob("us iran")
    task = WebSearchTask(job)
    assert task.query == "us iran"
    task.deliver_success(["r"], ["log"])
    assert job.resolved == (["r"], ["log"])
    task.deliver_failure(ValueError("boom"))
    assert isinstance(job.failed, ValueError)
    task.deliver_cooling()
    assert job.cooled is True


# ── NewsTaskGenerator (coalesce + self-reschedule + backlog) ──────────────────


def test_news_generator_emits_due_task_then_reschedules():
    clock = _Clock()
    gen = NewsTaskGenerator(_schedule(clock, interval=60), max_pages=3)
    gen.seed({"a"})
    task, wait = gen.poll()
    assert isinstance(task, NewsScrapeTask) and task.topic == "a"
    assert task.max_pages == 3  # generator stamps the page cap
    assert wait == 0.0

    gen.complete(task)  # self-reschedule one interval out
    task2, wait2 = gen.poll()
    assert task2 is None and wait2 == 60

    clock.advance(61)
    task3, _ = gen.poll()
    assert task3.topic == "a"


def test_news_generator_coalesces_per_topic():
    # reconcile must not double-schedule an already-pending topic.
    gen = NewsTaskGenerator(_schedule(_Clock()))
    gen.seed({"a"})
    gen.reconcile({"a"})
    first, _ = gen.poll()
    assert first.topic == "a"
    # Only one pending entry existed, so nothing is due now.
    again, _ = gen.poll()
    assert again is None


def test_news_generator_reports_no_topics():
    gen = NewsTaskGenerator(_schedule(_Clock()))
    gen.seed(set())
    task, wait = gen.poll()
    assert task is None and wait is None  # empty heap


def test_backlog_counts_overdue_topics_and_lateness():
    clock = _Clock()
    sched = _schedule(clock, interval=60)
    sched.seed({"a", "b", "c"})  # _LowRng => all seeded due now
    # All three are overdue (due at t=1000) before any are popped.
    count, lateness = sched.backlog()
    assert count == 3
    assert lateness == 0.0

    clock.advance(5)
    count, lateness = sched.backlog()
    assert count == 3
    assert lateness == 5.0  # oldest overdue is 5s late

    # Popping one (simulating a scrape) removes it from the backlog.
    sched.next_due()
    count, _ = sched.backlog()
    assert count == 2


def test_backlog_excludes_future_scheduled_topics():
    clock = _Clock()
    sched = _schedule(clock, interval=60)
    sched.seed({"a"})
    sched.next_due()  # pop the boot-due entry
    sched.reschedule("a")  # now scheduled 60s out
    count, lateness = sched.backlog()
    assert count == 0 and lateness == 0.0  # a healthy, caught-up engine


# ── WebSearchSource ───────────────────────────────────────────────────────────


def test_web_source_claims_one_job_as_task():
    queue = WebSearchQueue()
    future = queue.submit("bitcoin price")
    source = WebSearchSource(queue, max_pages=2)
    task = source.poll()
    assert isinstance(task, WebSearchTask)
    assert task.query == "bitcoin price"
    assert task.max_pages == 2
    assert source.poll() is None  # FCFS drained
    assert not future.done()  # not delivered until the task runs


def test_web_source_pending_count():
    queue = WebSearchQueue()
    queue.submit("a")
    queue.submit("b")
    assert WebSearchSource(queue).pending() == 2


def test_web_source_cooling_raises_to_in_process_caller():
    queue = WebSearchQueue()
    future = queue.submit("a")
    task = WebSearchSource(queue).poll()
    task.deliver_cooling()
    assert future.done()
    try:
        future.result()
        assert False, "expected EngineCoolingError"
    except EngineCoolingError:
        pass


# ── KeepAliveGenerator ────────────────────────────────────────────────────────


class _Heartbeat:
    """Stand-in exposing the KeepAliveHeartbeat surface the generator uses."""

    def __init__(self, is_due):
        self._due = is_due
        self.activity = 0

    def due(self):
        return self._due

    def next_query(self):
        return "weather tomorrow"

    def seconds_until_due(self):
        return 0.0 if self._due else 42.0

    def record_activity(self):
        self.activity += 1


def test_keepalive_generator_emits_only_when_due():
    assert KeepAliveGenerator(_Heartbeat(False)).poll() is None
    task = KeepAliveGenerator(_Heartbeat(True)).poll()
    assert isinstance(task, KeepAliveTask)
    assert task.query == "weather tomorrow"
    assert task.vertical.value == "news"  # warm-up uses the NEWS vertical
    assert task.max_pages == 1


def test_keepalive_generator_passes_through_timer():
    hb = _Heartbeat(False)
    gen = KeepAliveGenerator(hb)
    assert gen.seconds_until_due() == 42.0
    gen.record_activity()
    assert hb.activity == 1


# ── EngineTaskQueue facade ────────────────────────────────────────────────────


def _queue(clock, *, with_web=None, heartbeat=None, interval=60):
    news = NewsTaskGenerator(_schedule(clock, interval=interval))
    web = WebSearchSource(with_web) if with_web is not None else None
    ka = KeepAliveGenerator(heartbeat) if heartbeat is not None else None
    return news, EngineTaskQueue(news, web=web, keepalive=ka)


def test_queue_oneoff_preempts_due_news():
    clock = _Clock()
    web_q = WebSearchQueue()
    news, queue = _queue(clock, with_web=web_q)
    queue.seed({"a"})  # a news topic is due now
    web_q.submit("breaking")  # ...but a one-off is waiting
    task, wait = queue.pop_ready()
    assert isinstance(task, WebSearchTask) and task.query == "breaking"
    assert wait == 0.0


def test_queue_returns_due_news_when_no_oneoff():
    clock = _Clock()
    news, queue = _queue(clock, with_web=WebSearchQueue())
    queue.seed({"a"})
    task, wait = queue.pop_ready()
    assert isinstance(task, NewsScrapeTask) and task.topic == "a"
    assert wait == 0.0


def test_queue_keepalive_only_when_nothing_due():
    clock = _Clock()
    _, queue = _queue(clock, heartbeat=_Heartbeat(True))
    queue.seed(set())  # no topics
    task, _ = queue.pop_ready()
    assert isinstance(task, KeepAliveTask)


def test_queue_idle_wait_is_min_of_sources():
    clock = _Clock()
    sched_clock = clock
    web_q = WebSearchQueue()
    news = NewsTaskGenerator(_schedule(sched_clock, interval=60))
    queue = EngineTaskQueue(
        news,
        web=WebSearchSource(web_q),
        keepalive=KeepAliveGenerator(_Heartbeat(False)),
    )
    queue.seed({"a"})
    queue.pop_ready()  # consume the boot-due 'a'
    news.complete(NewsScrapeTask("a"))  # 'a' now 60s out
    task, wait = queue.pop_ready()
    assert task is None
    # news head is 60s out, keepalive 42s, web poll 1s -> min is the web slice.
    assert wait == WEB_POLL_INTERVAL


def test_queue_idle_wait_without_web_or_keepalive():
    clock = _Clock()
    news, queue = _queue(clock, interval=60)  # news only
    queue.seed({"a"})
    queue.pop_ready()
    news.complete(NewsScrapeTask("a"))
    task, wait = queue.pop_ready()
    assert task is None and wait == 60


def test_queue_on_completed_reschedules_news_and_warms_keepalive():
    clock = _Clock()
    hb = _Heartbeat(False)
    news, queue = _queue(clock, heartbeat=hb, interval=60)
    queue.seed({"a"})
    task, _ = queue.pop_ready()
    queue.on_completed(task)
    # news 'a' re-armed 60s out...
    nxt, wait = queue.pop_ready()
    assert nxt is None and wait == min(60, hb.seconds_until_due())
    # ...and the keep-alive timer was reset by the activity.
    assert hb.activity == 1


def test_queue_on_completed_ignores_non_news_for_reschedule():
    clock = _Clock()
    hb = _Heartbeat(False)
    _, queue = _queue(clock, heartbeat=hb)
    queue.on_completed(KeepAliveTask("weather"))  # not a news task
    assert hb.activity == 1  # still warms the session


def test_queue_health_reports_backlog_and_pending():
    clock = _Clock()
    web_q = WebSearchQueue()
    web_q.submit("a")
    web_q.submit("b")
    news, queue = _queue(clock, with_web=web_q, interval=60)
    queue.seed({"x", "y", "z"})  # all due now -> overdue backlog of 3
    health = queue.health()
    assert health.overdue_count == 3
    assert health.max_lateness_seconds == 0.0
    assert health.pending_oneoffs == 2
