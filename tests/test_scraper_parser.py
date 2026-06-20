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

# Mirrors the real web-search DOM: organic results in div.MjjYud, title in an
# <h3> wrapped by a clean destination <a href>, <cite> a display-only
# breadcrumb. The other two MjjYud blocks are the mixed content (a no-h3
# People-also-ask block, and an internal Google nav link) that must be skipped.
_GOOGLE_WEB_FIXTURE = """
<html><body>
  <div id="search">
    <div class="MjjYud">
      <div class="yuRUbf">
        <a href="https://www.spacex.com/vehicles/starship/" jsname="x">
          <h3 class="LC20lb">Starship | SpaceX</h3>
          <cite>https://www.spacex.com › vehicles › starship</cite>
        </a>
      </div>
      <div class="VwiC3b">Starship is the fully reusable launch system.</div>
    </div>
    <div class="MjjYud">
      <div class="related-question-pair">When is the next Starship launch?</div>
    </div>
    <div class="MjjYud">
      <a href="/search?q=starship+related"><h3>More results</h3></a>
    </div>
  </div>
</body></html>
"""


def _google_web_items(html=_GOOGLE_WEB_FIXTURE):
    return _GOOGLE_WEB.find_items(_soup(html))


def test_google_source_supports_web_vertical():
    assert SearchVertical.WEB in _GOOGLE.verticals


def test_google_web_find_items_returns_all_mjjyud_blocks():
    # find_items returns every MjjYud block; parse() is what filters non-organic.
    assert len(_google_web_items()) == 3


def test_google_web_parse_extracts_clean_url_and_domain():
    entry = _GOOGLE_WEB.parse(_google_web_items()[0], _req("starship"))
    assert entry is not None
    assert entry.title == "Starship | SpaceX"
    assert entry.url == "https://www.spacex.com/vehicles/starship/"  # clean, no /url?q=
    assert entry.domain == "spacex.com"  # from URL, not the <cite> breadcrumb
    assert entry.source is None  # no publisher concept for general web results
    assert entry.topic == "starship"


def test_google_web_skips_block_without_h3():
    # People-also-ask block (no <h3>) is not an organic result.
    assert _GOOGLE_WEB.parse(_google_web_items()[1], _req("starship")) is None


def test_google_web_skips_non_http_anchor():
    # Internal Google nav link (relative href) is not an organic result.
    assert _GOOGLE_WEB.parse(_google_web_items()[2], _req("starship")) is None


def test_google_web_build_url_omits_news_tab():
    url = _GOOGLE_WEB.build_url(
        _req("us iran", sort=Ordering.RELEVANCE, recency=Recency.ANY, page=1)
    )
    assert "tbm=nws" not in url and "nsd:1" not in url
    assert "q=us+iran" in url
    assert "start=0" in url
    # Relevance + any-recency means no tbs flags at all.
    assert "tbs=" not in url


def test_google_web_build_url_carries_sort_and_recency():
    url = _GOOGLE_WEB.build_url(
        _req("x", sort=Ordering.DATE, recency=Recency.WEEK, page=3)
    )
    assert "tbs=sbd:1,qdr:w" in url
    assert "start=20" in url  # page 3 -> offset 20


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
