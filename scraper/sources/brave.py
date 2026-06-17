"""Brave Search news source.

Brave's news vertical (``search.brave.com/news``) renders each result as a
``div.snippet[data-type="news"]`` whose first anchor links directly to the
article (no redirector). The publisher name and relative time share a
``.site-name`` element ("CoinDesk•18 hours ago").
"""

import re

from bs4 import BeautifulSoup
from bs4.element import Tag

from common.model import NewsEntry

from .base import Ordering, Recency, SearchSource

# Brave "freshness" codes for the tf query parameter.
_RECENCY_TF = {
    Recency.HOUR: "pd",  # Brave has no 1-hour filter; pd = past day is closest.
    Recency.DAY: "pd",
    Recency.WEEK: "pw",
    Recency.MONTH: "pm",
}


class BraveSource(SearchSource):
    name = "brave"
    ready_selector = "div.snippet[data-type='news'], #news-results"
    results_host = "brave.com"  # search.brave.com
    results_path_prefix = "/news"

    def build_url(
        self, topic: str, *, ordering: Ordering, recency: Recency, page: int
    ) -> str:
        q = re.sub(r"\s+", "+", topic.strip())
        params = [f"q={q}"]
        if page > 1:
            params.append(f"offset={page - 1}")
        tf = _RECENCY_TF.get(recency)
        if tf:
            params.append(f"tf={tf}")
        # Brave news has no documented date-sort parameter, so `ordering` is
        # not applied (results stay relevance-ranked); only recency filtering
        # via `tf` is honored. Date sort is a nice-to-have left for further
        # exploration.
        return "https://search.brave.com/news?" + "&".join(params)

    def find_items(self, soup: BeautifulSoup) -> list[Tag]:
        return soup.select("div.snippet[data-type='news']")

    def parse_item(self, item: Tag, topic: str) -> NewsEntry | None:
        link = item.select_one("a[href]")
        title_el = item.select_one(".title")
        if link is None or title_el is None:
            return None
        url = link.get("href")
        title = title_el.get_text(strip=True)
        if not url or not title:
            return None

        source_el = item.select_one(".site-name-content") or item.select_one(
            "[class*='site-name']"
        )
        source = None
        if source_el:
            # "CoinDesk•18 hours ago" -> "CoinDesk".
            source = source_el.get_text(strip=True).split("•")[0].strip() or None

        return NewsEntry.create_new(
            topic=topic, title=title, url=str(url).strip(), source=source
        )

    def detect_block(self, final_url: str, html: str) -> str | None:
        # When Brave flags/rate-limits traffic it serves a CAPTCHA interstitial.
        # Observed 2026-06-17 (docs/BLOCK_SIGNAL_FINDINGS.md) it comes with HTTP
        # 429 — already caught by the monitored-codes net — served in place (no
        # redirect). Key on the body too, in case it's ever served with a 200.
        # Real results pages carry page:"/search", never this copy or
        # page:"/captcha".
        lowered = html.lower()
        if "decided to schedule a captcha" in lowered or '"/captcha"' in lowered:
            return "Brave captcha interstitial"
        return None
