"""DuckDuckGo News search source.

CAVEAT: DuckDuckGo aggressively gates automated access. Its news vertical is
rendered client-side after a token (``vqd``) handshake, and the no-JS HTML
endpoint returns an "anomaly" challenge to datacenter clients. This source
targets DDG's news SERP markup (``article[data-testid="result"]`` with a
``result-title-a`` link), but unlike the Yahoo/Brave sources it could not be
live-validated from our infrastructure — so it ships disabled by default
(``scraper.engines.enabled`` does not list it). Enable it in production, where
the fingerprinted browser may clear the challenge, and the parse-0 health
signal will flag it if the markup has drifted.
"""

import re

from bs4 import BeautifulSoup
from bs4.element import Tag

from common.model import NewsEntry

from .base import Ordering, Recency, SearchSource

# DDG "df" date-filter codes.
_RECENCY_DF = {
    Recency.HOUR: "d",  # No 1-hour filter; d = past day is closest.
    Recency.DAY: "d",
    Recency.WEEK: "w",
    Recency.MONTH: "m",
}

# Challenge / rate-limit pages DDG serves to suspected bots.
_BLOCK_MARKERS = ("anomaly", "if this error persists")


class DuckDuckGoSource(SearchSource):
    name = "duckduckgo"
    ready_selector = "article[data-testid='result'], .results--news"

    def build_url(
        self, topic: str, *, ordering: Ordering, recency: Recency, page: int
    ) -> str:
        q = re.sub(r"\s+", "+", topic.strip())
        params = [f"q={q}", "iar=news", "ia=news"]
        df = _RECENCY_DF.get(recency)
        if df:
            params.append(f"df={df}")
        # DDG offers no date-sort parameter; results are relevance-ranked.
        return "https://duckduckgo.com/?" + "&".join(params)

    def find_items(self, soup: BeautifulSoup) -> list[Tag]:
        for selector in ("article[data-testid='result']", "div.result--news"):
            items = soup.select(selector)
            if items:
                return items
        return []

    def parse_item(self, item: Tag, topic: str) -> NewsEntry | None:
        link = item.select_one("a[data-testid='result-title-a']") or item.select_one(
            "a.result__a"
        )
        if link is None:
            return None
        title = link.get_text(strip=True)
        url = link.get("href")
        if not title or not url:
            return None

        source_el = item.select_one(
            "[data-testid='result-extras-url-link']"
        ) or item.select_one(".result__url")
        source = source_el.get_text(strip=True) if source_el else None

        return NewsEntry.create_new(
            topic=topic, title=title, url=str(url).strip(), source=source or None
        )

    def detect_block(self, final_url: str, html: str) -> str | None:
        lowered = html.lower()
        for marker in _BLOCK_MARKERS:
            if marker in lowered:
                return f"challenge page ({marker})"
        return None
