"""Tests for adaptive per-engine cooldown (scraper/cooldown.py) and its
integration with scrape_topic."""

from types import SimpleNamespace

import pytest

import scraper.scraper as scraper_mod
from common.model import NewsEntry, ScraperLog
from scraper.cooldown import EngineCooldownTracker, classify_logs
from scraper.scraper import scrape_topic


class _Clock:
    """Manually advanced monotonic clock."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def _tracker(clock, base=300, cap=3600):
    return EngineCooldownTracker(base_seconds=base, max_seconds=cap, _clock=clock)


def _log(engine, ok=True, n=1, status=None, error=None):
    return ScraperLog.create_new(
        topic="t",
        success=ok,
        entry_count=n,
        engine=engine,
        http_status_code=status,
        error_message=error,
    )


# --- classify_logs --------------------------------------------------------


def test_classify_block_wins_over_success():
    logs = [_log("g", ok=True, n=1), _log("g", ok=False, status=429)]
    assert classify_logs(logs) == "block"


def test_classify_block_from_message():
    logs = [_log("g", ok=False, error="google blocked: /sorry/ redirect")]
    assert classify_logs(logs) == "block"


def test_classify_ok_when_any_success():
    assert classify_logs([_log("g", ok=True, n=0)]) == "ok"


def test_classify_other_for_non_block_failure():
    logs = [_log("g", ok=False, error="TimeoutError: navigation timed out")]
    assert classify_logs(logs) == "other"


def test_classify_block_from_connection_closed():
    # Yahoo-style network teardown (no HTTP status) counts as a block, so the
    # engine gets benched instead of retried every cycle.
    logs = [
        _log(
            "yahoo",
            ok=False,
            error="Error: Page.goto: net::ERR_CONNECTION_CLOSED at https://y/search",
        )
    ]
    assert classify_logs(logs) == "block"


# --- tracker decide/record ------------------------------------------------


def test_healthy_engine_always_runs():
    tracker = _tracker(_Clock())
    assert tracker.decide("g") == "run"
    tracker.record("g", [_log("g", ok=True, n=2)])
    assert tracker.decide("g") == "run"


def test_block_benches_then_probes_after_window():
    clock = _Clock()
    tracker = _tracker(clock, base=300)
    tracker.record("g", [_log("g", ok=False, status=429)])

    assert tracker.decide("g") == "skip"
    assert 0 < tracker.remaining("g") <= 300

    clock.advance(299)
    assert tracker.decide("g") == "skip"
    clock.advance(2)  # past the 300s window
    assert tracker.decide("g") == "probe"


def test_clean_probe_clears_cooldown():
    clock = _Clock()
    tracker = _tracker(clock, base=300)
    tracker.record("g", [_log("g", ok=False, status=429)])
    clock.advance(301)
    assert tracker.decide("g") == "probe"

    tracker.record("g", [_log("g", ok=True, n=3)])
    assert tracker.decide("g") == "run"
    assert tracker.remaining("g") == pytest.approx(0.0)


def test_repeated_block_doubles_window_up_to_cap():
    clock = _Clock()
    tracker = _tracker(clock, base=300, cap=1000)
    windows = []
    for _ in range(4):
        tracker.record("g", [_log("g", ok=False, status=503)])
        windows.append(round(tracker.remaining("g")))
        clock.advance(tracker.remaining("g") + 1)  # expire so the next is a probe
    # 300, 600, capped at 1000, capped at 1000.
    assert windows == [300, 600, 1000, 1000]


def test_transient_failure_during_probe_rearms_without_deepening():
    clock = _Clock()
    tracker = _tracker(clock, base=300)
    tracker.record("g", [_log("g", ok=False, status=429)])  # failures=1, window 300
    clock.advance(301)
    assert tracker.decide("g") == "probe"

    # Probe times out (not a block): same depth, but re-armed so we don't retry
    # on every subsequent topic this cycle.
    tracker.record("g", [_log("g", ok=False, error="TimeoutError")])
    assert tracker.decide("g") == "skip"
    assert round(tracker.remaining("g")) == 300


# --- snapshot -------------------------------------------------------------


def test_snapshot_reports_failures_and_remaining():
    clock = _Clock()
    tracker = _tracker(clock, base=300)
    tracker.record("yahoo", [_log("yahoo", ok=False, status=503)])
    tracker.record("google", [_log("google", ok=True, n=2)])

    snap = {s.engine: s for s in tracker.snapshot()}
    assert set(snap) == {"yahoo", "google"}

    assert snap["yahoo"].failures == 1
    assert 0 < snap["yahoo"].remaining_seconds <= 300

    # A healthy engine is still reported, but not cooling.
    assert snap["google"].failures == 0
    assert snap["google"].remaining_seconds == pytest.approx(0.0)


def test_snapshot_empty_before_any_activity():
    assert _tracker(_Clock()).snapshot() == []


# --- scrape_topic integration ---------------------------------------------


def _source(name):
    return SimpleNamespace(name=name)


def _entry(name):
    return NewsEntry.create_new(topic="t", title=name, url=f"https://x.com/{name}")


class _PageFactory:
    def __init__(self):
        self.count = 0

    def __call__(self):
        self.count += 1
        return SimpleNamespace(close=lambda: None)


def _patch_scrape_news(monkeypatch, behavior):
    calls = []

    def fake(page, source, topic, **kwargs):
        calls.append(source.name)
        return behavior[source.name]

    monkeypatch.setattr(scraper_mod, "scrape_news", fake)
    return calls


def test_scrape_topic_skips_cooling_engine_and_falls_through(monkeypatch):
    clock = _Clock()
    tracker = _tracker(clock, base=300)
    tracker.record("google", [_log("google", ok=False, status=429)])  # cooling

    sources = [_source("google"), _source("bing")]
    calls = _patch_scrape_news(
        monkeypatch,
        {
            "google": ([_entry("g")], [_log("google", n=1)]),
            "bing": ([_entry("b")], [_log("bing", n=1)]),
        },
    )
    factory = _PageFactory()
    entries, _logs = scrape_topic(
        factory, sources, "t", strategy="fallback", cooldown=tracker  # type: ignore[arg-type]
    )
    # google is benched, so fallback goes straight to bing (no page opened for
    # google).
    assert calls == ["bing"]
    assert [e.title for e in entries] == ["b"]
    assert factory.count == 1


def test_scrape_topic_records_block_into_tracker(monkeypatch):
    clock = _Clock()
    tracker = _tracker(clock, base=300)
    sources = [_source("google")]
    _patch_scrape_news(
        monkeypatch,
        {"google": ([], [_log("google", ok=False, status=429)])},
    )
    scrape_topic(_PageFactory(), sources, "t", strategy="all", cooldown=tracker)  # type: ignore[arg-type]

    # The 429 observed during the scrape benches google for the next call.
    assert tracker.decide("google") == "skip"


def test_scrape_topic_without_cooldown_is_unchanged(monkeypatch):
    sources = [_source("google"), _source("bing")]
    calls = _patch_scrape_news(
        monkeypatch,
        {
            "google": ([], [_log("google", ok=False, status=429)]),
            "bing": ([_entry("b")], [_log("bing", n=1)]),
        },
    )
    scrape_topic(_PageFactory(), sources, "t", strategy="fallback")  # type: ignore[arg-type]
    # No tracker: behaves as before — google fails, fallback advances to bing.
    assert calls == ["google", "bing"]
