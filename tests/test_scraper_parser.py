"""Tests for the per-engine HTML parsers (Google, Bing, Yahoo, Brave).

Each engine has a synthetic fixture mirroring its real result markup. These
guard our parsing logic against regressions; the *runtime* selector-rot
detection (parse-0 health signal) covers an engine changing its markup live.
"""

from bs4 import BeautifulSoup

from scraper.sources import (
    BingSource,
    BraveSource,
    GoogleSource,
    Ordering,
    Recency,
    YahooSource,
)

_GOOGLE = GoogleSource()
_BING = BingSource()
_YAHOO = YahooSource()
_BRAVE = BraveSource()

_FIXTURE = """
<html><body>
  <div class="WCv1we">
    <a href="/url?q=https://www.spacenews.com/article-1&sa=U&ved=xyz">
      <div role="heading">Starship clears static-fire test</div>
    </a>
    <div class="MgUUmf">SpaceNews</div>
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
    return _GOOGLE.find_items(_soup(html))


def test_find_news_items_finds_all():
    assert len(_items()) == 2


def test_parse_unwraps_google_redirect_and_extracts_fields():
    entry = _GOOGLE.parse_item(_items()[0], topic="spacex")
    assert entry is not None
    assert entry.title == "Starship clears static-fire test"
    assert entry.url == "https://www.spacenews.com/article-1"  # /url?q= unwrapped
    assert entry.domain == "spacenews.com"  # www stripped
    assert entry.source == "SpaceNews"
    assert entry.topic == "spacex"


def test_parse_direct_url():
    entry = _GOOGLE.parse_item(_items()[1], topic="bitcoin")
    assert entry.url == "https://coindesk.com/story-2"
    assert entry.domain == "coindesk.com"


def test_parse_resolves_relative_url():
    html = (
        '<div class="WCv1we"><a href="/foo/bar"><div role="heading">T</div></a></div>'
    )
    entry = _GOOGLE.parse_item(_items(html)[0], topic="t")
    assert entry.url == "https://www.google.com/foo/bar"


def test_parse_returns_none_without_title():
    html = '<div class="WCv1we"><a href="https://x.com/a"></a></div>'
    assert _GOOGLE.parse_item(_items(html)[0], topic="t") is None


def test_parse_returns_none_without_url():
    html = '<div class="WCv1we"><div role="heading">Title only</div></div>'
    assert _GOOGLE.parse_item(_items(html)[0], topic="t") is None


def test_build_url_default_is_date_past_hour():
    url = _GOOGLE.build_url(
        "us iran", ordering=Ordering.DATE, recency=Recency.HOUR, page=1
    )
    assert "tbm=nws" in url
    assert "sbd:1" in url and "qdr:h" in url and "nsd:1" in url
    assert "start=0" in url
    assert "q=us+iran" in url


def test_build_url_relevance_drops_sort_by_date():
    url = _GOOGLE.build_url(
        "x", ordering=Ordering.RELEVANCE, recency=Recency.DAY, page=2
    )
    assert "sbd:1" not in url
    assert "qdr:d" in url
    assert "start=10" in url  # page 2 -> offset 10


def test_build_url_any_recency_drops_date_range():
    url = _GOOGLE.build_url("x", ordering=Ordering.DATE, recency=Recency.ANY, page=1)
    assert "qdr:" not in url


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
       title="Economist reveals next Bitcoin target"></div>
  <div class="news-card newsitem cardcommon"
       data-url="https://coindesk.com/story"
       data-title="Bitcoin holds above $69k"
       data-author="CoinDesk"></div>
</body></html>
"""


def _bing_items(html=_BING_FIXTURE):
    return _BING.find_items(_soup(html))


def test_bing_find_items():
    assert len(_bing_items()) == 2


def test_bing_parse_from_card_attributes():
    entry = _BING.parse_item(_bing_items()[0], topic="bitcoin")
    assert entry.title == "Economist reveals next Bitcoin target"
    assert entry.url == "https://www.thestreet.com/crypto/x"
    assert entry.domain == "thestreet.com"
    assert entry.source == "TheStreet"


def test_bing_parse_returns_none_without_url():
    html = '<div class="newsitem" data-title="No url"></div>'
    assert _BING.parse_item(_bing_items(html)[0], topic="t") is None


def test_bing_build_url_date_and_recency():
    url = _BING.build_url(
        "us iran", ordering=Ordering.DATE, recency=Recency.DAY, page=2
    )
    assert "q=us+iran" in url
    assert 'sortbydate%3d"1"' in url  # date sort as a qft token
    assert "first=11" in url  # page 2 -> offset 11
    assert 'interval%3d"7"' in url  # DAY -> past 24 hours


def test_bing_build_url_hour_is_past_hour_interval():
    # Past hour is interval "4" (not "7" = past 24h); this is the window the
    # live pipeline actually uses.
    url = _BING.build_url("x", ordering=Ordering.DATE, recency=Recency.HOUR, page=1)
    assert 'interval%3d"4"' in url


# --- Yahoo ----------------------------------------------------------------

# Yahoo wraps result links in an r.search.yahoo.com redirector; the real target
# is URL-encoded in the /RU=<url>/ segment.
_YAHOO_FIXTURE = """
<ol class="searchCenterMiddle">
  <li>
    <h4 class="s-title"><a href="https://r.search.yahoo.com/_ylt=abc/RV=2/RE=1/RO=10/RU=https%3a%2f%2fwww.coindesk.com%2fmarkets%2fstory/RK=2/RS=z">Bitcoin bottom signal flashes</a></h4>
    <span class="s-source">Coindesk</span>
    <span class="s-time">1 hour ago ·</span>
  </li>
  <li>
    <h4 class="s-title"><a href="https://r.search.yahoo.com/x/RU=https%3a%2f%2ffinance.yahoo.com%2fnews%2fmusk/RK=2">Elon Musk net worth</a></h4>
    <span class="s-source">BeInCrypto·  via Yahoo Finance</span>
  </li>
</ol>
"""


def _yahoo_items(html=_YAHOO_FIXTURE):
    return _YAHOO.find_items(_soup(html))


def test_yahoo_find_items():
    assert len(_yahoo_items()) == 2


def test_yahoo_unwraps_redirect_and_extracts_fields():
    entry = _YAHOO.parse_item(_yahoo_items()[0], topic="bitcoin")
    assert entry.title == "Bitcoin bottom signal flashes"
    assert entry.url == "https://www.coindesk.com/markets/story"  # RU= unwrapped
    assert entry.domain == "coindesk.com"
    assert entry.source == "Coindesk"


def test_yahoo_cleans_source_suffix():
    entry = _YAHOO.parse_item(_yahoo_items()[1], topic="t")
    assert entry.source == "BeInCrypto"  # "· via Yahoo Finance" stripped


def test_yahoo_build_url_query_and_offset():
    # Yahoo has no reliable date-sort/freshness param, so ordering/recency are
    # intentionally not reflected in the URL (engine-default ranking).
    url = _YAHOO.build_url(
        "us iran", ordering=Ordering.DATE, recency=Recency.DAY, page=2
    )
    assert "p=us+iran" in url
    assert "b=11" in url  # page 2 -> 1-based offset 11


# --- Brave ----------------------------------------------------------------

_BRAVE_FIXTURE = """
<div class="snippet" data-type="news">
  <a class="l1" href="https://www.coindesk.com/markets/story" rel="noopener">x</a>
  <div class="title">BlackRock launches bitcoin income fund</div>
  <div class="site-name-wrapper"><div class="site-name-content">CoinDesk•18 hours ago</div></div>
</div>
<div class="snippet" data-type="news">
  <a class="l1" href="https://cryptonews.net/story" rel="noopener">y</a>
  <div class="title">Bitcoin miners pivot to AI</div>
  <div class="site-name-wrapper"><div class="site-name-content">CryptoNews•2 hours ago</div></div>
</div>
"""


def _brave_items(html=_BRAVE_FIXTURE):
    return _BRAVE.find_items(_soup(html))


def test_brave_find_items():
    assert len(_brave_items()) == 2


def test_brave_extracts_title_url_and_source():
    entry = _BRAVE.parse_item(_brave_items()[0], topic="bitcoin")
    assert entry.title == "BlackRock launches bitcoin income fund"
    assert entry.url == "https://www.coindesk.com/markets/story"
    assert entry.domain == "coindesk.com"
    assert entry.source == "CoinDesk"  # "•18 hours ago" stripped


def test_brave_returns_none_without_title():
    html = '<div class="snippet" data-type="news"><a href="https://x.com/a">x</a></div>'
    assert _BRAVE.parse_item(_brave_items(html)[0], topic="t") is None


def test_brave_build_url_recency_and_offset():
    url = _BRAVE.build_url(
        "us iran", ordering=Ordering.DATE, recency=Recency.WEEK, page=2
    )
    assert "q=us+iran" in url
    assert "tf=pw" in url  # week
    assert "offset=1" in url
