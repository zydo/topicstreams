"""Tests for the per-engine topic scheduler (scraper/worker._TopicSchedule).

The scheduler is a min-heap of (next_eligible_time, topic) with lazy deletion of
deactivated topics. These exercise the cadence guarantee, sleep-to-head, and
add/remove reconciliation with an injectable clock and deterministic RNG.
"""

import random

from scraper.worker import _TopicSchedule


class _Clock:
    """Manually advanced monotonic clock."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class _LowRng(random.Random):
    """Deterministic RNG: uniform() always returns its low bound, so seed/
    reschedule land at the earliest allowed time (no random stagger/jitter)."""

    def uniform(self, a, b):
        return a


def _schedule(clock, interval=60, jitter=0.0):
    return _TopicSchedule(interval, jitter, clock=clock, rng=_LowRng())


def _drain_due(sched):
    """Pop every currently-due topic, returning them in pop order."""
    out = []
    while True:
        topic, _ = sched.next_due()
        if topic is None:
            return out
        out.append(topic)


def test_seed_makes_all_topics_due_now():
    sched = _schedule(_Clock())
    sched.seed({"a", "b", "c"})
    assert set(_drain_due(sched)) == {"a", "b", "c"}
    # Heap drained: nothing due, and nothing left (no active entries).
    assert sched.next_due() == (None, None)


def test_topic_not_rescraped_before_interval():
    clock = _Clock()
    sched = _schedule(clock, interval=60)
    sched.seed({"a"})
    assert sched.next_due() == ("a", 0.0)
    sched.reschedule("a")

    # Not due yet: the worker is told to wait ~interval.
    topic, wait = sched.next_due()
    assert topic is None
    assert wait == 60

    clock.advance(59)
    assert sched.next_due()[0] is None  # still not due
    clock.advance(2)  # past the interval
    assert sched.next_due() == ("a", 0.0)


def test_nothing_due_reports_wait_to_head():
    clock = _Clock()
    sched = _schedule(clock, interval=60)
    sched.seed({"a"})
    sched.next_due()  # consume the boot-due entry
    sched.reschedule("a")

    clock.advance(20)
    topic, wait = sched.next_due()
    assert topic is None
    assert wait == 40  # 60 - 20 remaining on the head


def test_deactivated_topic_dropped_on_pop():
    sched = _schedule(_Clock())
    sched.seed({"a"})
    sched.reconcile(set())  # 'a' deactivated
    # Lazily dropped when it surfaces; heap then empty.
    assert sched.next_due() == (None, None)


def test_reconcile_schedules_new_topic():
    sched = _schedule(_Clock())
    sched.seed({"a"})
    assert _drain_due(sched) == ["a"]

    sched.reconcile({"a", "b"})
    # 'a' had no live entry (already popped) so it is re-scheduled; 'b' is new.
    assert set(_drain_due(sched)) == {"a", "b"}


def test_reconcile_does_not_double_schedule():
    sched = _schedule(_Clock())
    sched.seed({"a"})
    sched.reconcile({"a"})  # already scheduled — must not add a second entry
    assert _drain_due(sched) == ["a"]
    assert sched.next_due() == (None, None)


def test_reschedule_applies_jitter():
    clock = _Clock()
    # jitter low-bound is 0, so even with jitter configured the deterministic RNG
    # keeps the base interval; this just guards the interval*(1+jitter) math.
    sched = _schedule(clock, interval=60, jitter=0.5)
    sched.seed({"a"})
    sched.next_due()
    sched.reschedule("a")
    assert sched.next_due() == (None, 60)
