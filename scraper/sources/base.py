"""Search-source contract.

Each search engine implements ``SearchSource`` so the scraper runner (browser
navigation, the page/topic loop, logging) stays engine-agnostic. The
engine-specific parts — URL construction, result selectors, block detection —
live in the implementations.
"""

from abc import ABC, abstractmethod
from enum import Enum
from urllib.parse import urlsplit

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
    #: Registrable host suffix and path prefix of this engine's results page.
    #: When both are set, the runner treats a navigation that lands off them as
    #: a block (a generic backup to ``detect_block`` — catches /sorry/-style
    #: redirects). Leave ``results_host`` None to opt out.
    results_host: str | None = None
    results_path_prefix: str = "/"

    def redirected_off_results(self, final_url: str) -> str | None:
        """Generic block signal: the final URL isn't this engine's results page.

        A blocked request is often redirected to a challenge/notice page (e.g.
        Google's ``/sorry/``). If, after navigation, the host or path no longer
        matches the configured results location, that's a block — and we'd parse
        0 items anyway, so flagging it is strictly more informative.
        """
        if not self.results_host:
            return None
        parts = urlsplit(final_url)
        host = parts.netloc.lower()
        host_ok = host == self.results_host or host.endswith("." + self.results_host)
        if host_ok and parts.path.startswith(self.results_path_prefix):
            return None
        return f"redirected off results page to {host}{parts.path}"

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
