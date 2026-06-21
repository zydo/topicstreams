"""Google Search source (News tab + general Web search).

The NEWS parser scrapes Google Search's News tab
(https://google.com/search?tbm=nws), NOT the Google News site. The WEB parser
scrapes the raw ``/search?q=`` page across its heterogeneous components. Results
are paginated (~10 per page).
"""

import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from bs4.element import Tag

from common.config import anti_detection_config
from common.model import NewsEntry, WebResult, WebResultKind

from .base import (
    Ordering,
    Recency,
    ResultParser,
    SearchRequest,
    SearchSource,
    SearchVertical,
    format_query,
)

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


class GoogleNewsParser(ResultParser):
    ready_selector = "#search, #rso, div[data-sokoban-container]"

    def build_url(self, request: SearchRequest) -> str:
        start = (request.page - 1) * 10  # 10 results per Google result page

        # tbs ("to be sorted") flags:
        #   sbd:1  - sort by date (newest first); omitted for relevance ordering
        #   qdr:X  - query date range (h/d/w/m); omitted for "any"
        #   nsd:1  - show the same news from different sources
        tbs = []
        if request.sort is Ordering.DATE:
            tbs.append("sbd:1")
        qdr = _RECENCY_QDR.get(request.recency)
        if qdr:
            tbs.append(f"qdr:{qdr}")
        tbs.append("nsd:1")

        return (
            "https://www.google.com/search?tbm=nws"
            f"&tbs={','.join(tbs)}&start={start}&q={format_query(request.query)}"
        )

    def find_items(self, soup: BeautifulSoup) -> list[Tag]:
        for selector in _ITEM_SELECTORS:
            items = soup.select(selector)
            if items:
                return items
        return []

    def parse(self, item: Tag, request: SearchRequest) -> NewsEntry | None:
        title = self._get_title(item)
        if not title:
            return None
        url = self._get_url(item)
        if not url:
            return None
        source = self._get_source(item)
        return NewsEntry.create_new(
            topic=request.query,
            title=title,
            url=url,
            source=source,
            snippet=self._get_snippet(item, title, source),
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

    @staticmethod
    def _get_snippet(item: Tag, title: str, source: str | None) -> str | None:
        """The description blurb under the headline.

        Google's snippet div uses obfuscated, frequently-rotating class names
        (e.g. GI74Re, then UqSP2b), so match by structure instead: the longest
        leaf text block in the card that is neither the title nor the source.
        """
        best = ""
        for div in item.find_all("div"):
            if div.find("div"):  # only leaf-ish text containers
                continue
            text = div.get_text(" ", strip=True)
            if len(text) <= len(best):
                continue
            if text == title or (source and text == source):
                continue
            best = text
        return best or None


# Organic result container, newest layout first; Google rotates these. tF2Cxc is
# the standard organic block; yuRUbf is its title-link wrapper, kept as a fallback
# for layouts that drop the outer block (deduped by URL, so it never double-counts).
_WEB_ORGANIC_SELECTORS = ("div.tF2Cxc", "div.yuRUbf")
# News-pack cards (the "Top stories" / "Also in the news" / Discussions clusters).
# Each article is an a.WlydOe anchor carrying the destination URL.
_WEB_NEWS_CARD_SELECTOR = (
    "div[data-news-cluster-id] a.WlydOe[href], g-card a.WlydOe[href]"
)
# Video-carousel results.
_WEB_VIDEO_SELECTOR = 'a[href*="youtube.com/watch"], a[href*="youtu.be/"]'
# Single-block "direct answer" components, by their stable marker.
_ANSWER_SELECTOR = "div.LGOjhe"  # featured-snippet answer paragraph
_KNOWLEDGE_PANEL_SELECTOR = "div.kno-rdesc"  # entity-summary description block
_WEATHER_SELECTOR = "#wob_wc"  # weather widget

# "Channel · NNN views · time" tail Google appends after a video title.
_VIDEO_META_RE = re.compile(
    r"YouTube\s*[·•]\s*(?P<channel>.+?)(?:\s+[\d.,KMB+]+\s+views?\b|\s*[·•]|$)"
)
# Video heading text that's actually an uploader/date/duration line, not a title.
_VIDEO_JUNK_TITLE_RE = re.compile(
    r"^(?:\d+:\d+|\d+\s*(?:s|sec|min|h|hr|hour|d|day|w|wk|week|mo|month|y|yr|year)s?"
    r"(?:\s+ago)?|[A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})$",
    re.IGNORECASE,
)
# Social / forum / blogging hosts whose cards are discussions, not news. Matched
# against the registrable host and any subdomain (e.g. user.medium.com).
_DISCUSSION_DOMAINS = frozenset({
    "reddit.com", "x.com", "twitter.com", "medium.com", "facebook.com",
    "tiktok.com", "threads.com", "threads.net", "instagram.com", "linkedin.com",
    "quora.com", "substack.com", "stackexchange.com", "stackoverflow.com",
    "ycombinator.com",
})
# Cap on discussion cards so a chatty query (e.g. "apple" → dozens of Reddit/X
# posts) can't drown the substantive results.
_MAX_DISCUSSIONS = 5


def _domain_of(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _is_discussion(domain: str) -> bool:
    return any(
        domain == d or domain.endswith("." + d) for d in _DISCUSSION_DOMAINS
    )


class GoogleWebParser(ResultParser):
    """General web-search results — the raw ``/search?q=`` page.

    The web SERP is heterogeneous and, unlike the news tab, packs several result
    kinds under one generic ``div.MjjYud`` wrapper. Selecting ``MjjYud`` and
    grabbing the first ``h3`` conflates them and surfaces whichever block comes
    first (often a video). Instead we target each component by its *inner* marker
    and return a uniform ``WebResult`` tagged with its ``kind``, ordered by how
    directly each answers an information lookup. See
    ``docs/GOOGLE_WEB_SERP_PARSING.md`` for the full research + rationale.

    Parsed (most-direct first):

    - **Answer** / featured snippet (``div.LGOjhe``): the extracted answer text.
    - **Knowledge panel** (``div.kno-rdesc``): entity summary + its source link.
    - **Widget** (weather, ``#wob_wc``): a structured direct answer (temp/cond).
    - **Organic** (``div.tF2Cxc``): title ``h3``, clean ``a[href]``, source
      ``span.VuuXrf``, snippet ``div.VwiC3b``.
    - **Top story / Discussion** (``a.WlydOe`` in ``div[data-news-cluster-id]``):
      title ``div[role="heading"]``, source ``.MgUUmf``; cards from social/forum
      hosts are tagged ``DISCUSSION`` (and capped), the rest ``TOP_STORY``.
    - **Video** (standalone ``youtube.com/watch`` carousel items — not the
      "related links" chips inside organic blocks): title, channel, summary.

    Deduped by URL, in the order above. Deliberately ignored: People-also-ask
    (questions, not answers), image / short-video carousels (thumbnail-only),
    ads, and tab/nav chrome. Dictionary and finance widgets are not yet parsed
    (their markup wasn't cleanly mappable from the sampled pages — see the doc).
    """

    ready_selector = "#search, #rso"

    def build_url(self, request: SearchRequest) -> str:
        # Raw web search — ONLY q=<query> (plus start for pagination). The web
        # endpoint ignores the news sort/recency axes; emitting tbs=sbd:1,qdr:*
        # returns a date-sorted, video-heavy page instead of the relevance one.
        url = "https://www.google.com/search?q=" + format_query(request.query)
        if request.page > 1:
            url += f"&start={(request.page - 1) * 10}"  # 10 results per page
        return url

    def find_items(self, soup: BeautifulSoup) -> list[Tag]:
        items: list[Tag] = []
        seen: set[str] = set()

        def _add(tag: Tag, href) -> None:
            key = self._norm_url(href)
            if key and key not in seen:
                seen.add(key)
                items.append(tag)

        # Direct-answer singletons first (most useful for an info lookup): the
        # featured-snippet answer, the knowledge panel, and the weather widget.
        for sel in (_ANSWER_SELECTOR, _KNOWLEDGE_PANEL_SELECTOR, _WEATHER_SELECTOR):
            block = soup.select_one(sel)
            if block is not None:
                items.append(block)
        # Organic results.
        for block in soup.select(", ".join(_WEB_ORGANIC_SELECTORS)):
            anchor = block.select_one("a[href]")
            if anchor:
                _add(block, anchor.get("href"))
        # Top stories / Also in the news / Discussions cards, with discussions
        # (social/forum hosts) capped so they can't swamp the result set.
        discussions = 0
        for anchor in soup.select(_WEB_NEWS_CARD_SELECTOR):
            href = anchor.get("href") or ""
            if _is_discussion(_domain_of(href)):
                if discussions >= _MAX_DISCUSSIONS:
                    continue
                discussions += 1
            _add(anchor, href)
        # Video-carousel results: a standalone video anchor is NOT inside an
        # organic block and isn't a "View related links" chip.
        for anchor in soup.select(_WEB_VIDEO_SELECTOR):
            if "related link" in (anchor.get("aria-label") or "").lower():
                continue
            if anchor.find_parent("div", class_="tF2Cxc"):
                continue
            _add(anchor, anchor.get("href"))
        return items

    def parse(self, item: Tag, request: SearchRequest) -> WebResult | None:
        classes = item.get("class") or []
        if item.get("id") == "wob_wc":
            return self._parse_weather(item, request)
        if "kno-rdesc" in classes:
            return self._parse_knowledge_panel(item, request)
        if "LGOjhe" in classes:
            return self._parse_answer(item, request)
        if item.name == "a":
            href = str(item.get("href", ""))
            if "youtube.com/watch" in href or "youtu.be/" in href:
                return self._parse_video(item)
            return self._parse_news_card(item)
        return self._parse_organic(item)

    @staticmethod
    def _norm_url(href) -> str | None:
        """Canonical key for dedup: http(s) URL without fragment/trailing slash.

        The query string is dropped for normal results (it's usually tracking
        cruft) but KEPT for YouTube, whose video id lives in ``?v=`` — otherwise
        every watch URL would collapse to a single ``youtube.com/watch`` key.
        """
        if not href:
            return None
        url = str(href).strip()
        if not url.startswith(("http://", "https://")):
            return None
        url = url.split("#")[0]
        if "youtube.com/watch" in url or "youtu.be/" in url:
            return url.rstrip("/")
        return url.split("?")[0].rstrip("/")

    @staticmethod
    def _parse_organic(item: Tag) -> WebResult | None:
        heading = item.select_one("h3")
        if not heading:
            return None
        anchor = heading.find_parent("a", href=True) or item.select_one("a[href]")
        if not anchor:
            return None
        url = str(anchor["href"]).strip()
        if not url.startswith(("http://", "https://")):
            return None
        title = heading.get_text(strip=True)
        if not title:
            return None
        source = item.select_one("span.VuuXrf")
        snippet = item.select_one("div.VwiC3b")
        return WebResult.create(
            kind=WebResultKind.ORGANIC,
            title=title,
            url=url,
            source=source.get_text(strip=True) if source else None,
            snippet=snippet.get_text(" ", strip=True) if snippet else None,
        )

    @staticmethod
    def _parse_news_card(anchor: Tag) -> WebResult | None:
        url = str(anchor.get("href", "")).strip()
        if not url.startswith(("http://", "https://")):
            return None
        heading = anchor.select_one('div[role="heading"], .n0jPhd')
        title = heading.get_text(strip=True) if heading else None
        if not title:
            return None
        source = anchor.select_one(".MgUUmf")
        kind = (
            WebResultKind.DISCUSSION
            if _is_discussion(_domain_of(url))
            else WebResultKind.TOP_STORY
        )
        return WebResult.create(
            kind=kind,
            title=title,
            url=url,
            source=source.get_text(strip=True) if source else None,
        )

    @classmethod
    def _parse_video(cls, anchor: Tag) -> WebResult | None:
        url = str(anchor.get("href", "")).strip()
        if not url.startswith(("http://", "https://")):
            return None
        # Climb to the carousel block, collecting heading candidates. Some
        # layouts put the uploader/date in a role="heading" too, so pick the
        # first candidate that doesn't look like a date/duration/uploader line.
        block = anchor
        candidates: list[str] = []
        for _ in range(5):
            block = block.parent
            if block is None:
                break
            candidates = [
                t for t in (
                    h.get_text(strip=True)
                    for h in block.select('div[role="heading"], h3')
                ) if t
            ]
            if candidates:
                break
        title = next(
            (t for t in candidates if not _VIDEO_JUNK_TITLE_RE.match(t) and len(t) > 4),
            None,
        )
        if not title or block is None:
            return None
        block_text = block.get_text(" ", strip=True)
        meta = _VIDEO_META_RE.search(block_text)
        channel = meta.group("channel").strip() if meta else None
        if channel:
            # Strip a trailing upload date/relative-time the channel name picked
            # up ("defragmenteur Sep 28, 2024" -> "defragmenteur"), and drop the
            # channel entirely if what's left is just such a token ("1w").
            channel = re.sub(
                r"\s+(?:[A-Z][a-z]{2,8}\.?\s+\d{1,2},?\s+\d{4}"
                r"|\d+\s+\w+\s+ago)$",
                "", channel,
            ).strip()
            if not channel or _VIDEO_JUNK_TITLE_RE.match(channel):
                channel = None
        # Text summary, when the clip has one (YouTube news often does): the
        # block text minus the title and the "Channel · views · time" tail.
        summary = block_text
        for chunk in (title, "YouTube", channel or ""):
            summary = summary.replace(chunk, " ")
        summary = re.sub(r"[·•]|\b[\d.,KMB+]+\s+views?\b|\b\d+\s+\w+\s+ago\b", " ", summary)
        summary = re.sub(r"\s+", " ", summary).strip(" -|")
        return WebResult.create(
            kind=WebResultKind.VIDEO,
            title=title,
            url=url,
            source=channel,
            snippet=summary or None,
        )

    @staticmethod
    def _parse_answer(block: Tag, request: SearchRequest) -> WebResult | None:
        """Featured-snippet answer paragraph + its source link, if any."""
        text = block.get_text(" ", strip=True)
        if not text:
            return None
        # The source result usually sits just outside the answer block.
        url = source = None
        scope = block
        for _ in range(5):
            scope = scope.parent
            if scope is None:
                break
            link = scope.select_one("a[href^=http] h3")
            if link:
                a = link.find_parent("a", href=True)
                url = a["href"] if a else None
                cite = scope.select_one("span.VuuXrf, cite")
                source = cite.get_text(strip=True) if cite else None
                break
        return WebResult.create(
            kind=WebResultKind.ANSWER,
            title=request.query,
            url=url,
            source=source,
            snippet=text,
        )

    @staticmethod
    def _parse_knowledge_panel(block: Tag, request: SearchRequest) -> WebResult | None:
        """Entity-summary description + its source (usually Wikipedia)."""
        span = block.select_one("span")
        description = span.get_text(" ", strip=True) if span else None
        if not description:
            return None
        link = block.select_one("a[href^=http]")
        return WebResult.create(
            kind=WebResultKind.KNOWLEDGE_PANEL,
            title=request.query,
            url=link["href"] if link else None,
            source=link.get_text(strip=True) if link else None,
            snippet=description,
        )

    @staticmethod
    def _parse_weather(block: Tag, request: SearchRequest) -> WebResult | None:
        """Weather widget → a compact 'NN°, condition' direct answer."""
        temp = block.select_one("#wob_tm")
        cond = block.select_one("#wob_dc")
        if not (temp and temp.get_text(strip=True)):
            return None
        parts = [f"{temp.get_text(strip=True)}°"]
        if cond and cond.get_text(strip=True):
            parts.append(cond.get_text(strip=True))
        return WebResult.create(
            kind=WebResultKind.WIDGET,
            title=request.query,
            snippet=", ".join(parts),
        )


class GoogleSource(SearchSource):
    name = "google"
    results_host = "google.com"
    results_path_prefix = "/search"  # a block redirects to /sorry/

    def _build_parsers(self) -> dict[SearchVertical, ResultParser]:
        return {
            SearchVertical.NEWS: GoogleNewsParser(),
            SearchVertical.WEB: GoogleWebParser(),
        }

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
