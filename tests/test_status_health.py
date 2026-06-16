"""Tests for the server-side scrape-health computation (incl. selector rot)."""

from datetime import datetime, timedelta, timezone

from api.v1.status import _fail_reason, _stale_threshold_s, compute_health
from common.model import ScraperLog

ACTIVE = {"a", "b", "c"}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _log(topic, ok=True, n=10, ago_s=0, code=200, err=None):
    return ScraperLog(
        topic=topic,
        success=ok,
        scraped_at=_now() - timedelta(seconds=ago_s),
        http_status_code=code,
        error_message=err,
        entry_count=n,
    )


def test_idle_when_no_logs():
    assert compute_health([], ACTIVE)[0] == "idle"


def test_live_when_success_with_entries():
    logs = [_log(t, ago_s=i * 5) for i, t in enumerate(["a", "b", "c", "a", "b", "c"])]
    assert compute_health(logs, ACTIVE)[0] == "live"


def test_parsing_when_success_but_zero_entries():
    logs = [
        _log(t, n=0, ago_s=i * 5) for i, t in enumerate(["a", "b", "c", "a", "b", "c"])
    ]
    state, label, _ = compute_health(logs, ACTIVE)
    assert state == "parsing"
    assert label == "no items"


def test_quiet_hour_one_zero_topic_is_not_parsing():
    # One topic parses 0 but others have entries -> not selector rot.
    logs = [_log("a", n=0, ago_s=1), _log("b", n=8, ago_s=2), _log("c", n=5, ago_s=3)]
    assert compute_health(logs, ACTIVE)[0] == "live"


def test_errors_when_all_latest_failed():
    logs = [_log(t, ok=False, n=0, code=429, err="blocked") for t in ["a", "b", "c"]]
    assert compute_health(logs, ACTIVE)[0] == "errors"


def test_degraded_when_one_topic_failing():
    logs = [
        _log("a", ok=False, n=0, ago_s=1, code=429, err="x"),
        _log("b", ago_s=2),
        _log("c", ago_s=3),
    ]
    assert compute_health(logs, ACTIVE)[0] == "degraded"


def test_stalled_when_last_scrape_old():
    logs = [_log("a", ago_s=2400), _log("a", ago_s=2405), _log("a", ago_s=2410)]
    assert compute_health(logs, ACTIVE)[0] == "stalled"


def test_stale_threshold_clamped_to_floor():
    logs = [_log("a", ago_s=0), _log("a", ago_s=5), _log("a", ago_s=10)]
    assert _stale_threshold_s(logs) == 5 * 60


def test_stale_threshold_default_with_one_log():
    assert _stale_threshold_s([_log("a")]) == 15 * 60


def test_fail_reason_prefers_message_then_status():
    assert _fail_reason(_log("a", err="boom")) == "boom"
    assert _fail_reason(_log("a", err=None, code=503)) == "HTTP 503"
