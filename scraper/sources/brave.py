"""Brave Search news source.

Brave's news vertical (``search.brave.com/news``) renders each result as a
``div.snippet[data-type="news"]`` whose first anchor links directly to the
article (no redirector). The publisher name and relative time share a
``.site-name`` element ("CoinDesk•18 hours ago").
"""

from bs4 import BeautifulSoup
from bs4.element import Tag

from common.model import NewsEntry

from .base import (
    Recency,
    ResultParser,
    SearchRequest,
    SearchSource,
    SearchVertical,
    format_query,
)

# Brave "freshness" codes for the tf query parameter.
_RECENCY_TF = {
    Recency.HOUR: "pd",  # Brave has no 1-hour filter; pd = past day is closest.
    Recency.DAY: "pd",
    Recency.WEEK: "pw",
    Recency.MONTH: "pm",
}


class BraveNewsParser(ResultParser):
    ready_selector = "div.snippet[data-type='news'], #news-results"

    def build_url(self, request: SearchRequest) -> str:
        params = [f"q={format_query(request.query)}"]
        if request.page > 1:
            params.append(f"offset={request.page - 1}")
        tf = _RECENCY_TF.get(request.recency)
        if tf:
            params.append(f"tf={tf}")
        # Brave news has no documented date-sort parameter, so `sort` is not
        # applied (results stay relevance-ranked); only recency filtering via
        # `tf` is honored. Date sort is a nice-to-have left for further
        # exploration.
        return "https://search.brave.com/news?" + "&".join(params)

    def find_items(self, soup: BeautifulSoup) -> list[Tag]:
        return soup.select("div.snippet[data-type='news']")

    def parse(self, item: Tag, request: SearchRequest) -> NewsEntry | None:
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

        snippet_el = (
            item.select_one(".snippet-description")
            or item.select_one(".generic-snippet")
            or item.select_one(".description")
        )
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else None

        return NewsEntry.create_new(
            topic=request.query,
            title=title,
            url=str(url).strip(),
            source=source,
            snippet=snippet or None,
        )


class BraveSource(SearchSource):
    name = "brave"
    results_host = "brave.com"  # search.brave.com
    results_path_prefix = "/news"

    def _build_parsers(self) -> dict[SearchVertical, ResultParser]:
        return {SearchVertical.NEWS: BraveNewsParser()}

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
