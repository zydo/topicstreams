"""Integration tests for the junction data model against a real Postgres."""

from common.model import NewsEntry


def _article(topic, title, url, source="Wire"):
    return NewsEntry.create_new(topic=topic, title=title, url=url, source=source)


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
