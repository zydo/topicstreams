"""Tests for the weighted exit-IP saturation signal and the proactive pacer."""

import threading

from scraper.cooldown import CooldownSnapshot
from scraper.saturation import (
    SharedEngineState,
    evaluate_saturation,
    log_saturation,
)
from scraper.worker import _Pacer


def _snap(engine: str, failures: int) -> CooldownSnapshot:
    return CooldownSnapshot(engine=engine, failures=failures, remaining_seconds=0.0)


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


# --- SharedEngineState ----------------------------------------------------


def test_shared_state_keeps_latest_per_engine():
    state = SharedEngineState()
    state.update(_snap("google", 1))
    state.update(_snap("google", 3))  # overwrites
    state.update(_snap("bing", 0))
    by_engine = {s.engine: s.failures for s in state.all()}
    assert by_engine == {"google": 3, "bing": 0}


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
