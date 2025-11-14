"""Utility functions for TopicStreams application."""

import re


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
