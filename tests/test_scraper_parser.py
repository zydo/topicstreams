"""Tests for the Google News-tab HTML parser.

A synthetic fixture mirroring the current markup (`div.WCv1we` items with a
`div[role="heading"]` title, an `a[href]` link, and a `div.MgUUmf` source).
These guard our parsing logic against regressions; the *runtime* selector-rot
detection covers Google changing its markup.
"""

from bs4 import BeautifulSoup

from scraper.scraper import _find_news_items, _parse_item

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
    return _find_news_items(_soup(html))


def test_find_news_items_finds_all():
    assert len(_items()) == 2


def test_parse_unwraps_google_redirect_and_extracts_fields():
    entry = _parse_item(_items()[0], topic="spacex")
    assert entry is not None
    assert entry.title == "Starship clears static-fire test"
    assert entry.url == "https://www.spacenews.com/article-1"  # /url?q= unwrapped
    assert entry.domain == "spacenews.com"  # www stripped
    assert entry.source == "SpaceNews"
    assert entry.topic == "spacex"


def test_parse_direct_url():
    entry = _parse_item(_items()[1], topic="bitcoin")
    assert entry.url == "https://coindesk.com/story-2"
    assert entry.domain == "coindesk.com"


def test_parse_resolves_relative_url():
    html = (
        '<div class="WCv1we"><a href="/foo/bar"><div role="heading">T</div></a></div>'
    )
    entry = _parse_item(_items(html)[0], topic="t")
    assert entry.url == "https://www.google.com/foo/bar"


def test_parse_returns_none_without_title():
    html = '<div class="WCv1we"><a href="https://x.com/a"></a></div>'
    assert _parse_item(_items(html)[0], topic="t") is None


def test_parse_returns_none_without_url():
    html = '<div class="WCv1we"><div role="heading">Title only</div></div>'
    assert _parse_item(_items(html)[0], topic="t") is None
