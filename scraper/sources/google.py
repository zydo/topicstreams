"""Google Search News-tab source.

Scrapes Google Search's News tab (https://google.com/search?tbm=nws), NOT the
Google News site. Results are paginated (~10 per page).
"""

import re

from bs4 import BeautifulSoup
from bs4.element import Tag

from common.config import anti_detection_config
from common.model import NewsEntry

from .base import Ordering, Recency, SearchSource

# Google's "query date range" (qdr) codes.
_RECENCY_QDR = {
    Recency.HOUR: "h",
    Recency.DAY: "d",
    Recency.WEEK: "w",
    Recency.MONTH: "m",
}

# Result-item containers, newest layout first; Google rotates markup over time.
_ITEM_SELECTORS = (
    "div.WCv1we",
    "div.SoaBEf",
    "div.Gx5Zad",
    "div[data-sokoban-container] > div",
    "#rso div.g, #search div.g",
)


class GoogleSource(SearchSource):
    name = "google"
    ready_selector = "#search, #rso, div[data-sokoban-container]"

    def build_url(
        self, topic: str, *, ordering: Ordering, recency: Recency, page: int
    ) -> str:
        formatted_topic = re.sub(r"\s+", "+", topic.strip())
        start = (page - 1) * 10  # 10 results per Google result page

        # tbs ("to be sorted") flags:
        #   sbd:1  - sort by date (newest first); omitted for relevance ordering
        #   qdr:X  - query date range (h/d/w/m); omitted for "any"
        #   nsd:1  - show the same news from different sources
        tbs = []
        if ordering is Ordering.DATE:
            tbs.append("sbd:1")
        qdr = _RECENCY_QDR.get(recency)
        if qdr:
            tbs.append(f"qdr:{qdr}")
        tbs.append("nsd:1")

        return (
            "https://www.google.com/search?tbm=nws"
            f"&tbs={','.join(tbs)}&start={start}&q={formatted_topic}"
        )

    def find_items(self, soup: BeautifulSoup) -> list[Tag]:
        for selector in _ITEM_SELECTORS:
            items = soup.select(selector)
            if items:
                return items
        return []

    def parse_item(self, item: Tag, topic: str) -> NewsEntry | None:
        title = self._get_title(item)
        if not title:
            return None
        url = self._get_url(item)
        if not url:
            return None
        return NewsEntry.create_new(
            topic=topic, title=title, url=url, source=self._get_source(item)
        )

    @staticmethod
    def _get_title(item: Tag) -> str | None:
        elem = item.select_one('div[role="heading"], a[role="heading"]')
        if not elem:
            elem = item.select_one("h3, h4")
        return elem.get_text(strip=True) if elem else None

    @staticmethod
    def _get_url(item: Tag) -> str | None:
        link = item.select_one("a[href]")
        href = link.get("href") if link else None
        if not href:
            return None
        url = str(href).strip()
        if url.startswith("/url?q="):
            # Google redirect wrapper: /url?q=<real-url>&...
            url = url.split("/url?q=")[1].split("&")[0]
        elif url.startswith("/"):
            url = "https://www.google.com" + url
        return url

    @staticmethod
    def _get_source(item: Tag) -> str | None:
        elem = item.select_one("div.MgUUmf, span.MgUUmf")
        if not elem:
            elem = item.select_one("div[data-n-tid], div.CEMjEf span")
        return elem.get_text(strip=True) if elem else None

    def detect_block(self, final_url: str, html: str) -> str | None:
        # The definitive signal is the /sorry/ redirect; keyword matching alone
        # false-positives because real results pages mention "captcha" in
        # Google's inline JS.
        if not anti_detection_config.captcha_detection_enabled:
            return None
        if "/sorry/" in final_url:
            return "redirected to /sorry/ block page"
        lower = html.lower()
        for keyword in anti_detection_config.captcha_keywords:
            if keyword.lower() in lower:
                return f"'{keyword}' found"
        return None
