"""Tests for the per-engine HTML parsers (Google, Bing, Yahoo, Brave).

Each engine has a synthetic fixture mirroring its real result markup. These
guard our parsing logic against regressions; the *runtime* selector-rot
detection (parse-0 health signal) covers an engine changing its markup live.

URL/selector/parse logic now lives on a per-vertical ``ResultParser`` reached
via ``source.parser_for(vertical)``; block/redirect detection stays on the
``SearchSource``.
"""

from bs4 import BeautifulSoup

from scraper.sources import (
    BingSource,
    BraveSource,
    GoogleSource,
    Ordering,
    Recency,
    SearchRequest,
    SearchVertical,
    YahooSource,
)

_GOOGLE = GoogleSource()
_BING = BingSource()
_YAHOO = YahooSource()
_BRAVE = BraveSource()

# News-vertical parsers (the only vertical implemented today).
_NEWS = SearchVertical.NEWS
_GOOGLE_NEWS = _GOOGLE.parser_for(_NEWS)
_BING_NEWS = _BING.parser_for(_NEWS)
_YAHOO_NEWS = _YAHOO.parser_for(_NEWS)
_BRAVE_NEWS = _BRAVE.parser_for(_NEWS)


def _req(query, **kwargs):
    return SearchRequest(query=query, **kwargs)


_FIXTURE = """
<html><body>
  <div class="WCv1we">
    <a href="/url?q=https://www.spacenews.com/article-1&sa=U&ved=xyz">
      <div role="heading">Starship clears static-fire test</div>
    </a>
    <div class="MgUUmf">SpaceNews</div>
    <div class="UqSP2b">Starship aced its eighth static-fire test ahead of the orbital attempt.</div>
  </div>
  <div class="WCv1we">
    <a href="https://coindesk.com/story-2">
      <div role="heading">Bitcoin holds above $69,000</div>
    </a>
    <div class="MgUUmf">CoinDesk</div>
  </div>
</body></html>
"""


def _soup(html):
    return BeautifulSoup(html, "lxml")


def _items(html=_FIXTURE):
    return _GOOGLE_NEWS.find_items(_soup(html))


def test_find_news_items_finds_all():
    assert len(_items()) == 2


def test_parse_unwraps_google_redirect_and_extracts_fields():
    entry = _GOOGLE_NEWS.parse(_items()[0], _req("spacex"))
    assert entry is not None
    assert entry.title == "Starship clears static-fire test"
    assert entry.url == "https://www.spacenews.com/article-1"  # /url?q= unwrapped
    assert entry.domain == "spacenews.com"  # www stripped
    assert entry.source == "SpaceNews"
    assert entry.topic == "spacex"
    # Snippet is the longest leaf text that isn't the title or source.
    assert entry.snippet == (
        "Starship aced its eighth static-fire test ahead of the orbital attempt."
    )


def test_google_snippet_none_when_only_title_and_source():
    entry = _GOOGLE_NEWS.parse(_items()[1], _req("bitcoin"))  # no snippet div
    assert entry is not None
    assert entry.snippet is None


def test_parse_direct_url():
    entry = _GOOGLE_NEWS.parse(_items()[1], _req("bitcoin"))
    assert entry is not None
    assert entry.url == "https://coindesk.com/story-2"
    assert entry.domain == "coindesk.com"


def test_parse_resolves_relative_url():
    html = (
        '<div class="WCv1we"><a href="/foo/bar"><div role="heading">T</div></a></div>'
    )
    entry = _GOOGLE_NEWS.parse(_items(html)[0], _req("t"))
    assert entry is not None
    assert entry.url == "https://www.google.com/foo/bar"


def test_parse_returns_none_without_title():
    html = '<div class="WCv1we"><a href="https://x.com/a"></a></div>'
    assert _GOOGLE_NEWS.parse(_items(html)[0], _req("t")) is None


def test_parse_returns_none_without_url():
    html = '<div class="WCv1we"><div role="heading">Title only</div></div>'
    assert _GOOGLE_NEWS.parse(_items(html)[0], _req("t")) is None


def test_build_url_default_is_date_past_hour():
    url = _GOOGLE_NEWS.build_url(
        _req("us iran", sort=Ordering.DATE, recency=Recency.HOUR, page=1)
    )
    assert "tbm=nws" in url
    assert "sbd:1" in url and "qdr:h" in url and "nsd:1" in url
    assert "start=0" in url
    assert "q=us+iran" in url


def test_build_url_relevance_drops_sort_by_date():
    url = _GOOGLE_NEWS.build_url(
        _req("x", sort=Ordering.RELEVANCE, recency=Recency.DAY, page=2)
    )
    assert "sbd:1" not in url
    assert "qdr:d" in url
    assert "start=10" in url  # page 2 -> offset 10


def test_build_url_any_recency_drops_date_range():
    url = _GOOGLE_NEWS.build_url(
        _req("x", sort=Ordering.DATE, recency=Recency.ANY, page=1)
    )
    assert "qdr:" not in url


def test_request_defaults_are_date_past_hour_news():
    # The canonical request defaults mirror the live pipeline.
    req = SearchRequest("x")
    assert req.sort is Ordering.DATE
    assert req.recency is Recency.HOUR
    assert req.vertical is SearchVertical.NEWS
    assert req.page == 1


def test_unsupported_vertical_raises():
    import pytest

    from scraper.sources.base import ResultParser, SearchSource

    # All real engines now support both verticals, so use a throwaway source that
    # supports only NEWS to exercise the unsupported-vertical contract.
    class _NewsOnlySource(SearchSource):
        name = "newsonly"

        def _build_parsers(self):
            return {SearchVertical.NEWS: _BRAVE_NEWS}

        def detect_block(self, final_url, html):
            return None

    with pytest.raises(ValueError):
        _NewsOnlySource().parser_for(SearchVertical.WEB)


# --- Google WEB vertical --------------------------------------------------

_GOOGLE_WEB = _GOOGLE.parser_for(SearchVertical.WEB)

# Mirrors the real /search?q= DOM (captured live, 2026-06-20/21). The web SERP
# packs many result kinds under a generic div.MjjYud wrapper, each parsed into a
# uniform WebResult tagged with its kind (most-direct answer first):
#   - ANSWER: div.LGOjhe featured-snippet paragraph + its source link
#   - KNOWLEDGE_PANEL: div.kno-rdesc entity description + source
#   - WIDGET: weather (#wob_wc) -> compact "NN°, condition"
#   - ORGANIC: div.tF2Cxc (h3 title, clean a[href], span.VuuXrf source, .VwiC3b)
#   - TOP_STORY / DISCUSSION: a.WlydOe cards in div[data-news-cluster-id], split
#     by host (reddit/x/medium... -> DISCUSSION, capped)
#   - VIDEO: standalone youtube-watch carousel items (NOT "related links" chips)
#   Excluded: People-also-ask (div[data-q]), empty MjjYud spacers, ads.
# The CNN card appears in BOTH the news pack and the organic list -> deduped.
from common.model import WebResultKind  # noqa: E402

_GOOGLE_WEB_FIXTURE = """
<html><body>
  <div id="search"><div id="rso">
    <div class="xpdopen">
      <div class="LGOjhe">The Strait of Hormuz handles about a fifth of global oil.</div>
      <div class="g"><a href="https://www.eia.gov/hormuz"><h3>Strait of Hormuz facts</h3></a>
        <span class="VuuXrf">EIA (.gov)</span></div>
    </div>
    <div class="kno-rdesc"><span>The Strait of Hormuz is a strait between the Persian Gulf and the Gulf of Oman.</span>
      <a href="https://en.wikipedia.org/wiki/Strait_of_Hormuz">Wikipedia</a></div>
    <div id="wob_wc"><span id="wob_tm">71</span><span id="wob_dc">Sunny</span></div>
    <div class="ULSxyf">
      <div data-news-cluster-id="1">
        <a class="WlydOe" href="https://www.nbcnews.com/world/iran/story">
          <div role="heading" class="n0jPhd">Iran says Strait of Hormuz is closed</div>
          <div class="MgUUmf">NBC News</div><div class="OSrXXb">3 hours ago</div>
        </a>
        <a class="WlydOe" href="https://www.cnn.com/2026/06/20/world/strait">
          <div role="heading" class="n0jPhd">CNN duplicate of the organic result</div>
          <div class="MgUUmf">CNN</div>
        </a>
        <a class="WlydOe" href="https://www.reddit.com/r/worldnews/post">
          <div role="heading" class="n0jPhd">Reddit thread on the strait</div>
          <div class="MgUUmf">Reddit</div>
        </a>
      </div>
    </div>
    <div class="MjjYud"></div>
    <div class="MjjYud"><div class="tF2Cxc">
      <div class="yuRUbf"><a href="https://www.cnn.com/2026/06/20/world/strait">
        <h3>Iran and US make opposing claims on Strait of Hormuz</h3>
        <cite>https://www.cnn.com › strait</cite></a></div>
      <span class="VuuXrf">CNN</span>
      <div class="VwiC3b">2 hours ago — The US military denied Iran's claim.</div>
      <a href="https://www.youtube.com/watch?v=chip1" aria-label="YouTube (+1) - View related links">x</a>
    </div></div>
    <div class="MjjYud"><div class="tF2Cxc">
      <div class="yuRUbf"><a href="https://en.wikipedia.org/wiki/2026_Iran_war">
        <h3>2026 Iran war</h3></a></div>
      <span class="VuuXrf">Wikipedia</span>
      <div class="VwiC3b">From 28 February to 17 June 2026...</div>
    </div></div>
    <div class="MjjYud">
      <a href="https://www.youtube.com/watch?v=vid1">
        <div role="heading">US and Iran prepare for crucial talks</div>
      </a>
      <div>YouTube · Al Jazeera English 3.4K+ views · 1 hour ago</div>
      <div>Diplomats gather in Switzerland as both sides signal cautious optimism.</div>
    </div>
    <div class="MjjYud">
      <a href="https://www.youtube.com/watch?v=vid2">
        <div role="heading">3w</div>
        <div role="heading">Inside the Strait of Hormuz standoff</div>
      </a>
      <div>YouTube · BBC News Sep 28, 2024</div>
    </div>
    <div class="ULSxyf">
      <div data-q="What is the issue between Iran and the USA?"></div>
    </div>
  </div></div>
</body></html>
"""


def _google_web_items(html=_GOOGLE_WEB_FIXTURE):
    return _GOOGLE_WEB.find_items(_soup(html))


def _google_web_entries(html=_GOOGLE_WEB_FIXTURE):
    return [
        e
        for e in (
            _GOOGLE_WEB.parse(it, _req("us iran")) for it in _google_web_items(html)
        )
        if e is not None
    ]


def _by_kind(kind):
    return [e for e in _google_web_entries() if e.kind is kind]


def test_google_source_supports_web_vertical():
    assert SearchVertical.WEB in _GOOGLE.verticals


def test_google_web_parses_every_component_kind():
    # answer + knowledge_panel + widget + 2 organic + 1 top_story (CNN deduped)
    # + 1 discussion (reddit) + 2 video. Spacer/related-chip/PAA excluded.
    from collections import Counter

    counts = Counter(e.kind for e in _google_web_entries())
    assert dict(counts) == {
        WebResultKind.ANSWER: 1,
        WebResultKind.KNOWLEDGE_PANEL: 1,
        WebResultKind.WIDGET: 1,
        WebResultKind.ORGANIC: 2,
        WebResultKind.TOP_STORY: 1,
        WebResultKind.DISCUSSION: 1,
        WebResultKind.VIDEO: 2,
    }


def test_google_web_answer_first_with_source():
    entries = _google_web_entries()
    assert entries[0].kind is WebResultKind.ANSWER  # most-direct answer ranks first
    ans = entries[0]
    assert ans.snippet.startswith("The Strait of Hormuz handles about a fifth")
    assert ans.url == "https://www.eia.gov/hormuz"
    assert ans.source == "EIA (.gov)"


def test_google_web_knowledge_panel_has_description_and_source():
    kp = _by_kind(WebResultKind.KNOWLEDGE_PANEL)[0]
    assert kp.snippet.startswith("The Strait of Hormuz is a strait")
    assert kp.domain == "en.wikipedia.org"
    assert kp.source == "Wikipedia"


def test_google_web_widget_gives_compact_answer():
    w = _by_kind(WebResultKind.WIDGET)[0]
    assert w.snippet == "71°, Sunny"
    assert w.url is None


def test_google_web_organic_has_title_source_snippet():
    cnn = _by_kind(WebResultKind.ORGANIC)[0]
    assert cnn.title == "Iran and US make opposing claims on Strait of Hormuz"
    assert cnn.url == "https://www.cnn.com/2026/06/20/world/strait"  # clean, no /url?q=
    assert cnn.domain == "cnn.com"
    assert cnn.source == "CNN"  # span.VuuXrf, not the <cite> breadcrumb
    assert cnn.snippet == "2 hours ago — The US military denied Iran's claim."


def test_google_web_top_story_extracts_heading_and_source():
    nbc = _by_kind(WebResultKind.TOP_STORY)[0]
    assert nbc.title == "Iran says Strait of Hormuz is closed"
    assert nbc.domain == "nbcnews.com"
    assert nbc.source == "NBC News"


def test_google_web_discussion_split_by_host():
    # A Reddit card in the news cluster is a DISCUSSION, not a TOP_STORY.
    disc = _by_kind(WebResultKind.DISCUSSION)
    assert [d.domain for d in disc] == ["reddit.com"]


def test_google_web_video_has_channel_and_summary():
    vid = _by_kind(WebResultKind.VIDEO)[0]
    assert vid.title == "US and Iran prepare for crucial talks"
    assert vid.domain == "youtube.com"
    assert vid.source == "Al Jazeera English"  # channel, parsed off the meta tail
    assert vid.snippet == (
        "Diplomats gather in Switzerland as both sides signal cautious optimism."
    )


def test_google_web_video_skips_uploader_date_heading():
    # "3w" is an uploader/relative-time line, not the title; channel date stripped.
    vid = _by_kind(WebResultKind.VIDEO)[1]
    assert vid.title == "Inside the Strait of Hormuz standoff"
    assert vid.source == "BBC News"  # "Sep 28, 2024" stripped off the channel


def test_google_web_excludes_related_link_video_chip():
    # The youtube chip inside the CNN organic block (aria "View related links")
    # is not a standalone result.
    urls = [e.url for e in _google_web_entries() if e.url]
    assert not any("chip1" in u for u in urls)


def test_google_web_excludes_people_also_ask():
    # PAA questions (div[data-q]) are deliberately dropped from results.
    assert all(
        e.title != "What is the issue between Iran and the USA?"
        for e in _google_web_entries()
    )


def test_google_web_dedupes_url_across_news_and_organic():
    # cnn.com appears in both the news pack and the organic list -> once only.
    assert sum(e.domain == "cnn.com" for e in _google_web_entries()) == 1


def test_google_web_build_url_is_raw_query_only():
    # Raw search: q=<query> and nothing else on page 1 (no tbm/tbs/start).
    url = _GOOGLE_WEB.build_url(
        _req("us iran", sort=Ordering.DATE, recency=Recency.HOUR)
    )
    assert url == "https://www.google.com/search?q=us+iran"


def test_google_web_build_url_pagination_adds_start_only():
    url = _GOOGLE_WEB.build_url(_req("us iran", page=3))
    assert url == "https://www.google.com/search?q=us+iran&start=20"
    assert "tbs=" not in url


# --- Bing -----------------------------------------------------------------

# Mirrors Bing's real markup: the card div carries the article url, title, and
# source as attributes.
_BING_FIXTURE = """
<html><body>
  <div class="news-card newsitem cardcommon"
       url="https://www.thestreet.com/crypto/x"
       data-url="https://www.thestreet.com/crypto/x"
       data-title="Economist reveals next Bitcoin target"
       data-author="TheStreet"
       title="Economist reveals next Bitcoin target">
    <div class="snippet">Harry Dent warns a 2026 reset could send Bitcoin to new lows.</div>
  </div>
  <div class="news-card newsitem cardcommon"
       data-url="https://coindesk.com/story"
       data-title="Bitcoin holds above $69k"
       data-author="CoinDesk"></div>
</body></html>
"""


def _bing_items(html=_BING_FIXTURE):
    return _BING_NEWS.find_items(_soup(html))


def test_bing_find_items():
    assert len(_bing_items()) == 2


def test_bing_parse_from_card_attributes():
    entry = _BING_NEWS.parse(_bing_items()[0], _req("bitcoin"))
    assert entry is not None
    assert entry.title == "Economist reveals next Bitcoin target"
    assert entry.url == "https://www.thestreet.com/crypto/x"
    assert entry.domain == "thestreet.com"
    assert entry.source == "TheStreet"
    assert (
        entry.snippet == "Harry Dent warns a 2026 reset could send Bitcoin to new lows."
    )


def test_bing_parse_returns_none_without_url():
    html = '<div class="newsitem" data-title="No url"></div>'
    assert _BING_NEWS.parse(_bing_items(html)[0], _req("t")) is None


def test_bing_build_url_date_and_recency():
    url = _BING_NEWS.build_url(
        _req("us iran", sort=Ordering.DATE, recency=Recency.DAY, page=2)
    )
    assert "q=us+iran" in url
    assert 'sortbydate%3d"1"' in url  # date sort as a qft token
    assert "first=11" in url  # page 2 -> offset 11
    assert 'interval%3d"7"' in url  # DAY -> past 24 hours


def test_bing_build_url_hour_is_past_hour_interval():
    # Past hour is interval "4" (not "7" = past 24h); this is the window the
    # live pipeline actually uses.
    url = _BING_NEWS.build_url(
        _req("x", sort=Ordering.DATE, recency=Recency.HOUR, page=1)
    )
    assert 'interval%3d"4"' in url


def test_bing_detect_block_always_none():
    # Bing never hard-blocks (2026-06-18 concurrency run: ~50k requests, all
    # HTTP 200); its throttle has no page to detect, so detect_block stays None.
    assert (
        _BING.detect_block("https://www.bing.com/news/search?q=x", "<html>x</html>")
        is None
    )
    assert _BING.detect_block("https://www.bing.com/news/search?q=x", "") is None


# --- Yahoo ----------------------------------------------------------------

# Yahoo wraps result links in an r.search.yahoo.com redirector; the real target
# is URL-encoded in the /RU=<url>/ segment.
_YAHOO_FIXTURE = """
<ol class="searchCenterMiddle">
  <li>
    <h4 class="s-title"><a href="https://r.search.yahoo.com/_ylt=abc/RV=2/RE=1/RO=10/RU=https%3a%2f%2fwww.coindesk.com%2fmarkets%2fstory/RK=2/RS=z">Bitcoin bottom signal flashes</a></h4>
    <span class="s-source">Coindesk</span>
    <span class="s-time">1 hour ago ·</span>
    <p class="s-desc">Live coverage as a bitcoin bottom signal flashes for holders.</p>
  </li>
  <li>
    <h4 class="s-title"><a href="https://r.search.yahoo.com/x/RU=https%3a%2f%2ffinance.yahoo.com%2fnews%2fmusk/RK=2">Elon Musk net worth</a></h4>
    <span class="s-source">BeInCrypto·  via Yahoo Finance</span>
  </li>
</ol>
"""


def _yahoo_items(html=_YAHOO_FIXTURE):
    return _YAHOO_NEWS.find_items(_soup(html))


def test_yahoo_find_items():
    assert len(_yahoo_items()) == 2


def test_yahoo_unwraps_redirect_and_extracts_fields():
    entry = _YAHOO_NEWS.parse(_yahoo_items()[0], _req("bitcoin"))
    assert entry is not None
    assert entry.title == "Bitcoin bottom signal flashes"
    assert entry.url == "https://www.coindesk.com/markets/story"
    assert entry.domain == "coindesk.com"
    assert entry.source == "Coindesk"
    assert (
        entry.snippet == "Live coverage as a bitcoin bottom signal flashes for holders."
    )


def test_yahoo_cleans_source_suffix():
    entry = _YAHOO_NEWS.parse(_yahoo_items()[1], _req("t"))
    assert entry is not None
    assert entry.source == "BeInCrypto"  # "· via Yahoo Finance" stripped


def test_yahoo_build_url_query_and_offset():
    # Yahoo has no reliable date-sort/freshness param, so sort/recency are
    # intentionally not reflected in the URL (engine-default ranking).
    url = _YAHOO_NEWS.build_url(
        _req("us iran", sort=Ordering.DATE, recency=Recency.DAY, page=2)
    )
    assert "p=us+iran" in url
    assert "b=11" in url  # page 2 -> 1-based offset 11


def test_yahoo_detect_block_always_none():
    # Yahoo's block (2026-06-18) is an empty 0-byte HTTP 500, not a parseable
    # page — a real browser nav sees net::ERR_CONNECTION_CLOSED, already caught
    # as a nav error. There is no body to key on, so detect_block stays None.
    assert _YAHOO.detect_block("https://news.search.yahoo.com/search?p=x", "") is None
    assert (
        _YAHOO.detect_block(
            "https://news.search.yahoo.com/search?p=x", "<html>results</html>"
        )
        is None
    )


# --- Brave ----------------------------------------------------------------

_BRAVE_FIXTURE = """
<div class="snippet" data-type="news">
  <a class="l1" href="https://www.coindesk.com/markets/story" rel="noopener">x</a>
  <div class="title">BlackRock launches bitcoin income fund</div>
  <div class="site-name-wrapper"><div class="site-name-content">CoinDesk•18 hours ago</div></div>
  <div class="generic-snippet">BlackRock's new fund targets income from its bitcoin ETF.</div>
</div>
<div class="snippet" data-type="news">
  <a class="l1" href="https://cryptonews.net/story" rel="noopener">y</a>
  <div class="title">Bitcoin miners pivot to AI</div>
  <div class="site-name-wrapper"><div class="site-name-content">CryptoNews•2 hours ago</div></div>
</div>
"""


def _brave_items(html=_BRAVE_FIXTURE):
    return _BRAVE_NEWS.find_items(_soup(html))


def test_brave_find_items():
    assert len(_brave_items()) == 2


def test_brave_extracts_title_url_and_source():
    entry = _BRAVE_NEWS.parse(_brave_items()[0], _req("bitcoin"))
    assert entry is not None
    assert entry.title == "BlackRock launches bitcoin income fund"
    assert entry.url == "https://www.coindesk.com/markets/story"
    assert entry.domain == "coindesk.com"
    assert entry.source == "CoinDesk"  # "•18 hours ago" stripped
    assert entry.snippet == "BlackRock's new fund targets income from its bitcoin ETF."


def test_brave_returns_none_without_title():
    html = '<div class="snippet" data-type="news"><a href="https://x.com/a">x</a></div>'
    assert _BRAVE_NEWS.parse(_brave_items(html)[0], _req("t")) is None


def test_brave_build_url_recency_and_offset():
    url = _BRAVE_NEWS.build_url(
        _req("us iran", sort=Ordering.DATE, recency=Recency.WEEK, page=2)
    )
    assert "q=us+iran" in url
    assert "tf=pw" in url  # week
    assert "offset=1" in url


def test_brave_detect_block_flags_captcha_interstitial():
    # Grounded in the 2026-06-17 block characterization (captcha copy + the
    # page:"/captcha" JS state); served with HTTP 429, sometimes a 200 body.
    assert _BRAVE.detect_block(
        "https://search.brave.com/news?q=x",
        "<p>Brave Search decided to schedule a captcha for you.</p>",
    )
    assert _BRAVE.detect_block(
        "https://search.brave.com/news?q=x", 'window.state={page:"/captcha"}'
    )


def test_brave_detect_block_passes_real_results():
    assert (
        _BRAVE.detect_block(
            "https://search.brave.com/news?q=x", 'window.state={page:"/search"}'
        )
        is None
    )


# --- Generic "redirected off the results page" block check ----------------


def test_redirect_check_flags_google_sorry():
    # A block redirects Google off /search to /sorry/.
    assert _GOOGLE.redirected_off_results(
        "https://www.google.com/sorry/index?continue=x"
    )
    assert (
        _GOOGLE.redirected_off_results("https://www.google.com/search?q=x&tbm=nws")
        is None
    )


def test_redirect_check_allows_results_subdomain():
    # Yahoo's results live on a subdomain of the registrable host.
    assert (
        _YAHOO.redirected_off_results("https://news.search.yahoo.com/search?p=x")
        is None
    )
    assert (
        _BRAVE.redirected_off_results("https://search.brave.com/news?q=x&tf=pd") is None
    )


def test_redirect_check_flags_off_path_and_foreign_host():
    # Same host, wrong path (e.g. an account/notice page).
    assert _YAHOO.redirected_off_results("https://login.yahoo.com/account")
    # Entirely different host.
    assert _BRAVE.redirected_off_results("https://challenge.example.com/news?q=x")


# --- Bing WEB vertical -----------------------------------------------------

_BING_WEB = _BING.parser_for(SearchVertical.WEB)

# Mirrors the real bing.com/search?q= DOM (captured live, 2026-06-21). Bing's web
# SERP is cleaner than Google's: organic results are a stable li.b_algo list and
# news-pack cards carry a class-marked anchor (a.nslite_card_link). Every result
# href is wrapped in a /ck/a?...&u=a1<base64> redirect — the real URL is base64
# in u=. We parse only the two reliably-followable, text-bearing kinds — organic
# and news cards — and deliberately drop the rest (see BING_WEB_SERP_PARSING.md):
#   - the video pack: its tiles use a non-news class AND their u= decodes to a
#     relative /videos/riverview player path, not a real source.
#   - the #b_context "Deep dive" Copilot promo, the l_genai generative answer, ads.
# The CNN card appears in BOTH the news pack and the organic list -> deduped.
_CK = "https://www.bing.com/ck/a?!&&p=abc&u=a1"
_BING_WEB_FIXTURE = """
<html><body>
  <ol id="b_results">
    <li class="b_ad b_adTop"><div class="sb_add"><h2><a href="%(ck)saHR0cHM6Ly9hZHMuZXhhbXBsZS5jb20v">Buy Iran tickets</a></h2></div></li>
    <li class="b_ans b_top b_topborder"><h2>News about US Iran</h2>
      <div class="b_slidesContainer">
        <a class="nslite_card_link" href="%(ck)saHR0cHM6Ly93d3cubmJjbmV3cy5jb20vd29ybGQvaXJhbi9zdG9yeQ=="
           title="· 3h US and Iran set to meet for talks in Switzerland"></a>
        <a class="nslist_card_link" href="%(ck)saHR0cHM6Ly93d3cucmVkZGl0LmNvbS9yL3dvcmxkbmV3cy9wb3N0"
           title="· 45m · on MSN Reddit megathread on the Strait of Hormuz"></a>
        <a class="nslite_card_link" href="%(ck)saHR0cHM6Ly93d3cuY25uLmNvbS8yMDI2LzA2LzIwL3dvcmxkL3N0cmFpdA=="
           title="· 2h CNN duplicate of the organic result"></a>
      </div>
    </li>
    <li class="b_algo">
      <h2><a href="%(ck)saHR0cHM6Ly93d3cuY25uLmNvbS8yMDI2LzA2LzIwL3dvcmxkL3N0cmFpdA==">Iran and US make opposing claims on Strait of Hormuz</a></h2>
      <div class="b_tpcn"><div class="tptt">CNN</div></div>
      <div class="b_caption"><p>2 hours ago. The US military denied Iran's claim.</p></div>
    </li>
    <li class="b_algo">
      <h2><a href="%(ck)saHR0cHM6Ly9lbi53aWtpcGVkaWEub3JnL3dpa2kvMjAyNl9JcmFuX3dhcg==">2026 Iran war</a></h2>
      <div class="b_tpcn"><div class="tptt">Wikipedia</div></div>
    </li>
    <li class="b_ans b_mop b_vidAns"><h2>Videos of US Iran</h2>
      <div class="mc_vtvc"><a class="mc_vtvc_link" href="%(ck)saHR0cHM6Ly93d3cuYmluZy5jb20vdmlkZW9zL3JpdmVydmlldy9yZWxhdGVkdmlkZW8/cT11cytpcmFu"
        title="· 1d Strait of Hormuz explained"></a></div>
    </li>
    <li class="b_pag"></li>
  </ol>
  <div id="b_context">
    <div class="b_acard"><h2>Deep dive into us iran</h2>
      <div class="copans_container"><div class="l_genaibdy"><p>Tensions between the US and Iran...</p></div></div>
    </div>
  </div>
</body></html>
""" % {"ck": _CK}


def _bing_web_entries(html=_BING_WEB_FIXTURE):
    return [
        e
        for e in (
            _BING_WEB.parse(it, _req("us iran"))
            for it in _BING_WEB.find_items(_soup(html))
        )
        if e is not None
    ]


def _bing_by_kind(kind):
    return [e for e in _bing_web_entries() if e.kind is kind]


def test_bing_source_supports_web_vertical():
    assert SearchVertical.WEB in _BING.verticals


def test_bing_web_parses_organic_and_news_only():
    from collections import Counter

    # 2 organic + 1 top_story (NBC) + 1 discussion (Reddit). CNN news card is a
    # dup of the CNN organic. The ad, video pack, and #b_context are all dropped.
    counts = Counter(e.kind for e in _bing_web_entries())
    assert dict(counts) == {
        WebResultKind.ORGANIC: 2,
        WebResultKind.TOP_STORY: 1,
        WebResultKind.DISCUSSION: 1,
    }


def test_bing_web_organic_decodes_redirect_and_extracts_fields():
    cnn = _bing_by_kind(WebResultKind.ORGANIC)[0]
    assert cnn.title == "Iran and US make opposing claims on Strait of Hormuz"
    assert cnn.url == "https://www.cnn.com/2026/06/20/world/strait"  # ck/a decoded
    assert cnn.domain == "cnn.com"
    assert cnn.source == "CNN"  # .tptt publisher name
    assert cnn.snippet == "2 hours ago. The US military denied Iran's claim."


def test_bing_web_organic_snippet_optional():
    wiki = _bing_by_kind(WebResultKind.ORGANIC)[1]
    assert wiki.domain == "en.wikipedia.org"
    assert wiki.snippet is None


def test_bing_web_news_card_strips_age_meta_and_sets_source_by_host():
    nbc = _bing_by_kind(WebResultKind.TOP_STORY)[0]
    # The leading "· 3h " age stamp is stripped from the headline.
    assert nbc.title == "US and Iran set to meet for talks in Switzerland"
    assert nbc.url == "https://www.nbcnews.com/world/iran/story"
    assert nbc.domain == "nbcnews.com"


def test_bing_web_discussion_split_by_host():
    # A reddit card in the news pack is a DISCUSSION (and "· 45m · on MSN" meta stripped).
    disc = _bing_by_kind(WebResultKind.DISCUSSION)
    assert [d.domain for d in disc] == ["reddit.com"]
    assert disc[0].title == "Reddit megathread on the Strait of Hormuz"


def test_bing_web_drops_video_pack_internal_links():
    # The video card's u= decodes to a bing.com/videos player -> not a real source.
    assert all("bing.com" not in (e.domain or "") for e in _bing_web_entries())


def test_bing_web_drops_ads_and_deep_dive_promo():
    titles = [e.title for e in _bing_web_entries()]
    assert "Buy Iran tickets" not in titles
    assert all("Deep dive" not in t for t in titles)


def test_bing_web_dedupes_url_across_news_and_organic():
    # cnn.com appears in both the news pack and the organic list -> once only.
    assert sum(e.domain == "cnn.com" for e in _bing_web_entries()) == 1


def test_bing_web_build_url_is_raw_query_only():
    url = _BING_WEB.build_url(_req("us iran", sort=Ordering.DATE, recency=Recency.HOUR))
    assert url == "https://www.bing.com/search?q=us+iran"


def test_bing_web_build_url_pagination_adds_first_only():
    url = _BING_WEB.build_url(_req("us iran", page=3))
    assert url == "https://www.bing.com/search?q=us+iran&first=21"
    assert "qft=" not in url


def test_bing_real_url_decoding_and_rejection():
    from scraper.sources.bing import _bing_real_url

    ck = _CK + "aHR0cHM6Ly93d3cuY25uLmNvbS8yMDI2LzA2LzIwL3dvcmxkL3N0cmFpdA=="
    assert _bing_real_url(ck) == "https://www.cnn.com/2026/06/20/world/strait"
    # A direct absolute href passes straight through.
    assert _bing_real_url("https://example.com/x") == "https://example.com/x"
    # Relative chrome links and Bing-internal destinations are rejected.
    assert (
        _bing_real_url(_CK + "L3ZpZGVvcy9yaXZlcnZpZXc=") is None
    )  # "/videos/riverview"
    assert _bing_real_url("https://www.bing.com/videos/riverview?q=x") is None
    assert _bing_real_url(None) is None


# --- Yahoo WEB vertical ----------------------------------------------------

_YAHOO_WEB = _YAHOO.parser_for(SearchVertical.WEB)

# Mirrors the real search.yahoo.com/search?p= DOM (captured live, 2026-06-21).
# Yahoo's web SERP has one clean signal — the organic div.algo list — so the
# parser keeps ONLY organic results (see YAHOO_WEB_SERP_PARSING.md). Each carries
# a *direct* destination URL (no redirect wrapper), a title in div.compTitle a h3,
# the brand in the breadcrumb (text before the URL), and a snippet in .compText.
# Excluded: ads (data-matarget="ad" reusing the algo markup), the yahoo.com/news
# carousel (Yahoo-internal aggregator), and the right rail. Reuters appears twice
# -> deduped by URL.
_YAHOO_WEB_FIXTURE = """
<html><body>
  <div id="web"><ol class="searchCenterMiddle">
    <li><div class="algo">
      <div class="compTitle"><a class="d-ib" data-matarget="algo" href="https://www.reuters.com/world/iran/">
        <div class="p-abs"><span class="fc-141414">Reuters</span><span>https://www.reuters.com &#8250; world &#8250; iran</span></div>
        <h3 class="title">Iran War: Latest Breaking News</h3></a></div>
      <div class="compText"><p>Real-time Reuters coverage of the Iran war.</p></div>
    </div></li>
    <li><div class="algo">
      <div class="compTitle"><a data-matarget="algo" href="https://www.reuters.com/world/iran/">
        <div class="p-abs"><span>Reuters</span><span>https://www.reuters.com</span></div>
        <h3 class="title">Reuters duplicate of the first result</h3></a></div>
    </div></li>
    <li><div class="algo">
      <div class="compTitle"><a data-matarget="algo" href="https://en.wikipedia.org/wiki/2026_Iran_war">
        <div class="p-abs"><span>Wikipedia</span><span>https://en.wikipedia.org &#8250; wiki</span></div>
        <h3 class="title">2026 Iran war</h3></a></div>
    </div></li>
    <li class="ads"><div class="algo">
      <div class="compTitle"><a data-matarget="ad" href="https://ads.example.com/iran">
        <h3 class="title">Buy Iran tickets</h3></a></div>
    </div></li>
  </ol></div>
  <div class="dd tn-carousel sys_news_auto"><h3 class="title">Top Stories</h3>
    <ul><li><a href="https://www.yahoo.com/news/articles/us-iran-talks-123.html"
      title="US-Iran peace talks to begin">Reuters via Yahoo</a></li></ul>
  </div>
  <div id="right"><div class="dd disambiguation"><h2>See results about</h2>
    <p>Ongoing armed conflict in West Asia</p></div></div>
</body></html>
"""


def _yahoo_web_entries(html=_YAHOO_WEB_FIXTURE):
    return [
        e
        for e in (
            _YAHOO_WEB.parse(it, _req("us iran"))
            for it in _YAHOO_WEB.find_items(_soup(html))
        )
        if e is not None
    ]


def test_yahoo_source_supports_web_vertical():
    assert SearchVertical.WEB in _YAHOO.verticals


def test_yahoo_web_parses_only_organic():
    from collections import Counter

    # 2 organic (Reuters once after dedup + Wikipedia). Ad, news carousel, and
    # right rail are all excluded.
    counts = Counter(e.kind for e in _yahoo_web_entries())
    assert dict(counts) == {WebResultKind.ORGANIC: 2}


def test_yahoo_web_organic_has_direct_url_title_source_snippet():
    reuters = _yahoo_web_entries()[0]
    assert reuters.title == "Iran War: Latest Breaking News"
    assert reuters.url == "https://www.reuters.com/world/iran/"  # direct, no redirect
    assert reuters.domain == "reuters.com"
    assert reuters.source == "Reuters"  # brand from the breadcrumb, before the URL
    assert reuters.snippet == "Real-time Reuters coverage of the Iran war."


def test_yahoo_web_organic_snippet_optional():
    wiki = _yahoo_web_entries()[1]
    assert wiki.domain == "en.wikipedia.org"
    assert wiki.snippet is None


def test_yahoo_web_excludes_ads():
    assert all(e.title != "Buy Iran tickets" for e in _yahoo_web_entries())
    assert all("ads.example.com" not in (e.domain or "") for e in _yahoo_web_entries())


def test_yahoo_web_excludes_news_carousel_and_right_rail():
    # The yahoo.com/news carousel and the #right disambiguation list aren't results.
    assert all("yahoo.com" not in (e.domain or "") for e in _yahoo_web_entries())
    assert all("West Asia" not in (e.snippet or "") for e in _yahoo_web_entries())


def test_yahoo_web_dedupes_by_url():
    assert sum(e.domain == "reuters.com" for e in _yahoo_web_entries()) == 1


def test_yahoo_web_build_url_is_raw_query_only():
    url = _YAHOO_WEB.build_url(
        _req("us iran", sort=Ordering.DATE, recency=Recency.HOUR)
    )
    assert url == "https://search.yahoo.com/search?p=us+iran"


def test_yahoo_web_build_url_pagination_adds_offset_only():
    url = _YAHOO_WEB.build_url(_req("us iran", page=3))
    assert url == "https://search.yahoo.com/search?p=us+iran&b=21"


# --- Brave WEB vertical ----------------------------------------------------

_BRAVE_WEB = _BRAVE.parser_for(SearchVertical.WEB)

# Mirrors the real search.brave.com/search?q= DOM (captured live, 2026-06-21).
# Brave's web SERP is server-rendered and clean: every result is a div.snippet
# tagged by a stable data-type, and every href is the DIRECT destination (no
# redirect wrapper). We parse the three text-bearing, real-source kinds (see
# BRAVE_WEB_SERP_PARSING.md):
#   - KNOWLEDGE_PANEL: section#infobox entity description + its Wikipedia source.
#   - ORGANIC: div.snippet[data-type="web"] (.title, a.l1, .site-name-content
#     brand, .generic-snippet .content with a leading "N ago -" date stripped).
#   - TOP_STORY / DISCUSSION: a.enrichment-card-item cards in the "In the News"
#     data-type="cluster" carousel (direct publisher links), split by host.
#   Excluded: ads (data-type="ad") and the weather widget (.rich-weather-content).
# The CNN card appears in BOTH the cluster and the organic list -> deduped.
_BRAVE_WEB_FIXTURE = """
<html><body>
  <section id="infobox" class="svelte-c7jm64">
    <header><a class="title-link" href="https://en.wikipedia.org/wiki/Strait_of_Hormuz">Strait of Hormuz</a></header>
    <section class="svelte-1adoobh">The Strait of Hormuz is a strait between the Persian Gulf and the Gulf of Oman. …
      <a class="svelte-1adoobh" href="https://en.wikipedia.org/wiki/Strait_of_Hormuz">Wikipedia</a></section>
  </section>
  <div id="results">
    <div class="snippet" data-type="ad" id="search-ad" data-headline-text="Buy Iran tickets"
         data-landing-page="https://ads.example.com/iran"><a href="https://ads.example.com/iran">Buy Iran tickets</a></div>
    <div class="snippet" data-type="web" data-pos="1">
      <a class="l1" href="https://www.cnn.com/2026/06/20/world/strait" target="_self">
        <div class="site-name-wrapper">
          <div class="favicon-wrapper"><img class="favicon"/></div>
          <div class="site-name-content">
            <div class="t-secondary text-ellipsis">CNN</div>
            <div class="url-wrapper"><cite class="snippet-url">cnn.com<span>&#8250; world</span></cite></div>
          </div>
        </div>
        <div class="title search-snippet-title" title="Iran and US make opposing claims on Strait of Hormuz">Iran and US make opposing claims on Strait of Hormuz</div>
      </a>
      <div class="generic-snippet"><div class="content"><span class="t-secondary">2 hours ago -</span> The US military denied Iran's claim.</div></div>
    </div>
    <div class="snippet" data-type="web" data-pos="2">
      <a class="l1" href="https://en.wikipedia.org/wiki/2026_Iran_war">
        <div class="site-name-wrapper"><div class="site-name-content">
          <div class="t-secondary">Wikipedia</div>
          <div class="url-wrapper"><cite class="snippet-url">en.wikipedia.org</cite></div>
        </div></div>
        <div class="title" title="2026 Iran war">2026 Iran war</div>
      </a>
    </div>
    <div class="snippet standalone" data-type="cluster" data-pos="3">
      <h5>In the News</h5>
      <div class="news-cluster-carousel"><div class="carousel-items">
        <a class="enrichment-card-item" href="https://www.nbcnews.com/world/iran/story" rel="noopener" target="_blank">
          <div class="enrichment-card-content">
            <div class="enrichment-card-site"><img class="favicon"/><span>nbcnews.com</span></div>
            <div class="t-secondary line-clamp-2">Iran says Strait of Hormuz is closed</div>
          </div></a>
        <a class="enrichment-card-item" href="https://www.reddit.com/r/worldnews/post" rel="noopener" target="_blank">
          <div class="enrichment-card-content">
            <div class="enrichment-card-site"><span>reddit.com</span></div>
            <div class="line-clamp-2">Reddit megathread on the Strait of Hormuz</div>
          </div></a>
        <a class="enrichment-card-item" href="https://www.cnn.com/2026/06/20/world/strait" rel="noopener" target="_blank">
          <div class="enrichment-card-content">
            <div class="enrichment-card-site"><span>cnn.com</span></div>
            <div class="line-clamp-2">CNN duplicate of the organic result</div>
          </div></a>
      </div></div>
    </div>
    <div class="rich-weather-content">San Jose, CA clear sky 59° F</div>
  </div>
</body></html>
"""


def _brave_web_entries(html=_BRAVE_WEB_FIXTURE):
    return [
        e
        for e in (
            _BRAVE_WEB.parse(it, _req("us iran"))
            for it in _BRAVE_WEB.find_items(_soup(html))
        )
        if e is not None
    ]


def _brave_by_kind(kind):
    return [e for e in _brave_web_entries() if e.kind is kind]


def test_brave_source_supports_web_vertical():
    assert SearchVertical.WEB in _BRAVE.verticals


def test_brave_web_parses_panel_organic_and_news_only():
    from collections import Counter

    # knowledge_panel + 2 organic + 1 top_story (NBC) + 1 discussion (Reddit).
    # CNN cluster card dups the CNN organic; the ad and weather widget are dropped.
    counts = Counter(e.kind for e in _brave_web_entries())
    assert dict(counts) == {
        WebResultKind.KNOWLEDGE_PANEL: 1,
        WebResultKind.ORGANIC: 2,
        WebResultKind.TOP_STORY: 1,
        WebResultKind.DISCUSSION: 1,
    }


def test_brave_web_knowledge_panel_first_with_description_and_source():
    entries = _brave_web_entries()
    assert entries[0].kind is WebResultKind.KNOWLEDGE_PANEL  # most-direct answer
    kp = entries[0]
    assert kp.snippet.startswith("The Strait of Hormuz is a strait")
    assert "Wikipedia" not in kp.snippet  # trailing attribution stripped
    assert kp.domain == "en.wikipedia.org"
    assert kp.source == "Wikipedia"


def test_brave_web_organic_has_direct_url_title_source_snippet():
    cnn = _brave_by_kind(WebResultKind.ORGANIC)[0]
    assert cnn.title == "Iran and US make opposing claims on Strait of Hormuz"
    assert (
        cnn.url == "https://www.cnn.com/2026/06/20/world/strait"
    )  # direct, no redirect
    assert cnn.domain == "cnn.com"
    assert cnn.source == "CNN"  # brand from .site-name-content
    # The leading "2 hours ago -" date span is stripped from the snippet.
    assert cnn.snippet == "The US military denied Iran's claim."


def test_brave_web_organic_snippet_optional():
    wiki = _brave_by_kind(WebResultKind.ORGANIC)[1]
    assert wiki.domain == "en.wikipedia.org"
    assert wiki.snippet is None


def test_brave_web_news_card_extracts_headline_and_source_by_host():
    nbc = _brave_by_kind(WebResultKind.TOP_STORY)[0]
    assert nbc.title == "Iran says Strait of Hormuz is closed"
    assert nbc.url == "https://www.nbcnews.com/world/iran/story"
    assert nbc.domain == "nbcnews.com"
    assert nbc.source == "nbcnews.com"  # the card's site label


def test_brave_web_discussion_split_by_host():
    # A reddit card in the news cluster is a DISCUSSION, not a TOP_STORY.
    disc = _brave_by_kind(WebResultKind.DISCUSSION)
    assert [d.domain for d in disc] == ["reddit.com"]
    assert disc[0].title == "Reddit megathread on the Strait of Hormuz"


def test_brave_web_drops_ads_and_weather_widget():
    titles = [e.title for e in _brave_web_entries()]
    assert "Buy Iran tickets" not in titles
    assert all("ads.example.com" not in (e.domain or "") for e in _brave_web_entries())
    assert all("clear sky" not in (e.snippet or "") for e in _brave_web_entries())


def test_brave_web_dedupes_url_across_cluster_and_organic():
    # cnn.com appears in both the cluster and the organic list -> once only.
    assert sum(e.domain == "cnn.com" for e in _brave_web_entries()) == 1


def test_brave_web_build_url_is_raw_query_only():
    url = _BRAVE_WEB.build_url(
        _req("us iran", sort=Ordering.DATE, recency=Recency.HOUR)
    )
    assert url == "https://search.brave.com/search?q=us+iran"


def test_brave_web_build_url_pagination_adds_offset_only():
    url = _BRAVE_WEB.build_url(_req("us iran", page=3))
    assert url == "https://search.brave.com/search?q=us+iran&offset=2"
    assert "tf=" not in url
