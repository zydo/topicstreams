"""Yahoo News search source.

Yahoo serves a server-rendered news vertical at ``news.search.yahoo.com``.
Result links are wrapped in a Yahoo redirector (``r.search.yahoo.com/.../RU=<url>/``),
so we unwrap the real target from the ``RU=`` segment.
"""

import re
from urllib.parse import unquote

from bs4 import BeautifulSoup
from bs4.element import Tag

from common.model import NewsEntry

from .base import Ordering, Recency, SearchSource

# The real destination is URL-encoded between ``/RU=`` and the next ``/``.
_REDIRECT_RE = re.compile(r"/RU=([^/]+)/")


class YahooSource(SearchSource):
    name = "yahoo"
    ready_selector = "ol.searchCenterMiddle, #web"
    results_host = "yahoo.com"  # news.search.yahoo.com
    results_path_prefix = "/search"

    def build_url(
        self, topic: str, *, ordering: Ordering, recency: Recency, page: int
    ) -> str:
        q = re.sub(r"\s+", "+", topic.strip())
        # Yahoo paginates by 1-based result offset (b=1, 11, 21, ...).
        b = (page - 1) * 10 + 1
        # Yahoo news search exposes no reliable date-sort or freshness
        # parameter, so `ordering`/`recency` are not applied here (results are
        # engine-default ranked). Date sort is a nice-to-have left for further
        # exploration; a short scrape interval keeps the feed fresh regardless.
        return f"https://news.search.yahoo.com/search?p={q}&b={b}"

    def find_items(self, soup: BeautifulSoup) -> list[Tag]:
        return soup.select("ol.searchCenterMiddle li")

    def parse_item(self, item: Tag, topic: str) -> NewsEntry | None:
        link = item.select_one("h4.s-title a") or item.select_one("h4 a")
        if link is None:
            return None
        title = link.get_text(strip=True)
        href = link.get("href")
        if not title or not href:
            return None

        match = _REDIRECT_RE.search(str(href))
        url = unquote(match.group(1)) if match else str(href)

        source_el = item.select_one("span.s-source")
        source = source_el.get_text(strip=True) if source_el else None
        if source:
            # e.g. "BeInCrypto·  via Yahoo Finance" -> "BeInCrypto".
            source = source.split("·")[0].strip() or None

        return NewsEntry.create_new(
            topic=topic, title=title, url=url.strip(), source=source
        )

    def detect_block(self, final_url: str, html: str) -> str | None:
        return None
