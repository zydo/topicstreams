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
    FOREIGN KEY (topic) REFERENCES topics(name) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_scraper_logs_scraped_at ON scraper_logs(scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_scraper_logs_topic_scraped_at ON scraper_logs(topic, scraped_at DESC);

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
