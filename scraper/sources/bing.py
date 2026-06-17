"""Bing News search source.

Bing's news cards carry the article URL, title, and source as attributes on the
card element (``data-url`` / ``data-title`` / ``data-author``), so extraction is
attribute-based — more robust than nested text selectors.
"""

import re

from bs4 import BeautifulSoup
from bs4.element import Tag

from common.model import NewsEntry

from .base import Ordering, Recency, SearchSource

# Bing News freshness codes for the qft `interval` filter, verified 2026-06-17
# against bing.com/news/search?...&qft=interval%3d"<N>":
#   "4" -> past hour
#   "7" -> past 24 hours
#   "8" -> past day (Bing's label for its broader "recent" window)
#   "9" -> past 30 days
# We scrape the past hour to match the rest of the pipeline (Google's qdr:h), so
# only HOUR is exercised today. The DAY/WEEK/MONTH rows are wired for when we
# expose other windows; revisit "8" if a dedicated 7-day code turns up.
_RECENCY_INTERVAL = {
    Recency.HOUR: "4",
    Recency.DAY: "7",
    Recency.WEEK: "8",
    Recency.MONTH: "9",
}

_ITEM_SELECTORS = ("div.news-card.newsitem", "div.newsitem")


class BingSource(SearchSource):
    name = "bing"
    ready_selector = "div.newsitem, #algocore"
    results_host = "bing.com"
    results_path_prefix = "/news"

    def build_url(
        self, topic: str, *, ordering: Ordering, recency: Recency, page: int
    ) -> str:
        q = re.sub(r"\s+", "+", topic.strip())
        first = (page - 1) * 10 + 1  # Bing paginates by 1-based result offset
        params = [f"q={q}", f"first={first}"]
        # Date sorting and freshness are both expressed as qft filter tokens
        # (e.g. qft=interval%3d"7"+sortbydate%3d"1").
        qft: list[str] = []
        interval = _RECENCY_INTERVAL.get(recency)
        if interval:
            qft.append(f'interval%3d"{interval}"')
        if ordering is Ordering.DATE:
            qft.append('sortbydate%3d"1"')
        if qft:
            params.append("qft=" + "+".join(qft))
        return "https://www.bing.com/news/search?" + "&".join(params)

    def find_items(self, soup: BeautifulSoup) -> list[Tag]:
        for selector in _ITEM_SELECTORS:
            items = soup.select(selector)
            if items:
                return items
        return []

    def parse_item(self, item: Tag, topic: str) -> NewsEntry | None:
        title = item.get("data-title") or item.get("title")
        url = item.get("data-url") or item.get("url")
        if not title or not url:
            return None
        return NewsEntry.create_new(
            topic=topic,
            title=str(title).strip(),
            url=str(url).strip(),
            source=(item.get("data-author") or None),
        )

    def detect_block(self, final_url: str, html: str) -> str | None:
        # Bing news search has no clear block signal; rely on HTTP status codes
        # and the parse-0 health signal. Refine if blocks are observed.
        return None
