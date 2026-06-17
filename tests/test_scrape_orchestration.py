"""Tests for multi-engine orchestration (scrape_topic / engine selection).

These exercise the strategy logic without Playwright by stubbing out the
per-engine ``scrape_news`` call.
"""

from types import SimpleNamespace

import scraper.scraper as scraper_mod
from common.model import NewsEntry, ScraperLog
from scraper.scraper import _engines_for_cycle, scrape_topic


def _source(name):
    return SimpleNamespace(name=name)


def _entry(name):
    return NewsEntry.create_new(topic="t", title=name, url=f"https://x.com/{name}")


def _log(engine, ok=True, n=1):
    return ScraperLog.create_new(topic="t", success=ok, entry_count=n, engine=engine)


class _FakePage:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _PageFactory:
    """Hands out fake pages and remembers them so we can assert cleanup."""

    def __init__(self):
        self.pages = []

    def __call__(self):
        page = _FakePage()
        self.pages.append(page)
        return page


def _patch_scrape_news(monkeypatch, behavior):
    """Stub scrape_news; ``behavior`` maps engine name -> (entries, logs)."""
    calls = []

    def fake(page, source, topic, **kwargs):
        calls.append(source.name)
        return behavior[source.name]

    monkeypatch.setattr(scraper_mod, "scrape_news", fake)
    return calls


# --- engine selection -----------------------------------------------------


def test_all_and_fallback_consider_every_engine():
    sources = [_source("google"), _source("bing")]
    assert _engines_for_cycle(sources, "all", cycle=3) == sources
    assert _engines_for_cycle(sources, "fallback", cycle=3) == sources


def test_rotate_picks_one_engine_per_cycle():
    sources = [_source("google"), _source("bing")]
    assert [_engines_for_cycle(sources, "rotate", c)[0].name for c in range(4)] == [
        "google",
        "bing",
        "google",
        "bing",
    ]


def test_rotate_with_empty_sources_is_safe():
    assert _engines_for_cycle([], "rotate", cycle=1) == []


# --- scrape_topic ---------------------------------------------------------


def test_fallback_stops_after_first_engine_with_items(monkeypatch):
    sources = [_source("google"), _source("bing")]
    calls = _patch_scrape_news(
        monkeypatch,
        {
            "google": ([_entry("g")], [_log("google", n=1)]),
            "bing": ([_entry("b")], [_log("bing", n=1)]),
        },
    )
    factory = _PageFactory()
    entries, logs = scrape_topic(factory, sources, "t", strategy="fallback")

    assert calls == ["google"]  # bing never reached
    assert [e.title for e in entries] == ["g"]
    assert len(factory.pages) == 1 and factory.pages[0].closed


def test_fallback_advances_when_first_engine_is_empty(monkeypatch):
    sources = [_source("google"), _source("bing")]
    calls = _patch_scrape_news(
        monkeypatch,
        {
            "google": ([], [_log("google", ok=True, n=0)]),
            "bing": ([_entry("b")], [_log("bing", n=1)]),
        },
    )
    entries, logs = scrape_topic(_PageFactory(), sources, "t", strategy="fallback")

    assert calls == ["google", "bing"]
    assert [e.title for e in entries] == ["b"]
    assert {log.engine for log in logs} == {"google", "bing"}


def test_fallback_advances_when_first_engine_fails(monkeypatch):
    sources = [_source("google"), _source("bing")]
    calls = _patch_scrape_news(
        monkeypatch,
        {
            "google": ([], [_log("google", ok=False, n=0)]),
            "bing": ([_entry("b")], [_log("bing", n=1)]),
        },
    )
    scrape_topic(_PageFactory(), sources, "t", strategy="fallback")
    assert calls == ["google", "bing"]


def test_all_runs_every_engine_even_when_first_succeeds(monkeypatch):
    sources = [_source("google"), _source("bing")]
    calls = _patch_scrape_news(
        monkeypatch,
        {
            "google": ([_entry("g")], [_log("google", n=1)]),
            "bing": ([_entry("b")], [_log("bing", n=1)]),
        },
    )
    factory = _PageFactory()
    entries, _ = scrape_topic(factory, sources, "t", strategy="all")

    assert calls == ["google", "bing"]
    assert {e.title for e in entries} == {"g", "b"}
    assert len(factory.pages) == 2 and all(p.closed for p in factory.pages)


def test_rotate_uses_a_single_engine_for_the_cycle(monkeypatch):
    sources = [_source("google"), _source("bing")]
    calls = _patch_scrape_news(
        monkeypatch,
        {
            "google": ([_entry("g")], [_log("google", n=1)]),
            "bing": ([_entry("b")], [_log("bing", n=1)]),
        },
    )
    scrape_topic(_PageFactory(), sources, "t", strategy="rotate", cycle=1)
    assert calls == ["bing"]
