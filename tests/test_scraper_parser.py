"""Tests for the Google News-tab HTML parser.

A synthetic fixture mirroring the current markup (`div.WCv1we` items with a
`div[role="heading"]` title, an `a[href]` link, and a `div.MgUUmf` source).
These guard our parsing logic against regressions; the *runtime* selector-rot
detection covers Google changing its markup.
"""

from bs4 import BeautifulSoup

from scraper.sources import BingSource, GoogleSource, Ordering, Recency

_GOOGLE = GoogleSource()
_BING = BingSource()

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
    assert "sortbydate=1" in url
    assert "first=11" in url  # page 2 -> offset 11
    assert "interval" in url
