"""Tests for topic and URL normalization, and the URL-derived news id."""

import uuid

from common.utils import news_id_for_url, normalize_topic, normalize_url


class TestNormalizeTopic:
    def test_lowercase_and_trim(self):
        assert normalize_topic("  Bitcoin ") == "bitcoin"

    def test_collapse_spaces(self):
        assert normalize_topic("ARTIFICIAL   INTELLIGENCE") == "artificial intelligence"

    def test_strips_punctuation(self):
        assert normalize_topic("AI, Machine Learning!") == "ai machine learning"

    def test_unicode_preserved(self):
        assert normalize_topic("比特币") == "比特币"

    def test_empty_after_normalization(self):
        assert normalize_topic("!!!") == ""

    def test_hyphen_kept_without_surrounding_spaces(self):
        assert normalize_topic("machine - learning") == "machine-learning"


class TestNormalizeUrl:
    def test_drops_fragment(self):
        assert normalize_url("https://x.com/a#section") == "https://x.com/a"

    def test_lowercases_host_only(self):
        # host lowercased, path case preserved
        assert normalize_url("https://EXAMPLE.com/Path") == "https://example.com/Path"

    def test_strips_trailing_slash(self):
        assert normalize_url("https://x.com/a/") == "https://x.com/a"

    def test_strips_utm_and_known_trackers(self):
        assert (
            normalize_url("https://x.com/a?utm_source=g&gclid=1&id=5")
            == "https://x.com/a?id=5"
        )

    def test_keeps_ambiguous_query_params(self):
        # 'source' is ambiguous (may carry article identity) -> kept
        assert (
            normalize_url("https://x.com/a?source=google")
            == "https://x.com/a?source=google"
        )


class TestNewsIdForUrl:
    def test_deterministic_across_tracking_variants(self):
        a = news_id_for_url("https://x.com/a?utm_source=g#frag")
        b = news_id_for_url("https://X.com/a/?gclid=1")
        assert a == b

    def test_is_uuid5(self):
        nid = news_id_for_url("https://x.com/a")
        assert isinstance(nid, uuid.UUID)
        assert nid.version == 5

    def test_distinct_urls_get_distinct_ids(self):
        assert news_id_for_url("https://x.com/a") != news_id_for_url("https://x.com/b")
