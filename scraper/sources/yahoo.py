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

from .base import (
    ResultParser,
    SearchRequest,
    SearchSource,
    SearchVertical,
    format_query,
)

# The real destination is URL-encoded between ``/RU=`` and the next ``/``.
_REDIRECT_RE = re.compile(r"/RU=([^/]+)/")


class YahooNewsParser(ResultParser):
    ready_selector = "ol.searchCenterMiddle, #web"

    def build_url(self, request: SearchRequest) -> str:
        # Yahoo paginates by 1-based result offset (b=1, 11, 21, ...).
        b = (request.page - 1) * 10 + 1
        # Yahoo news search exposes no reliable date-sort or freshness
        # parameter, so `sort`/`recency` are not applied here (results are
        # engine-default ranked). Date sort is a nice-to-have left for further
        # exploration; a short scrape interval keeps the feed fresh regardless.
        return f"https://news.search.yahoo.com/search?p={format_query(request.query)}&b={b}"

    def find_items(self, soup: BeautifulSoup) -> list[Tag]:
        return soup.select("ol.searchCenterMiddle li")

    def parse(self, item: Tag, request: SearchRequest) -> NewsEntry | None:
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

        desc_el = item.select_one("p.s-desc")
        snippet = desc_el.get_text(" ", strip=True) if desc_el else None

        return NewsEntry.create_new(
            topic=request.query,
            title=title,
            url=url.strip(),
            source=source,
            snippet=snippet or None,
        )


class YahooSource(SearchSource):
    name = "yahoo"
    results_host = "yahoo.com"  # news.search.yahoo.com
    results_path_prefix = "/search"

    def _build_parsers(self) -> dict[SearchVertical, ResultParser]:
        return {SearchVertical.NEWS: YahooNewsParser()}

    def detect_block(self, final_url: str, html: str) -> str | None:
        del final_url, html  # no body signal to inspect; see below
        # Yahoo DOES block under load, but not with a parseable page: the
        # 2026-06-18 concurrency run tripped it after ~250 rapid requests, after
        # which it served a persistent empty (0-byte) HTTP 500 from its
        # `Server: ATS` edge with `Connection: close` (an IP cooldown). A real
        # browser nav then fails with net::ERR_CONNECTION_CLOSED, which the
        # runner's outer exception handler already records as a failed scrape —
        # before any body exists for detect_block to inspect. So there is no
        # body signal to key on; this stays None (see docs/BLOCK_SIGNAL_FINDINGS.md).
        return None
