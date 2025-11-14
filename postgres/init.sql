CREATE TABLE IF NOT EXISTS topics (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);
CREATE TABLE IF NOT EXISTS news_entries (
    id SERIAL PRIMARY KEY,
    topic VARCHAR(255) NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    domain VARCHAR(255) NOT NULL,
    source VARCHAR(255),
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(topic, title, domain),
    FOREIGN KEY (topic) REFERENCES topics(name) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_topic_scraped_at ON news_entries(topic, scraped_at DESC);
CREATE TABLE IF NOT EXISTS scraper_logs (
    id SERIAL PRIMARY KEY,
    topic VARCHAR(255) NOT NULL,
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    success BOOLEAN DEFAULT TRUE,
    http_status_code INTEGER,
    error_message TEXT,
    FOREIGN KEY (topic) REFERENCES topics(name) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_scraper_logs_scraped_at ON scraper_logs(scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_scraper_logs_topic_scraped_at ON scraper_logs(topic, scraped_at DESC);
CREATE OR REPLACE FUNCTION notify_news_entry() RETURNS TRIGGER AS $$ BEGIN --
    -- Send notification with topic and entry_id, format: "topic:new_entry_id"
    PERFORM pg_notify('news_updates', NEW.topic || ':' || NEW.id::text);
RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trigger_news_entry_insert
AFTER
INSERT ON news_entries FOR EACH ROW EXECUTE FUNCTION notify_news_entry();