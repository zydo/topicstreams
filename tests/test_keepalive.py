"""Tests for the per-engine idle keep-alive heartbeat (scraper/keepalive.py).

The clock and RNG are injected so the timer logic is deterministic and tested
without sleeping.
"""

import random

import pytest

from scraper.keepalive import DEFAULT_QUERIES, KeepAliveHeartbeat


class _Clock:
    """Manually advanced monotonic clock."""

    def __init__(self, t: float = 0.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _hb(clock, *, interval=600.0, jitter_ratio=0.0, queries=DEFAULT_QUERIES):
    # jitter_ratio=0 by default for deterministic timing; rng seeded so the
    # startup shuffle is reproducible.
    return KeepAliveHeartbeat(
        interval=interval,
        jitter_ratio=jitter_ratio,
        queries=queries,
        clock=clock,
        rng=random.Random(0),
    )


def test_due_immediately_at_startup():
    # The first check fires the startup warm-up.
    hb = _hb(_Clock())
    assert hb.due() is True
    assert hb.seconds_until_due() == 0.0


def test_record_activity_defers_until_interval_elapses():
    clock = _Clock()
    hb = _hb(clock, interval=600.0)

    hb.record_activity()
    assert hb.due() is False
    assert hb.seconds_until_due() == pytest.approx(600.0)

    clock.advance(599.0)
    assert hb.due() is False

    clock.advance(1.0)  # exactly at the boundary
    assert hb.due() is True


def test_real_work_resets_the_idle_timer():
    # A real scrape (record_activity) keeps deferring the heartbeat, so a busy
    # session never fires one.
    clock = _Clock()
    hb = _hb(clock, interval=600.0)
    for _ in range(5):
        clock.advance(300.0)  # half an interval of "real work" each time
        hb.record_activity()
        assert hb.due() is False


def test_next_query_rotates_without_immediate_repeat():
    hb = _hb(_Clock())
    picks = [hb.next_query() for _ in range(len(DEFAULT_QUERIES))]
    # One full rotation covers every query exactly once (no repeats).
    assert sorted(picks) == sorted(DEFAULT_QUERIES)
    # Then it wraps around to the start of the same shuffled order.
    assert hb.next_query() == picks[0]


def test_all_queries_are_benign_defaults():
    hb = _hb(_Clock())
    seen = {hb.next_query() for _ in range(len(DEFAULT_QUERIES))}
    assert seen == set(DEFAULT_QUERIES)


def test_restarts_vary_the_opening_query():
    # Different RNG seeds shuffle to different opening queries, so a fleet of
    # restarts doesn't all hammer the same query first.
    def first_query(seed):
        hb = KeepAliveHeartbeat(
            interval=600.0, queries=DEFAULT_QUERIES, rng=random.Random(seed)
        )
        return hb.next_query()

    openings = {first_query(s) for s in range(20)}
    assert len(openings) > 1


def test_jitter_keeps_delay_within_bounds():
    clock = _Clock()
    hb = _hb(clock, interval=600.0, jitter_ratio=0.5)
    hb.record_activity()
    # Delay is interval * (1 + uniform(0, jitter_ratio)) -> [600, 900].
    assert 600.0 <= hb.seconds_until_due() <= 900.0


def test_empty_queries_rejected():
    with pytest.raises(ValueError):
        KeepAliveHeartbeat(interval=600.0, queries=[])
