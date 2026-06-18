CREATE TABLE IF NOT EXISTS topics (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);

-- One row per article, deduped by a content id (UUIDv5 over the normalized
-- URL). The topic is intentionally NOT part of the identity.
CREATE TABLE IF NOT EXISTS news (
    id UUID PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    domain VARCHAR(255) NOT NULL,
    source VARCHAR(255),
    -- Short excerpt/blurb under the headline, for display only. Descriptive,
    -- never part of identity. Engines (and re-scrapes) excerpt differently; we
    -- keep the longest seen (see insert_news_entries).
    snippet TEXT,
    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- One row the first time a topic matches an article. This is the feed stream:
-- each row is a distinct feed event, so an article matched by several topics
-- appears once per topic. UNIQUE(topic, news_id) stops a topic re-emitting the
-- same article every scrape cycle.
CREATE TABLE IF NOT EXISTS topic_news (
    id BIGSERIAL PRIMARY KEY,
    topic VARCHAR(255) NOT NULL,
    news_id UUID NOT NULL,
    matched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(topic, news_id),
    FOREIGN KEY (topic) REFERENCES topics(name) ON DELETE CASCADE,
    FOREIGN KEY (news_id) REFERENCES news(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_topic_news_topic_id ON topic_news(topic, id DESC);
CREATE INDEX IF NOT EXISTS idx_topic_news_id ON topic_news(id DESC);

-- Which search engines surfaced each feed event, and when each first scraped
-- it. The article is still stored once in `news`; this many-to-many lets a
-- single feed event (topic_news row) be attributed to several engines (e.g.
-- both Google and Bing found the same article for a topic). Cascades with the
-- feed event, so retention purges clean these up automatically.
CREATE TABLE IF NOT EXISTS topic_news_engines (
    topic_news_id BIGINT NOT NULL,
    engine VARCHAR(32) NOT NULL,
    seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (topic_news_id, engine),
    FOREIGN KEY (topic_news_id) REFERENCES topic_news(id) ON DELETE CASCADE
);
-- Filtering the feed by a single engine (EXISTS over this table).
CREATE INDEX IF NOT EXISTS idx_tne_engine ON topic_news_engines(engine);

CREATE TABLE IF NOT EXISTS scraper_logs (
    id SERIAL PRIMARY KEY,
    topic VARCHAR(255) NOT NULL,
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    success BOOLEAN DEFAULT TRUE,
    http_status_code INTEGER,
    error_message TEXT,
    -- Items parsed from this scrape. A run of successful scrapes with 0 parsed
    -- items signals the engine changed its markup (selector rot), not "no news".
    entry_count INTEGER DEFAULT 0,
    -- Which search engine this scrape used, so health can be per-engine.
    engine VARCHAR(32) DEFAULT 'google',
    -- Wall-clock to fetch+load this results page (page.goto, ms). Deliberately
    -- excludes the anti-detection settle/scroll waits so it reflects real fetch
    -- latency. Nullable for legacy rows / unmeasurable attempts.
    duration_ms INTEGER,
    FOREIGN KEY (topic) REFERENCES topics(name) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_scraper_logs_scraped_at ON scraper_logs(scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_scraper_logs_topic_scraped_at ON scraper_logs(topic, scraped_at DESC);
-- Engine-scoped recency index for the per-engine metrics aggregation.
CREATE INDEX IF NOT EXISTS idx_scraper_logs_engine_scraped_at ON scraper_logs(engine, scraped_at DESC);

-- One row per scrape cycle (a full pass over all topics). Captures the
-- wall-clock cycle duration and per-pass counts that scraper_logs (one row per
-- page-attempt) can't cleanly express. Purged on the same retention window as
-- news/logs. No unique constraint, so inserts aren't retried (see insert_cycle).
CREATE TABLE IF NOT EXISTS scraper_cycles (
    id SERIAL PRIMARY KEY,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP NOT NULL,
    duration_seconds DOUBLE PRECISION NOT NULL,
    topics_count INTEGER NOT NULL,
    entries_parsed INTEGER NOT NULL,
    new_events INTEGER NOT NULL,
    success BOOLEAN NOT NULL DEFAULT TRUE,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_scraper_cycles_started_at ON scraper_cycles(started_at DESC);

-- NOTIFY on each new feed event (topic match), not on news-content insert, so
-- a topic referencing an already-stored article still streams to that topic.
-- Format: "topic:topic_news_id".
CREATE OR REPLACE FUNCTION notify_topic_news() RETURNS TRIGGER AS $$ BEGIN
    PERFORM pg_notify('news_updates', NEW.topic || ':' || NEW.id::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trigger_topic_news_insert
AFTER INSERT ON topic_news FOR EACH ROW EXECUTE FUNCTION notify_topic_news();
