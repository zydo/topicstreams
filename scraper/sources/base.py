"""Search-source contract.

Each search engine implements ``SearchSource`` so the scraper runner (browser
navigation, the page/topic loop, logging) stays engine-agnostic. The
engine-specific parts — URL construction, result selectors, block detection —
live in the implementations.
"""

from abc import ABC, abstractmethod
from enum import Enum

from bs4 import BeautifulSoup
from bs4.element import Tag

from common.model import NewsEntry


class Ordering(str, Enum):
    RELEVANCE = "relevance"  # the engine's default ordering
    DATE = "date"  # newest first


class Recency(str, Enum):
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    ANY = "any"


class SearchSource(ABC):
    """A scrapeable search engine's news results."""

    #: Stable engine identifier (e.g. "google"), used in logs.
    name: str
    #: CSS selector to wait for before reading the rendered page.
    ready_selector: str

    @abstractmethod
    def build_url(
        self, topic: str, *, ordering: Ordering, recency: Recency, page: int
    ) -> str:
        """URL for a results page (1-based ``page``)."""

    @abstractmethod
    def find_items(self, soup: BeautifulSoup) -> list[Tag]:
        """The result-item containers on the page."""

    @abstractmethod
    def parse_item(self, item: Tag, topic: str) -> NewsEntry | None:
        """Extract a NewsEntry from one item, or None if it's incomplete."""

    @abstractmethod
    def detect_block(self, final_url: str, html: str) -> str | None:
        """Reason string if the engine blocked/CAPTCHA'd the request, else None."""
