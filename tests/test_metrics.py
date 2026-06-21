"""Unit tests for the per-engine health classification + rate helper.

The SQL aggregation is covered by the integration suite; these exercise the
pure Python derivation that turns an aggregate row into a triage label.
"""

from datetime import datetime, timedelta

import pytest

from api.v1.metrics import (
    _COOLDOWN_STALE_SECONDS,
    _build_engine,
    _empty_engine_row,
    _live_cooldown_seconds,
    _rate,
    classify_engine,
)


def _row(
    *,
    scrapes: int = 10,
    successes: int = 10,
    zero_parse: int = 0,
    failures: int = 0,
    blocked: int = 0,
    last_success: bool = True,
    last_http_status: int | None = 200,
    last_error_message: str | None = None,
):
    return {
        "scrapes": scrapes,
        "successes": successes,
        "zero_parse": zero_parse,
        "failures": failures,
        "blocked": blocked,
        "last_success": last_success,
        "last_http_status": last_http_status,
        "last_error_message": last_error_message,
    }


def test_idle_when_no_scrapes():
    assert (
        classify_engine(_row(scrapes=0, successes=0, last_http_status=None)) == "idle"
    )


def test_blocked_when_latest_is_429():
    assert (
        classify_engine(
            _row(scrapes=5, successes=2, last_success=False, last_http_status=429)
        )
        == "blocked"
    )


def test_blocked_when_latest_is_403_or_503():
    assert (
        classify_engine(
            _row(last_success=False, last_http_status=403, scrapes=1, successes=0)
        )
        == "blocked"
    )
    assert (
        classify_engine(
            _row(last_success=False, last_http_status=503, scrapes=1, successes=0)
        )
        == "blocked"
    )


def test_not_blocked_when_latest_ok_despite_block_history():
    # Five blocks in history, but the most recent scrape succeeded → not blocked.
    r = _row(
        scrapes=10, successes=9, blocked=5, last_success=True, last_http_status=200
    )
    assert classify_engine(r) == "healthy"


def test_blocked_on_connection_closed_without_http_status():
    # Yahoo-style network-style teardown: no HTTP status, but the error message is a
    # connection-level block → "blocked", not "degraded".
    r = _row(
        scrapes=4,
        successes=0,
        last_success=False,
        last_http_status=None,
        last_error_message=(
            "Error: Page.goto: net::ERR_CONNECTION_CLOSED at "
            "https://news.search.yahoo.com/search?p=spacex&b=1"
        ),
    )
    assert classify_engine(r) == "blocked"


def test_timeout_is_not_a_network_block():
    # A navigation timeout is transient, not a block: stays degraded.
    r = _row(
        scrapes=4,
        successes=0,
        last_success=False,
        last_http_status=None,
        last_error_message="TimeoutError: Timeout 30000ms exceeded.",
    )
    assert classify_engine(r) == "degraded"


def test_parsing_when_all_successes_parse_zero():
    r = _row(
        scrapes=6, successes=6, zero_parse=6, last_success=True, last_http_status=200
    )
    assert classify_engine(r) == "parsing"


def test_parsing_requires_at_least_three_scrapes():
    # Only 2 scrapes → too little to call selector rot; rate 1.0 → healthy.
    r = _row(
        scrapes=2, successes=2, zero_parse=2, last_success=True, last_http_status=200
    )
    assert classify_engine(r) == "healthy"


def test_parsing_needs_some_successes():
    # All failed (zero successes) and latest isn't a block code → degraded, not parsing.
    r = _row(
        scrapes=6, successes=0, zero_parse=0, last_success=False, last_http_status=500
    )
    assert classify_engine(r) == "degraded"


def test_degraded_on_low_success_rate():
    assert (
        classify_engine(_row(scrapes=10, successes=5, last_success=True)) == "degraded"
    )


def test_total_non_block_failure_is_degraded():
    assert (
        classify_engine(
            _row(scrapes=4, successes=0, last_success=False, last_http_status=500)
        )
        == "degraded"
    )


def test_healthy_at_high_success_rate():
    assert classify_engine(_row(scrapes=10, successes=9)) == "healthy"


def test_rate_helper():
    assert _rate(8, 10) == pytest.approx(0.8)
    assert _rate(0, 0) is None
    assert _rate(3, 4) == pytest.approx(0.75)


# ── _live_cooldown_seconds ───────────────────────────────────────────────────

_NOW = datetime(2026, 6, 18, 12, 0, 0)


def _cd(*, failures=1, probe_in=300, updated_ago=5):
    """A cooldown row: probe `probe_in`s from _NOW, snapshot `updated_ago`s old."""
    return {
        "failures": failures,
        "next_probe_at": _NOW + timedelta(seconds=probe_in),
        "updated_at": _NOW - timedelta(seconds=updated_ago),
    }


def test_cooldown_live_when_benched_and_fresh():
    assert _live_cooldown_seconds(_cd(probe_in=286), _NOW) == pytest.approx(286)


def test_cooldown_none_when_not_cooling():
    assert _live_cooldown_seconds(_cd(failures=0), _NOW) is None
    assert _live_cooldown_seconds(None, _NOW) is None
    assert (
        _live_cooldown_seconds(
            {"failures": 1, "next_probe_at": None, "updated_at": _NOW}, _NOW
        )
        is None
    )


def test_cooldown_none_when_probe_already_due():
    # next_probe_at in the past: the engine probes next cycle, not benched now.
    assert _live_cooldown_seconds(_cd(probe_in=-10), _NOW) is None


def test_cooldown_none_when_snapshot_stale():
    # Scraper hasn't refreshed the snapshot recently → ignore (likely down).
    stale = _cd(probe_in=300, updated_ago=_COOLDOWN_STALE_SECONDS + 60)
    assert _live_cooldown_seconds(stale, _NOW) is None


# ── _build_engine: backlog + "behind" health ─────────────────────────────────


def _erow(**over):
    """A healthy per-engine aggregate row (overridable)."""
    row = _empty_engine_row("google")
    row.update(scrapes=10, successes=10, last_success=True, last_http_status=200)
    row.update(over)
    return row


def _backlog(overdue, lateness):
    return {"overdue_count": overdue, "max_lateness_seconds": lateness}


def test_engine_flagged_behind_when_oldest_topic_too_late():
    e = _build_engine(_erow(), backlog=_backlog(5, 300.0), behind_threshold=180.0)
    assert e.health == "behind"
    assert e.backlog_overdue == 5
    assert e.backlog_lateness_seconds == 300.0


def test_engine_not_behind_under_threshold_but_numbers_surface():
    e = _build_engine(_erow(), backlog=_backlog(2, 60.0), behind_threshold=180.0)
    assert e.health == "healthy"
    assert e.backlog_overdue == 2  # the count is still reported
    assert e.backlog_lateness_seconds == 60.0


def test_cooldown_takes_precedence_over_behind():
    e = _build_engine(
        _erow(scrapes=0, successes=0),
        cooldown={"remaining": 100.0, "failures": 2},
        backlog=_backlog(9, 999.0),
        behind_threshold=180.0,
    )
    assert e.health == "cooldown"
    assert e.backlog_overdue == 9  # backlog still reported alongside


def test_behind_does_not_mask_a_blocked_engine():
    blocked = _erow(successes=0, last_success=False, last_http_status=429)
    e = _build_engine(blocked, backlog=_backlog(9, 999.0), behind_threshold=180.0)
    assert e.health == "blocked"


def test_no_backlog_defaults_to_zero():
    e = _build_engine(_erow())
    assert e.health == "healthy"
    assert e.backlog_overdue == 0
    assert e.backlog_lateness_seconds == 0.0
