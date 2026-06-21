"""Search-source contract.

The engine-specific parts of scraping split along two axes so the runner
(browser navigation, the page/topic loop, logging) stays engine-agnostic:

- **Per engine, vertical-agnostic** — identity and navigation block/redirect
  detection, on ``SearchSource``.
- **Per (engine × vertical)** — URL construction, the ready selector, result
  selectors, and item parsing, on a ``ResultParser`` the source exposes via
  ``parser_for``.

A request is described canonically by ``SearchRequest`` (modeled on Google's
search params, the most expressive). Each engine's parser maps the canonical
request onto its own URL and silently drops dimensions it can't express (e.g.
Yahoo has no date sort). Today only the NEWS vertical is implemented;
``SearchVertical.WEB`` is reserved so a general web-search parser can slot in
without touching this contract.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from bs4.element import Tag

from common.model import NewsEntry, WebResult


class Ordering(str, Enum):
    RELEVANCE = "relevance"  # the engine's default ordering
    DATE = "date"  # newest first


class Recency(str, Enum):
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    ANY = "any"


class SearchVertical(str, Enum):
    NEWS = "news"
    # Reserved extension point: no engine implements a web parser yet. Adding one
    # is a new ResultParser plus one line in that engine's _build_parsers.
    WEB = "web"


@dataclass(frozen=True)
class SearchRequest:
    """Canonical, engine-agnostic description of one results page to fetch.

    Modeled on Google's search params: ``sort`` → ``sbd:1``, ``recency`` →
    ``qdr``, ``vertical`` → ``tbm``. Each engine's parser maps these onto its own
    URL and drops what it can't express.
    """

    query: str
    page: int = 1
    sort: Ordering = Ordering.DATE
    recency: Recency = Recency.HOUR
    vertical: SearchVertical = SearchVertical.NEWS


def format_query(query: str) -> str:
    """Collapse whitespace to '+' for a URL query value ('us iran' → 'us+iran')."""
    return re.sub(r"\s+", "+", query.strip())


class ResultParser(ABC):
    """Per-(engine × vertical) strategy: how to request and read one vertical's
    results for one engine.

    Everything that differs by vertical lives here — the URL, the wait selector,
    the result-item containers, and item parsing — so adding a vertical to an
    engine is a new ``ResultParser`` plus one line in the source's
    ``_build_parsers``. The NEWS parsers return ``NewsEntry``; a future WEB parser
    will return its own result type (widening ``parse``'s return).
    """

    #: CSS selector to wait for before reading the rendered results page.
    ready_selector: str

    @abstractmethod
    def build_url(self, request: SearchRequest) -> str:
        """URL for the requested results page (1-based ``request.page``)."""

    @abstractmethod
    def find_items(self, soup: BeautifulSoup) -> list[Tag]:
        """The result-item containers on the page."""

    @abstractmethod
    def parse(self, item: Tag, request: SearchRequest) -> NewsEntry | WebResult | None:
        """Extract a result from one item, or None if it's incomplete.

        NEWS parsers return ``NewsEntry``; the WEB parser returns ``WebResult``
        (uniform across the SERP's heterogeneous components)."""


class SearchSource(ABC):
    """A scrapeable search engine.

    Holds the engine's identity and the vertical-agnostic navigation concerns
    (block/redirect detection), and exposes a ``ResultParser`` per supported
    vertical. The runner picks the parser for the request's vertical and drives
    it (see scraper/scraper.py).
    """

    #: Stable engine identifier (e.g. "google"), used in logs.
    name: str
    #: Registrable host suffix and path prefix of this engine's results page.
    #: When both are set, the runner treats a navigation that lands off them as
    #: a block (a generic backup to ``detect_block`` — catches /sorry/-style
    #: redirects). Leave ``results_host`` None to opt out.
    results_host: str | None = None
    results_path_prefix: str = "/"

    def __init__(self) -> None:
        self._parsers = self._build_parsers()

    @abstractmethod
    def _build_parsers(self) -> dict[SearchVertical, "ResultParser"]:
        """The verticals this engine supports, mapped to their parsers."""

    @property
    def verticals(self) -> frozenset[SearchVertical]:
        """Verticals this engine can scrape."""
        return frozenset(self._parsers)

    def parser_for(self, vertical: SearchVertical) -> ResultParser:
        """The parser for ``vertical``, or ValueError if unsupported."""
        try:
            return self._parsers[vertical]
        except KeyError:
            raise ValueError(
                f"{self.name} does not support the '{vertical.value}' vertical "
                f"(has: {sorted(v.value for v in self._parsers)})"
            )

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
    def detect_block(self, final_url: str, html: str) -> str | None:
        """Reason string if the engine blocked/CAPTCHA'd the request, else None."""
