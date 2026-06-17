"""Utility functions for TopicStreams application."""

import re
import uuid
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

# Unambiguous ad/click tracking params — safe to strip because they never
# encode article identity. Ambiguous ones (e.g. "source", "id", "p") are kept
# on purpose: some sites put the article identity in the query string, and a
# wrong strip would merge distinct articles (worse than an occasional dupe).
_TRACKING_PARAMS = frozenset(
    {
        "gclid",
        "fbclid",
        "msclkid",
        "mc_eid",
        "mc_cid",
        "igshid",
        "yclid",
        "dclid",
        "twclid",
    }
)


def normalize_url(url: str) -> str:
    """Canonicalize a URL for use as article identity.

    Forces the scheme to https (http/https of the same page are one article),
    lowercases the host, drops the fragment, strips a trailing slash, and
    removes utm_* plus the known tracking params above. Everything else is
    preserved.

    We only strip *cross-platform* tracking params (utm_*, ad-click ids).
    Site-specific tracking tags (e.g. a single publisher's own query param)
    are left in place: they don't follow a public convention, so chasing them
    means a brittle per-site denylist, and the occasional duplicate they cause
    is an acceptable trade vs. wrongly merging distinct articles.
    """
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not (k.lower().startswith("utm_") or k.lower() in _TRACKING_PARAMS)
        ]
    )
    return urlunsplit(("https", parts.netloc.lower(), path, query, ""))


def news_id_for_url(url: str) -> uuid.UUID:
    """Deterministic content id for an article, derived from its normalized URL.

    The same article always yields the same id, so inserts are a pure upsert
    with no prior lookup, and the topic plays no part in the identity.
    """
    return uuid.uuid5(uuid.NAMESPACE_URL, normalize_url(url))


def normalize_topic(topic: str) -> str:
    """
    Normalize a topic name for consistent storage and comparison.

    Normalization rules:
    1. Convert to lowercase
    2. Remove most punctuation/symbols (keep hyphens and spaces)
    3. Collapse multiple spaces into one
    4. Strip leading/trailing whitespace
    5. Preserve Unicode characters (Chinese, Japanese, Korean, Arabic, etc.)

    Examples:
        "Bitcoin" -> "bitcoin"
        "ARTIFICIAL  INTELLIGENCE" -> "artificial intelligence"
        "AI, Machine Learning" -> "ai machine learning"
        "比特币" -> "比特币"
        "한국" -> "한국"
        "العربية" -> "العربية"
    """
    # Convert to lowercase
    topic = topic.lower()

    # Remove most punctuation, but keep:
    # - Letters (including Unicode)
    # - Numbers
    # - Spaces
    # - Hyphens (useful for compound words like "machine-learning")
    # Remove: commas, periods, quotes, brackets, etc.
    topic = re.sub(r"[^\w\s\-]", " ", topic, flags=re.UNICODE)

    # Replace multiple spaces (or hyphens surrounded by spaces) with single space
    topic = re.sub(r"\s+", " ", topic)

    # Clean up hyphens: remove if at start/end, or if surrounded by spaces
    topic = re.sub(r"\s*-\s*", "-", topic)  # Remove spaces around hyphens
    topic = re.sub(r"(^-+)|(-+$)", "", topic)  # Remove leading/trailing hyphens

    # Strip leading and trailing whitespace
    return topic.strip()
