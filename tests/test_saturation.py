"""Tests for the weighted exit-IP saturation signal and the proactive pacer."""

import threading

from scraper.cooldown import CooldownSnapshot
from scraper.saturation import (
    SharedEngineState,
    evaluate_backlog,
    evaluate_saturation,
    log_saturation,
)
from scraper.tasks import SchedulerHealth
from scraper.worker import _Pacer


def _snap(engine: str, failures: int) -> CooldownSnapshot:
    return CooldownSnapshot(engine=engine, failures=failures, remaining_seconds=0.0)


def _health(overdue=0, lateness=0.0, pending=0) -> SchedulerHealth:
    return SchedulerHealth(
        overdue_count=overdue, max_lateness_seconds=lateness, pending_oneoffs=pending
    )


# --- evaluate_saturation --------------------------------------------------


def test_not_saturated_below_threshold():
    snaps = [_snap("google", 1), _snap("bing", 0), _snap("yahoo", 0)]
    verdict = evaluate_saturation(snaps, canary_engines=["brave"], robust_threshold=2)
    assert verdict.saturated is False
    assert verdict.cooling_robust == ["google"]


def test_saturated_when_robust_engines_reach_threshold():
    snaps = [_snap("google", 2), _snap("bing", 1), _snap("yahoo", 0)]
    verdict = evaluate_saturation(snaps, canary_engines=["brave"], robust_threshold=2)
    assert verdict.saturated is True
    assert verdict.cooling_robust == ["bing", "google"]  # sorted


def test_canary_cooling_does_not_signal_saturation():
    # Brave (canary) tripping plus one robust engine must NOT cross a threshold
    # of 2 — the canary is excluded from the count.
    snaps = [_snap("brave", 5), _snap("google", 1)]
    verdict = evaluate_saturation(snaps, canary_engines=["brave"], robust_threshold=2)
    assert verdict.saturated is False
    assert verdict.cooling_robust == ["google"]
    assert verdict.cooling_canary == ["brave"]


def test_log_saturation_emits_only_when_saturated(caplog):
    healthy = evaluate_saturation(
        [_snap("google", 0)], canary_engines=["brave"], robust_threshold=2
    )
    with caplog.at_level("WARNING"):
        log_saturation(healthy)
    assert "SATURATION" not in caplog.text

    saturated = evaluate_saturation(
        [_snap("google", 1), _snap("bing", 1)],
        canary_engines=["brave"],
        robust_threshold=2,
    )
    with caplog.at_level("WARNING"):
        log_saturation(saturated)
    assert "SATURATION SUSPECTED" in caplog.text


# --- evaluate_backlog -----------------------------------------------------


def test_backlog_not_behind_when_caught_up():
    # Oldest overdue topic is 30s late, well under 3 * 60s interval.
    v = evaluate_backlog("google", _health(overdue=2, lateness=30.0), interval=60)
    assert v.behind is False
    assert v.overdue_count == 2
    assert v.max_lateness_seconds == 30.0


def test_backlog_behind_when_lateness_exceeds_factor():
    # 200s late > 3 * 60s; the engine is cycling slower than its cadence.
    v = evaluate_backlog("google", _health(overdue=8, lateness=200.0), interval=60)
    assert v.behind is True


def test_backlog_lateness_factor_is_configurable():
    h = _health(overdue=1, lateness=120.0)
    assert evaluate_backlog("g", h, interval=60, lateness_factor=1.0).behind is True
    assert evaluate_backlog("g", h, interval=60, lateness_factor=3.0).behind is False


def test_log_backlog_only_when_behind(caplog):
    from scraper.saturation import log_backlog

    with caplog.at_level("WARNING"):
        log_backlog(evaluate_backlog("google", _health(lateness=10.0), interval=60))
    assert "falling behind" not in caplog.text
    with caplog.at_level("WARNING"):
        log_backlog(
            evaluate_backlog("brave", _health(overdue=9, lateness=999.0), interval=60)
        )
    assert "brave" in caplog.text and "falling behind" in caplog.text


# --- SharedEngineState ----------------------------------------------------


def test_shared_state_keeps_latest_per_engine():
    state = SharedEngineState()
    state.update(_snap("google", 1))
    state.update(_snap("google", 3))  # overwrites
    state.update(_snap("bing", 0))
    by_engine = {s.engine: s.failures for s in state.all()}
    assert by_engine == {"google": 3, "bing": 0}


def test_shared_state_keeps_latest_health_per_engine():
    state = SharedEngineState()
    state.update_health("google", _health(overdue=1))
    state.update_health("google", _health(overdue=4))  # overwrites
    state.update_health("bing", _health(overdue=0, pending=2))
    by_engine = state.health_all()
    assert by_engine["google"].overdue_count == 4
    assert by_engine["bing"].pending_oneoffs == 2


# --- _Pacer ---------------------------------------------------------------


class _FakeStop:
    """Stand-in for threading.Event that records wait() durations and never set."""

    def __init__(self):
        self.waits: list[float] = []

    def wait(self, timeout=None):
        self.waits.append(timeout)
        return False

    def is_set(self):
        return False


def test_pacer_first_request_does_not_block():
    pacer = _Pacer(min_interval=2.0, jitter_ratio=0.0, clock=lambda: 100.0)
    stop = _FakeStop()
    pacer.wait(stop)  # type: ignore[arg-type]
    assert stop.waits == []  # nothing to wait for on the first request


def test_pacer_blocks_for_remaining_interval():
    now = {"t": 100.0}
    pacer = _Pacer(min_interval=2.0, jitter_ratio=0.0, clock=lambda: now["t"])
    stop = _FakeStop()
    pacer.wait(stop)  # primes _last = 100.0
    now["t"] = 100.5  # only 0.5s elapsed, floor is 2.0s
    pacer.wait(stop)  # type: ignore[arg-type]
    assert stop.waits == [1.5]  # waits the remaining 1.5s


def test_pacer_no_block_when_interval_already_elapsed():
    now = {"t": 100.0}
    pacer = _Pacer(min_interval=2.0, jitter_ratio=0.0, clock=lambda: now["t"])
    stop = _FakeStop()
    pacer.wait(stop)
    now["t"] = 103.0  # 3s > 2s floor
    pacer.wait(stop)  # type: ignore[arg-type]
    assert stop.waits == []


def test_pacer_threading_event_compatible():
    # Sanity: real Event works as the wait target (interval not yet elapsed
    # would block, so use a fresh pacer whose first call never blocks).
    pacer = _Pacer(min_interval=0.0, jitter_ratio=0.0)
    pacer.wait(threading.Event())
