"""Unit tests for the per-engine health classification + rate helper.

The SQL aggregation is covered by the integration suite; these exercise the
pure Python derivation that turns an aggregate row into a triage label.
"""

from api.v1.metrics import _rate, classify_engine


def _row(
    *,
    scrapes=10,
    successes=10,
    zero_parse=0,
    failures=0,
    blocked=0,
    last_success=True,
    last_http_status=200,
):
    return {
        "scrapes": scrapes,
        "successes": successes,
        "zero_parse": zero_parse,
        "failures": failures,
        "blocked": blocked,
        "last_success": last_success,
        "last_http_status": last_http_status,
    }


def test_idle_when_no_scrapes():
    assert (
        classify_engine(
            _row(scrapes=0, successes=0, last_success=None, last_http_status=None)
        )
        == "idle"
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
    assert _rate(8, 10) == 0.8
    assert _rate(0, 0) is None
    assert _rate(3, 4) == 0.75
