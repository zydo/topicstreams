"""Brave Search source (News tab + general Web search).

Brave's news vertical (``search.brave.com/news``) renders each result as a
``div.snippet[data-type="news"]`` whose first anchor links directly to the
article (no redirector). The publisher name and relative time share a
``.site-name`` element ("CoinDesk•18 hours ago"). The WEB parser scrapes the
``search.brave.com/search?q=`` page; see ``docs/BRAVE_WEB_SERP_PARSING.md``.
"""

import re

from bs4 import BeautifulSoup
from bs4.element import Tag

from common.model import NewsEntry, WebResult, WebResultKind

from .base import (
    Recency,
    ResultParser,
    SearchRequest,
    SearchSource,
    SearchVertical,
    domain_of,
    format_query,
    is_discussion,
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


# The "In the News" cluster is a carousel (9+ publisher cards) that would swamp
# the organic list, so we keep only the leading handful; social/forum discussion
# cards within it are capped harder still. The host list and domain/discussion
# helpers are shared across engines (see base.py).
_MAX_NEWS = 6
_MAX_DISCUSSIONS = 3
# Trailing "… Wikipedia" attribution on the infobox description sentence.
_KP_ATTRIBUTION_RE = re.compile(r"\s*…?\s*Wikipedia\s*$")


class BraveWebParser(ResultParser):
    """General web-search results — the raw ``search.brave.com/search?q=`` page.

    Brave's web SERP is server-rendered and refreshingly clean: each result is a
    ``div.snippet`` tagged by a stable ``data-type`` attribute, so component kinds
    never have to be told apart by guessing. Every result href is the **direct**
    destination URL (no redirect wrapper). We parse the three kinds that carry
    text and follow to a real source, each a uniform ``WebResult`` ordered by how
    directly it answers a lookup. See ``docs/BRAVE_WEB_SERP_PARSING.md`` for the
    research + rationale.

    Parsed (most-direct first):

    - **Knowledge panel** (``section#infobox``): the entity-summary description +
      its Wikipedia source link.
    - **Organic** (``div.snippet[data-type="web"]``): title ``.title``, the direct
      ``a.l1`` URL, source the brand in ``.site-name-content``, snippet
      ``.generic-snippet .content`` (with its leading "N ago -" date stripped).
    - **Top story / Discussion** (``a.enrichment-card-item`` in the "In the News"
      ``data-type="cluster"`` carousel — cards link to the real publisher, not a
      Brave-internal aggregator): social/forum hosts are tagged ``DISCUSSION``
      (and capped), the rest ``TOP_STORY``.

    Deliberately **not** parsed (see the doc): ads (``data-type="ad"`` /
    ``#search-ad``) and the weather/finance widgets (``.rich-weather-content`` and
    kin — markup too brittle to map cleanly; the underlying sites still come
    through as organic results). Deduped by destination URL, knowledge panel and
    organic before the news cluster.
    """

    ready_selector = "#results, .snippet"

    def build_url(self, request: SearchRequest) -> str:
        # Raw web search — only q=<query> (+ offset for pagination, Brave's
        # 0-based page index). No freshness/sort: this is the relevance page.
        url = "https://search.brave.com/search?q=" + format_query(request.query)
        if request.page > 1:
            url += f"&offset={request.page - 1}"
        return url

    def find_items(self, soup: BeautifulSoup) -> list[Tag]:
        items: list[Tag] = []
        seen: set[str] = set()

        def _add(tag: Tag, url: str | None) -> bool:
            if not url:
                return False
            key = url.split("#")[0].split("?")[0].rstrip("/")
            if key in seen:
                return False
            seen.add(key)
            items.append(tag)
            return True

        # Knowledge panel first (the most direct entity answer), then organic.
        infobox = soup.select_one("section#infobox")
        if infobox is not None and self._infobox_source(infobox):
            items.append(infobox)
        for block in soup.select("div.snippet[data-type='web']"):
            anchor = block.select_one("a.l1[href]") or block.select_one("a[href]")
            _add(block, self._direct_url(anchor.get("href")) if anchor else None)
        # "In the News" cluster: keep the leading _MAX_NEWS cards (it's a long
        # carousel), with social/forum discussion cards capped harder still.
        news_added = discussions = 0
        for card in soup.select(
            "div.snippet[data-type='cluster'] a.enrichment-card-item[href]"
        ):
            if news_added >= _MAX_NEWS:
                break
            url = self._direct_url(card.get("href"))
            if not url:
                continue
            if is_discussion(domain_of(url)):
                if discussions >= _MAX_DISCUSSIONS:
                    continue
                discussions += 1
            if _add(card, url):  # not a dup of an organic result
                news_added += 1
        return items

    def parse(self, item: Tag, request: SearchRequest) -> WebResult | None:
        if item.get("id") == "infobox":
            return self._parse_infobox(item, request)
        if item.name == "a":  # an "In the News" cluster card
            return self._parse_news_card(item)
        return self._parse_organic(item)

    @staticmethod
    def _direct_url(href) -> str | None:
        """Brave's web hrefs are the real destination; just validate http(s)."""
        if not href:
            return None
        href = str(href).strip()
        return href if href.startswith(("http://", "https://")) else None

    @staticmethod
    def _infobox_source(infobox: Tag) -> Tag | None:
        """The infobox's "Wikipedia" source-attribution link (the one whose text
        reads "Wikipedia", not the entity-title link that also points there)."""
        for a in infobox.select("a[href*='wikipedia.org']"):
            if a.get_text(strip=True).lower() == "wikipedia":
                return a
        return None

    @classmethod
    def _parse_infobox(cls, infobox: Tag, request: SearchRequest) -> WebResult | None:
        link = cls._infobox_source(infobox)
        if link is None:
            return None
        # The description sentence is the text of the section that ends with the
        # "Wikipedia" attribution link; strip that trailing word.
        container = link.parent or infobox
        description = _KP_ATTRIBUTION_RE.sub(
            "", container.get_text(" ", strip=True)
        ).strip(" …")
        if not description:
            return None
        return WebResult.create(
            kind=WebResultKind.KNOWLEDGE_PANEL,
            title=request.query,
            url=cls._direct_url(link.get("href")),
            source="Wikipedia",
            snippet=description,
        )

    @classmethod
    def _parse_organic(cls, item: Tag) -> WebResult | None:
        anchor = item.select_one("a.l1[href]") or item.select_one("a[href]")
        url = cls._direct_url(anchor.get("href")) if anchor else None
        if not url:
            return None
        title_el = item.select_one(".title")
        title = (
            title_el.get("title") or title_el.get_text(strip=True) if title_el else None
        )
        if not title:
            return None
        brand = item.select_one(".site-name-content > div")
        content = item.select_one(".generic-snippet .content")
        snippet = None
        if content is not None:
            for date in content.select("span.t-secondary"):  # leading "N ago -"
                date.decompose()
            snippet = content.get_text(" ", strip=True) or None
        return WebResult.create(
            kind=WebResultKind.ORGANIC,
            title=str(title).strip(),
            url=url,
            source=brand.get_text(strip=True) if brand else None,
            snippet=snippet,
        )

    @classmethod
    def _parse_news_card(cls, card: Tag) -> WebResult | None:
        url = cls._direct_url(card.get("href"))
        if not url:
            return None
        title_el = card.select_one(".line-clamp-2")
        title = title_el.get_text(" ", strip=True) if title_el else None
        if not title:
            return None
        domain_el = card.select_one(".enrichment-card-site span")
        source = domain_el.get_text(strip=True) if domain_el else None
        kind = (
            WebResultKind.DISCUSSION
            if is_discussion(domain_of(url))
            else WebResultKind.TOP_STORY
        )
        return WebResult.create(kind=kind, title=title, url=url, source=source)


class BraveSource(SearchSource):
    name = "brave"
    results_host = "brave.com"  # search.brave.com
    # News -> /news, web -> /search; "/" covers both (a real block changes host).
    results_path_prefix = "/"

    def _build_parsers(self) -> dict[SearchVertical, ResultParser]:
        return {
            SearchVertical.NEWS: BraveNewsParser(),
            SearchVertical.WEB: BraveWebParser(),
        }

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
