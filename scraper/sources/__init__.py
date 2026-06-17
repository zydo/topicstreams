"""Pluggable search-engine scrapers.

Add a new engine by implementing ``SearchSource`` (see ``base.py``) and
registering it in ``_SOURCES``.
"""

from .base import Ordering, Recency, SearchSource
from .bing import BingSource
from .brave import BraveSource
from .google import GoogleSource
from .yahoo import YahooSource

# DuckDuckGo is intentionally not supported: it hard-blocks automated access
# (the news vertical redirects to a static block page and the token-gated
# news.js is unreachable). See docs/DUCKDUCKGO_UNSUPPORTED.md.
_SOURCES: dict[str, type[SearchSource]] = {
    GoogleSource.name: GoogleSource,
    BingSource.name: BingSource,
    YahooSource.name: YahooSource,
    BraveSource.name: BraveSource,
}


def get_source(name: str) -> SearchSource:
    """Instantiate a search source by name (e.g. 'google')."""
    try:
        return _SOURCES[name]()
    except KeyError:
        raise ValueError(
            f"Unknown search source '{name}'. Available: {sorted(_SOURCES)}"
        )


__all__ = [
    "SearchSource",
    "GoogleSource",
    "BingSource",
    "YahooSource",
    "BraveSource",
    "Ordering",
    "Recency",
    "get_source",
]
