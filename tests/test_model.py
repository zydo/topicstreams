"""Tests for the NewsEntry and ScraperLog models."""

from datetime import datetime

from common.model import NewsEntry, ScraperLog


class TestNewsEntry:
    def test_domain_extraction_strips_www(self):
        e = NewsEntry.create_new(topic="t", title="T", url="https://www.example.com/x")
        assert e.domain == "example.com"

    def test_domain_keeps_subdomain(self):
        e = NewsEntry.create_new(topic="t", title="T", url="https://news.bbc.co.uk/x")
        assert e.domain == "news.bbc.co.uk"

    def test_create_new_defaults(self):
        e = NewsEntry.create_new(topic="t", title="T", url="https://x.com")
        assert e.id is None
        assert e.scraped_at is None
        assert e.source is None
        assert e.engine is None  # scrape-side engine unset until stamped
        assert e.engines == []  # feed-side aggregate empty off the wire

    def test_create_new_with_engine(self):
        e = NewsEntry.create_new(
            topic="t", title="T", url="https://x.com", engine="bing"
        )
        assert e.engine == "bing"

    def test_from_db_row(self):
        row = {
            "id": 7,
            "topic": "t",
            "title": "Headline",
            "url": "https://x.com",
            "domain": "x.com",
            "source": "Wire",
            "scraped_at": datetime(2026, 1, 1),
        }
        e = NewsEntry.from_db_row(row)
        assert e.id == 7
        assert e.source == "Wire"


class TestScraperLog:
    def test_create_new_defaults(self):
        log = ScraperLog.create_new(topic="t", success=True)
        assert log.entry_count == 0
        assert log.success is True
        assert log.http_status_code is None
        assert log.scraped_at is not None
        assert log.engine == "google"

    def test_entry_count_and_engine_recorded(self):
        log = ScraperLog.create_new(
            topic="t", success=True, entry_count=10, engine="bing"
        )
        assert log.entry_count == 10
        assert log.engine == "bing"
