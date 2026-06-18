"""Tests for the news feed cursor helper."""

from api.v1.news import _next_cursor
from common.model import NewsEntry


def _entries(ids):
    return [
        NewsEntry(
            id=i,
            topic="t",
            title="T",
            url="https://x.com",
            domain="x.com",
            source=None,
            snippet=None,
            engine=None,
            scraped_at=None,
        )
        for i in ids
    ]


def test_full_page_returns_last_id():
    # A full page (len == limit) means more may remain; cursor = last id.
    assert _next_cursor(_entries([5, 4, 3]), limit=3) == 3


def test_short_page_returns_none():
    # Fewer than `limit` means we reached the earliest entry.
    assert _next_cursor(_entries([5, 4]), limit=3) is None


def test_empty_returns_none():
    assert _next_cursor([], limit=3) is None
