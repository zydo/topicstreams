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

    # Bing implements only the news vertical; asking for web is a ValueError.
    with pytest.raises(ValueError):
        _BING.parser_for(SearchVertical.WEB)


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
