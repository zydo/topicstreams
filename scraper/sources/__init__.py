"""Pluggable search-engine scrapers.

Add a new engine by implementing ``SearchSource`` (see ``base.py``) and
registering it in ``_SOURCES``.
"""

from .base import Ordering, Recency, SearchSource
from .bing import BingSource
from .brave import BraveSource
from .duckduckgo import DuckDuckGoSource
from .google import GoogleSource
from .yahoo import YahooSource

_SOURCES: dict[str, type[SearchSource]] = {
    GoogleSource.name: GoogleSource,
    BingSource.name: BingSource,
    YahooSource.name: YahooSource,
    BraveSource.name: BraveSource,
    DuckDuckGoSource.name: DuckDuckGoSource,
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
    "DuckDuckGoSource",
    "Ordering",
    "Recency",
    "get_source",
]
