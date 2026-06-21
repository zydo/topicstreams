"""Yahoo search source (News vertical + general Web search).

Yahoo serves a server-rendered news vertical at ``news.search.yahoo.com`` whose
result links are wrapped in a Yahoo redirector (``r.search.yahoo.com/.../RU=<url>/``);
we unwrap the real target from the ``RU=`` segment. The WEB parser scrapes the
``search.yahoo.com/search?p=`` page, whose organic links are direct (no redirect);
see ``docs/YAHOO_WEB_SERP_PARSING.md``.
"""

import re
from urllib.parse import unquote

from bs4 import BeautifulSoup
from bs4.element import Tag

from common.model import NewsEntry, WebResult, WebResultKind

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


class YahooWebParser(ResultParser):
    """General web-search results — the raw ``search.yahoo.com/search?p=`` page.

    Yahoo's web SERP has one strong, clean signal: the organic ``div.algo`` list,
    where each result carries a **direct** destination URL (no redirect wrapper),
    a title in ``div.compTitle a h3``, the publisher/brand in the breadcrumb, and a
    snippet in ``.compText``. We parse **only** organic results — the one kind that
    is reliably useful and points at a real source. See
    ``docs/YAHOO_WEB_SERP_PARSING.md`` for the research + rationale.

    Deliberately **not** parsed (see the doc): the news carousel (its cards all
    link to Yahoo's own ``yahoo.com/news`` aggregator, not the original publisher,
    and hot-news queries already surface those stories as organic results), the
    right rail (a "See results about" disambiguation list + "Yahoo Scout" AI promo
    + trending searches — no sourced entity panel), and ads (``data-matarget=ad``).
    Deduped by destination URL.
    """

    ready_selector = "#web, div.algo"

    def build_url(self, request: SearchRequest) -> str:
        # Raw web search — p=<query> (+ b= for pagination, Yahoo's 1-based offset).
        b = (request.page - 1) * 10 + 1
        url = "https://search.yahoo.com/search?p=" + format_query(request.query)
        if request.page > 1:
            url += f"&b={b}"
        return url

    def find_items(self, soup: BeautifulSoup) -> list[Tag]:
        items: list[Tag] = []
        seen: set[str] = set()
        for algo in soup.select("div.algo"):
            anchor = algo.select_one("div.compTitle a[href]")
            if anchor is None or anchor.get("data-matarget") == "ad":
                continue  # an ad block reusing the organic markup
            url = self._real_url(anchor.get("href"))
            if not url:
                continue
            key = url.split("#")[0].rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            items.append(algo)
        return items

    def parse(self, item: Tag, request: SearchRequest) -> WebResult | None:
        anchor = item.select_one("div.compTitle a[href]")
        if anchor is None:
            return None
        url = self._real_url(anchor.get("href"))
        if not url:
            return None
        heading = anchor.select_one("h3")
        title = heading.get_text(strip=True) if heading else None
        if not title:
            return None
        # Brand/site name sits in the breadcrumb line, before the URL text
        # ("Reutershttps://www.reuters.com › world" -> "Reuters").
        crumb = anchor.select_one("div")
        source = None
        if crumb:
            source = (
                crumb.get_text(" ", strip=True).split("http")[0].strip(" ·›") or None
            )
        snippet = item.select_one(".compText")
        return WebResult.create(
            kind=WebResultKind.ORGANIC,
            title=title,
            url=url,
            source=source,
            snippet=snippet.get_text(" ", strip=True) if snippet else None,
        )

    @staticmethod
    def _real_url(href) -> str | None:
        """The modern web SERP uses direct hrefs; still unwrap a legacy
        ``/RU=<url>/`` redirect defensively. Rejects non-http(s)."""
        if not href:
            return None
        href = str(href).strip()
        match = _REDIRECT_RE.search(href)
        if match:
            href = unquote(match.group(1))
        return href if href.startswith(("http://", "https://")) else None


class YahooSource(SearchSource):
    name = "yahoo"
    results_host = "yahoo.com"  # news.search.yahoo.com / search.yahoo.com
    results_path_prefix = "/search"

    def _build_parsers(self) -> dict[SearchVertical, ResultParser]:
        return {
            SearchVertical.NEWS: YahooNewsParser(),
            SearchVertical.WEB: YahooWebParser(),
        }

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
