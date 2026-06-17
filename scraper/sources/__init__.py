"""Pluggable search-engine scrapers.

Add a new engine by implementing ``SearchSource`` (see ``base.py``) and
registering it in ``_SOURCES``.
"""

from .base import Ordering, Recency, SearchSource
from .bing import BingSource
from .google import GoogleSource

_SOURCES: dict[str, type[SearchSource]] = {
    GoogleSource.name: GoogleSource,
    BingSource.name: BingSource,
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
    "Ordering",
    "Recency",
    "get_source",
]
