"""Integration tests for the junction data model against a real Postgres."""

from common.model import NewsEntry


def _article(topic, title, url, source="Wire", engine=None, snippet=None):
    return NewsEntry.create_new(
        topic=topic,
        title=title,
        url=url,
        source=source,
        engine=engine,
        snippet=snippet,
    )


def _news_row_count(db):
    with db._Connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) AS c FROM news")
        return cur.fetchone()["c"]


def test_same_article_two_topics_is_one_news_two_matches(db):
    db.add_topic("alpha")
    db.add_topic("beta")
    url = "https://example.com/shared-story"

    new_events = db.insert_news_entries(
        [_article("alpha", "Shared", url), _article("beta", "Shared", url)]
    )

    assert new_events == 2  # one feed event per topic
    assert _news_row_count(db) == 1  # content stored once
    assert db.get_news_count("alpha") == 1
    assert db.get_news_count("beta") == 1
    assert db.get_active_feed_count() == 2


def test_reinsert_same_topic_and_url_is_noop(db):
    db.add_topic("alpha")
    url = "https://example.com/x"
    assert db.insert_news_entries([_article("alpha", "X", url)]) == 1
    assert db.insert_news_entries([_article("alpha", "X", url)]) == 0
    assert db.get_news_count("alpha") == 1


def test_tracking_param_variants_dedup_to_one_article(db):
    db.add_topic("alpha")
    db.insert_news_entries(
        [
            _article("alpha", "T", "https://example.com/a?utm_source=x"),
            _article("alpha", "T", "https://example.com/a?gclid=y#frag"),
        ]
    )
    assert db.get_news_count("alpha") == 1
    assert _news_row_count(db) == 1


def test_cursor_pagination_descending_no_overlap(db):
    db.add_topic("alpha")
    db.insert_news_entries(
        [_article("alpha", f"T{i}", f"https://example.com/{i}") for i in range(5)]
    )

    page1 = db.get_news_entries("alpha", limit=2)
    page2 = db.get_news_entries("alpha", limit=2, before_id=page1[-1].id)

    assert len(page1) == 2
    assert len(page2) == 2
    ids = [e.id for e in page1 + page2]
    assert ids == sorted(ids, reverse=True)  # newest first
    assert len(set(ids)) == 4  # no overlap across pages


def test_get_news_entry_resolves_junction_id(db):
    db.add_topic("alpha")
    db.insert_news_entries([_article("alpha", "Hello", "https://example.com/h")])
    entry = db.get_news_entries("alpha", limit=1)[0]

    fetched = db.get_news_entry(entry.id)

    assert fetched is not None
    assert fetched.id == entry.id
    assert fetched.title == "Hello"
    assert fetched.topic == "alpha"


def test_topic_lifecycle_soft_delete_and_reactivate(db):
    db.add_topic("alpha")
    assert db.topic_exists("alpha")

    db.delete_topic("alpha")
    assert not db.topic_exists("alpha")
    assert "alpha" not in [t.name for t in db.get_topics()]
    assert "alpha" in [t.name for t in db.get_topics(include_inactive=True)]

    db.add_topic("alpha")  # re-adding reactivates
    assert db.topic_exists("alpha")


def test_purge_old_scraper_logs(db):
    db.add_topic("alpha")
    with db._Connection() as conn:
        conn.cursor().execute(
            "INSERT INTO scraper_logs (topic, scraped_at, success, entry_count) VALUES "
            "('alpha', NOW() - INTERVAL '40 days', TRUE, 0), "
            "('alpha', NOW(), TRUE, 5)"
        )

    deleted = db.purge_old_scraper_logs(30)

    assert deleted == 1
    remaining = db.get_scraper_logs(10)
    assert len(remaining) == 1
    assert remaining[0].entry_count == 5


def test_engine_attribution_correlates_multiple_engines_to_one_article(db):
    db.add_topic("alpha")
    url = "https://example.com/shared"
    # Same (topic, article) found by two engines in one cycle.
    new_events = db.insert_news_entries(
        [
            _article("alpha", "Shared", url, engine="google"),
            _article("alpha", "Shared", url, engine="bing"),
        ]
    )

    assert new_events == 1  # one feed event despite two engines
    assert _news_row_count(db) == 1  # article stored once
    entry = db.get_news_entries("alpha", limit=1)[0]
    assert entry.engines == ["bing", "google"]  # both engines, sorted
    assert db.get_feed_engines() == ["bing", "google"]


def test_engine_attribution_accumulates_across_cycles(db):
    db.add_topic("alpha")
    url = "https://example.com/x"
    assert db.insert_news_entries([_article("alpha", "X", url, engine="google")]) == 1
    # A later cycle: another engine surfaces the already-known article.
    assert db.insert_news_entries([_article("alpha", "X", url, engine="bing")]) == 0

    entry = db.get_news_entries("alpha", limit=1)[0]
    assert entry.engines == ["bing", "google"]


def test_engine_filter_restricts_feed_but_keeps_all_badges(db):
    db.add_topic("alpha")
    a = "https://example.com/a"
    b = "https://example.com/b"
    db.insert_news_entries(
        [
            _article("alpha", "A", a, engine="google"),
            _article("alpha", "A", a, engine="bing"),
            _article("alpha", "B", b, engine="bing"),
        ]
    )

    google = db.get_news_entries("alpha", engine="google")
    bing = db.get_news_entries("alpha", engine="bing")

    assert {e.title for e in google} == {"A"}  # B not surfaced by google
    assert {e.title for e in bing} == {"A", "B"}
    # Filtering by google still shows A's full engine list (not just google).
    assert google[0].engines == ["bing", "google"]
    assert db.get_news_count("alpha", engine="google") == 1
    assert db.get_news_count("alpha", engine="bing") == 2
    assert db.get_news_count("alpha") == 2


def test_snippet_keeps_the_longest_across_engines_and_rescrapes(db):
    db.add_topic("alpha")
    url = "https://example.com/story"
    short = "Short blurb."
    longer = "A considerably longer and more descriptive excerpt of the story."

    # Same article from two engines in one batch (short first, longer second).
    db.insert_news_entries(
        [
            _article("alpha", "T", url, engine="google", snippet=short),
            _article("alpha", "T", url, engine="bing", snippet=longer),
        ]
    )
    assert db.get_news_entries("alpha", limit=1)[0].snippet == longer

    # A re-scrape with an even shorter snippet must NOT shrink it.
    db.insert_news_entries([_article("alpha", "T", url, engine="google", snippet="x")])
    assert db.get_news_entries("alpha", limit=1)[0].snippet == longer

    # A re-scrape with a longer snippet updates it.
    longest = longer + " With extra trailing context appended later."
    db.insert_news_entries(
        [_article("alpha", "T", url, engine="yahoo", snippet=longest)]
    )
    assert db.get_news_entries("alpha", limit=1)[0].snippet == longest


def test_all_feed_excludes_inactive_topics(db):
    db.add_topic("alpha")
    db.add_topic("beta")
    db.insert_news_entries(
        [
            _article("alpha", "A", "https://example.com/a"),
            _article("beta", "B", "https://example.com/b"),
        ]
    )

    db.delete_topic("beta")

    assert {e.topic for e in db.get_news_entries_all(limit=50)} == {"alpha"}
    assert db.get_active_feed_count() == 1
