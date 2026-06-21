"""Bing search source (News tab + general Web search).

The NEWS parser scrapes Bing's news vertical, whose cards carry the article URL,
title, and source as attributes (``data-url`` / ``data-title`` / ``data-author``)
— attribute-based extraction, more robust than nested text selectors. The WEB
parser scrapes the raw ``/search?q=`` page; see ``docs/BING_WEB_SERP_PARSING.md``.
"""

import base64
import re

from bs4 import BeautifulSoup
from bs4.element import Tag

from common.model import NewsEntry, WebResult, WebResultKind

from .base import (
    Ordering,
    Recency,
    ResultParser,
    SearchRequest,
    SearchSource,
    SearchVertical,
    domain_of,
    format_query,
    is_discussion,
)

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


class BingNewsParser(ResultParser):
    ready_selector = "div.newsitem, #algocore"

    def build_url(self, request: SearchRequest) -> str:
        first = (request.page - 1) * 10 + 1  # Bing paginates by 1-based offset
        params = [f"q={format_query(request.query)}", f"first={first}"]
        # Date sorting and freshness are both expressed as qft filter tokens
        # (e.g. qft=interval%3d"7"+sortbydate%3d"1").
        qft: list[str] = []
        interval = _RECENCY_INTERVAL.get(request.recency)
        if interval:
            qft.append(f'interval%3d"{interval}"')
        if request.sort is Ordering.DATE:
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

    def parse(self, item: Tag, request: SearchRequest) -> NewsEntry | None:
        title = item.get("data-title") or item.get("title")
        url = item.get("data-url") or item.get("url")
        if not title or not url:
            return None
        snippet_el = item.select_one("div.snippet")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else None
        author = item.get("data-author")
        return NewsEntry.create_new(
            topic=request.query,
            title=str(title).strip(),
            url=str(url).strip(),
            source=str(author).strip() if author else None,
            snippet=snippet or None,
        )


# Bing rewrites every result href through a redirect: /ck/a?...&u=a1<base64>&...,
# where the real destination is base64 in the u= param (after the "a1" type tag).
# Some hrefs are already direct; handle both.
_CK_URL_RE = re.compile(r"[?&]u=a1([^&]+)")
# Bing's u= payload is standard base64; tolerate the URL-safe alphabet too.
_B64_FIX = str.maketrans("-_", "+/")


def _bing_real_url(href) -> str | None:
    """Resolve a Bing result href to its true http(s) destination, or None.

    Decodes the ``/ck/a?...&u=a1<base64>`` redirect wrapper (Bing's click
    tracker); passes an already-absolute http(s) href straight through; rejects
    anything else — relative chrome links and Bing-internal destinations (the
    ``/videos/riverview`` player, image search, etc.), which are never a real
    result source.
    """
    if not href:
        return None
    href = str(href).strip()
    m = _CK_URL_RE.search(href)
    if m:
        b64 = m.group(1).translate(_B64_FIX)
        b64 += "=" * (-len(b64) % 4)
        try:
            decoded = base64.b64decode(b64).decode("utf-8", "replace")
        except Exception:
            return None
        href = decoded
    if not href.startswith(("http://", "https://")):
        return None
    if domain_of(href) == "bing.com":  # Bing-internal player/search, not a source
        return None
    return href


# News-card anchors in the "News about <q>" pack. Bing ships two spellings of the
# class (nslite_/nslist_); match either. The headline is the anchor's title attr,
# prefixed with a "· <age> [· on MSN]" meta run that we strip.
_NEWS_CARD_SELECTOR = "a.nslite_card_link[href], a.nslist_card_link[href]"
_NEWS_META_RE = re.compile(
    r"^(?:·\s*(?:\d+[smhdwy]|on\s+[A-Za-z]+)\s*)+", re.IGNORECASE
)
# The "News about <q>" pack is a deep carousel (20+ cards for a hot query), which
# would swamp the organic results. Keep only the leading, most-relevant handful;
# within that, cap social/forum discussion cards harder so they can't dominate.
_MAX_NEWS = 6
_MAX_DISCUSSIONS = 3


class BingWebParser(ResultParser):
    """General web-search results — the raw ``bing.com/search?q=`` page.

    Bing's web SERP is cleaner to read than Google's: organic results are a stable
    ``li.b_algo`` list and the news pack's cards carry a class-marked anchor. We
    parse only the two kinds that are reliably **followable to a real source** and
    carry text — organic results and news cards — each as a uniform ``WebResult``.
    See ``docs/BING_WEB_SERP_PARSING.md`` for the research + rationale.

    Parsed (most-direct first):

    - **Organic** (``li.b_algo``): title ``h2 a``, the redirect-decoded URL,
      source ``.tptt`` (publisher name), snippet ``.b_caption p``.
    - **Top story / Discussion** (``a.nslite_card_link``/``nslist_card_link`` in
      the "News about …" pack): decoded URL + headline; social/forum hosts are
      tagged ``DISCUSSION`` (and capped), the rest ``TOP_STORY``.

    Deliberately **not** parsed (see the doc): the video pack (its links are
    Bing-internal ``/videos/riverview`` players, not the real source — thumbnail
    chrome with no followable destination), the ``#b_context`` sidebar (a "Deep
    dive into …" Copilot promo with no description), the Copilot generative answer
    (``l_genai*`` — AI-composed, unstable markup), ads (``li.b_ad``), and the
    weather/finance/dictionary widgets (markup too brittle to map cleanly).
    Deduped by destination URL, organic before news.
    """

    ready_selector = "#b_results, li.b_algo"

    def build_url(self, request: SearchRequest) -> str:
        # Raw web search — only q=<query> (+ first= for pagination). No sort or
        # freshness filters: this is the relevance page a user sees.
        url = "https://www.bing.com/search?q=" + format_query(request.query)
        if request.page > 1:
            url += f"&first={(request.page - 1) * 10 + 1}"  # 1-based offset, 10/page
        return url

    def find_items(self, soup: BeautifulSoup) -> list[Tag]:
        items: list[Tag] = []
        seen: set[str] = set()

        def _add(tag: Tag, url: str) -> None:
            key = url.split("#")[0].split("?")[0].rstrip("/")
            if key not in seen:
                seen.add(key)
                items.append(tag)

        # Organic results first (the most direct answers to a lookup).
        for block in soup.select("li.b_algo"):
            anchor = block.select_one("h2 a[href]")
            url = _bing_real_url(anchor.get("href")) if anchor else None
            if url:
                _add(block, url)
        # News pack: keep the leading _MAX_NEWS cards (it's a long carousel),
        # with social/forum discussion cards capped harder still.
        news_added = discussions = 0
        for anchor in soup.select(_NEWS_CARD_SELECTOR):
            if news_added >= _MAX_NEWS:
                break
            url = _bing_real_url(anchor.get("href"))
            if not url:
                continue
            if is_discussion(domain_of(url)):
                if discussions >= _MAX_DISCUSSIONS:
                    continue
                discussions += 1
            before = len(items)
            _add(anchor, url)
            if len(items) > before:  # actually added (not a dup of an organic)
                news_added += 1
        return items

    def parse(self, item: Tag, request: SearchRequest) -> WebResult | None:
        if item.name == "a":  # a news-pack card anchor
            return self._parse_news_card(item)
        return self._parse_organic(item)

    @staticmethod
    def _parse_organic(item: Tag) -> WebResult | None:
        heading = item.select_one("h2")
        anchor = heading.select_one("a[href]") if heading else None
        if not anchor:
            return None
        url = _bing_real_url(anchor.get("href"))
        if not url:
            return None
        title = heading.get_text(strip=True)
        if not title:
            return None
        source = item.select_one(".tptt")
        snippet = item.select_one(".b_caption p, .b_algoSlug")
        return WebResult.create(
            kind=WebResultKind.ORGANIC,
            title=title,
            url=url,
            source=source.get_text(strip=True) if source else None,
            snippet=snippet.get_text(" ", strip=True) if snippet else None,
        )

    @staticmethod
    def _parse_news_card(anchor: Tag) -> WebResult | None:
        url = _bing_real_url(anchor.get("href"))
        if not url:
            return None
        raw = (anchor.get("title") or anchor.get_text(" ", strip=True) or "").strip()
        title = _NEWS_META_RE.sub("", raw).strip(" ·")
        if not title:
            return None
        domain = domain_of(url)
        kind = (
            WebResultKind.DISCUSSION
            if is_discussion(domain)
            else WebResultKind.TOP_STORY
        )
        # The publisher name isn't on a stable element in the carousel card; the
        # domain (derived from the URL) carries the source instead.
        return WebResult.create(kind=kind, title=title, url=url)


class BingSource(SearchSource):
    name = "bing"
    results_host = "bing.com"
    results_path_prefix = "/"  # news -> /news/search, web -> /search

    def _build_parsers(self) -> dict[SearchVertical, ResultParser]:
        return {
            SearchVertical.NEWS: BingNewsParser(),
            SearchVertical.WEB: BingWebParser(),
        }

    def detect_block(self, final_url: str, html: str) -> str | None:
        del final_url, html  # no body signal to inspect; see below
        # Bing never hard-blocks: the 2026-06-18 concurrency run flooded it with
        # ~50k requests at up to ~76 req/s and every response was HTTP 200 with
        # real results — no 429/403/503, no redirect, no challenge page. Bing's
        # only defence is silently slow-rolling each connection to a per-IP
        # throughput ceiling, which has no page to key on (see
        # docs/BLOCK_SIGNAL_FINDINGS.md). So there is nothing to detect here.
        return None
