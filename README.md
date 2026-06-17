# TopicStreams

Real-time news aggregation system that continuously scrapes Google (not Google News) for any topics (search keywords) and streams updates via WebSocket.

## Why TopicStreams?

### The Limitations with Google News & RSS

Google News ([https://news.google.com](https://news.google.com)) and Google News RSS (`https://news.google.com/rss?search=<keyword>`) provide curated news collections based on Google's algorithms. While convenient, they have limitations:

- Results are **not necessarily the latest** - articles may be hours or days old
- Google filters by **quality and relevance**, potentially missing breaking news
- No control over what Google considers "newsworthy"

<p align="center">
<img src="docs/pic/google_news_search_example.png" alt="Google News Search result - hours or days old" width="600"/><br/>
<em>Google News Search result - hours or days old</em>
</p>

<p align="center">
<img src="docs/pic/google_news_rss_example.png" alt="Google News RSS - same as Google News search" width="600"/><br/>
<em>Google News RSS - same as Google News search</em>
</p>

### TopicStreams' Approach

TopicStreams scrapes **Google Search → News Tab** with time filters, giving you:

- **Real-time results** - All news indexed by Google, regardless of quality rating
- **Unfiltered access** - No curation, you decide what's relevant
- **Near-instant updates** - Scrape frequently enough and catch news as it breaks
- **Full control** - Customize topics (search keywords) and scrape intervals

<p align="center">
<img src="docs/pic/google_search_news_page_example.png" alt="Google Search News Tab - Latest, Unfiltered Results" width="600"/><br/>
<em>Google Search News Tab - Latest, Unfiltered Results</em>
</p>

## Try It Live

**Experience TopicStreams in action**: [http://topicstreams.dongziyu.com](http://topicstreams.dongziyu.com)

### Quick Demo

```bash
# Add topics (ensure they exist)
curl -X POST http://topicstreams.dongziyu.com/api/v1/topics \
  -H "Content-Type: application/json" \
  -d '{"name": "Bitcoin"}'

# List all active topics (contain "bitcoin")
curl http://topicstreams.dongziyu.com/api/v1/topics | jq

# Get latest news for "Bitcoin"
curl http://topicstreams.dongziyu.com/api/v1/news/bitcoin?limit=5 | jq
```

### WebSocket Streaming

For real-time news updates, connect via WebSocket:

```bash
# Real-time WebSocket news stream for an existing topic
# (add the topic first via POST /api/v1/topics — the WS doesn't create topics)
websocat ws://topicstreams.dongziyu.com/api/v1/ws/news/china | jq
```

The WebSocket delivers live news updates as they're scraped, showing the same content you'd see by continuously refreshing Google's news search page.

<p align="center">
<img src="docs/pic/websocket_stream_example.png" alt="WebSocket Real-time News Stream - Live updates as articles are scraped" width="600"/><br/>
<em>WebSocket Real-time News Stream - Live updates as articles are scraped</em>
</p>

### What TopicStreams Offers

- **Real-time news streaming** on customizable topics (any search keywords)
- **Self-hosted** - No third-party news API costs

### Limitations

- **Google Dependency** - Black box algorithms, no source control, variable indexing speed, geographic filtering
- **Inconsistent Results** - Same queries return different results based on IP, geolocation, browser, A/B testing
- **No Quality Control** - All news included, credible or not
- **Access Risks** - Google may detect scraping and rate limit or block access, mitigation: [Anti-Bot Detection](docs/ANTI_BOT_DETECTION.md)

## Features

- **Real-time News Aggregation** - Continuously scrapes Google Search News tab (not Google News site) for the latest articles
- **Multi-Topic Tracking** - Monitor multiple news topics simultaneously with configurable scrape intervals
- **WebSocket Streaming** - Subscribe to live news updates per topic via WebSocket connections
- **REST API** - Manage topics and retrieve historical news entries through HTTP endpoints
- **Anti-Bot Detection** - Native Chromium with runtime-derived browser fingerprinting and automation-signal hardening (playwright-stealth is deliberately disabled — Google detects its JS patches) ([details](docs/ANTI_BOT_DETECTION.md))

## Architecture

TopicStreams consists of three main components:

```plaintext
┌─────────────────────────┐
│         Client          │
│ (REST API / WebSocket)  │
└────────────┬────────────┘
             │                               
             ▼                               
┌─────────────────────────┐    ┌──────────────────────────────┐
│     FastAPI Server      │    │      Scraper Service         │
│                         │    │                              │
│  - REST endpoints       │    │  - Playwright browser        │
│  - WebSocket streams    │    │  - BeautifulSoup parser      │
│  - PostgreSQL listener  │    │  - Continuous scraping loop  │
└────────────┬────────────┘    └─────────────┬────────────────┘
             │                               │
             ▼                               ▼
┌─────────────────────────────────────────────────────────────┐
│                   PostgreSQL Database                       │
│                                                             │
│          - Topics (tracked keywords)                        │
│          - News Entries (scraped articles)                  │
│          - Scraper Logs (monitoring)                        │
│          - LISTEN/NOTIFY for real-time updates              │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **Scraper Service** continuously scrapes Google Search News tab for tracked topics
2. New articles are inserted into **PostgreSQL** with automatic deduplication
3. Database triggers send **NOTIFY** events on new inserts
4. **FastAPI Server** listens for these events via PostgreSQL's LISTEN/NOTIFY
5. Updates are pushed to connected **WebSocket clients** in real-time
6. Clients can also fetch historical data via **REST API**

### Key Technologies

- **FastAPI** - Web framework for REST and WebSocket
- **Playwright** - Browser automation with anti-bot detection ([see how it works](docs/ANTI_BOT_DETECTION.md))
- **PostgreSQL** - Reliable storage with LISTEN/NOTIFY for real-time events
- **Docker** - Container orchestration for easy deployment

## Prerequisites

- **Docker** installed on your system
  - [Install Docker](https://docs.docker.com/get-docker/)

That's it! All dependencies (Python, PostgreSQL, Playwright browsers) are handled inside containers.

> **Optional:** Install [websocat](https://github.com/vi/websocat) for WebSocket testing (used for demo in this article), or use any WebSocket client you prefer.

## Web UI

TopicStreams ships a responsive Web UI styled as a **news wire desk** — a single live transmission feed indexed by time, in light or dark themes.

### Features

- **The Wire** - One chronological stream of all watched topics, newest first. Live entries arrive over WebSocket and prepend at the top; scrolling down loads earlier entries indefinitely (cursor pagination). Filter to a single topic or view all. A **↑ latest** button appears once you scroll down, jumping back to the live edge.
- **Auto-watched topics** - Every topic you track is watched automatically (no per-topic subscribe step). Topics show as removable slugs; add one from the same row.
- **Scrape-health indicator** - The masthead status reflects the real state of the scraper, derived from recent scraper logs: `live`, `degraded` (some feeds failing), `errors` (all feeds failing), `stalled` (no recent scrapes), `idle` (none yet), or `offline` (API unreachable). Hover it for detail.
- **Light / dark themes** - Toggle in the masthead; defaults to your system preference and is remembered across visits.

### Access the Web UI

After [Quick Start](#quick-start), simply open your browser and navigate to:

```plaintext
http://localhost:5000
```

> **Note:** By default, the Web UI is accessible on port 5000. If you changed `HOST_PORT` in your `.env` file (e.g., set to `80` for production), use that port instead (e.g., `http://localhost:80`).

<p align="center">
<img src="docs/pic/ui_screenshot.png" alt="TopicStreams Web UI - the wire, a live news transmission feed" width="600"/>
<br/>
<em>TopicStreams Web UI — "the wire": a live, time-indexed news transmission feed</em>
</p>

> **Scrape-health is server-computed** (`GET /api/v1/status`) from recent `scraper_logs`, refreshed every 30s. It detects selector rot: if Google changes its News-tab markup so scrapes return HTTP 200 but parse **0 entries** across the board, the masthead shows `no items` (state `parsing`) instead of silently reading `live`. A single topic with genuinely no news this hour is safe — it only trips when *every* recent scrape parses nothing.

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/zydo/topicstreams.git
cd topicstreams
```

### 2. Start Services

Create your `.env` first — the stack fails fast with a clear message if it's missing:

```bash
cp .env.example .env
docker compose up -d
```

The defaults in `.env.example` work out-of-the-box; edit `.env` to customize ports, credentials, or the optional API key. The YAML config files (`config/scraper.yml`, `config/anti_detection.yml`) are still created from their `.yml.example` templates on first run, so you only need to copy them when you want to change scraper settings:

```bash
cp config/scraper.yml.example config/scraper.yml
cp config/anti_detection.yml.example config/anti_detection.yml
```

This will start three containers:

- **postgres** - Database
- **scraper** - Background scraping service
- **api** - FastAPI server [http://localhost:5000](http://localhost:5000) (or port set by `HOST_PORT` in `.env`)

### 3. Add Topics to Track

```bash
# Add a topic (replace 5000 with your HOST_PORT if changed)
curl -X POST http://localhost:5000/api/v1/topics \
  -H "Content-Type: application/json" \
  -d '{"name": "artificial intelligence"}'
```

Scraping of the topic will start on the next iteration.

### 4. Access Real-Time News

**WebSocket (for real-time):**

```bash
# Using websocat
websocat ws://localhost:5000/api/v1/ws/news/artificial+intelligence

# Or with jq for prettier formatted output
websocat ws://localhost:5000/api/v1/ws/news/artificial+intelligence | jq
```

**REST API (for historical data):**

```bash
# Get the latest 5 news entries for a topic (newest first)
curl "http://localhost:5000/api/v1/news/artificial+intelligence?limit=5" | jq

# Page back to older entries with the cursor from the previous response
curl "http://localhost:5000/api/v1/news/artificial+intelligence?limit=5&before_id=104" | jq

# Latest 5 across all topics
curl "http://localhost:5000/api/v1/news?limit=5" | jq

# List all actively scraping topics
curl http://localhost:5000/api/v1/topics | jq

# List recent 10 scraper logs (each log represents one Google webpage load - typically up to 10 news entries)
curl http://localhost:5000/api/v1/logs?limit=10 | jq
```

See the [API Reference](#api-reference) section below for complete endpoint documentation.

### 5. Monitor Logs

```bash
# Background scraper logs
docker compose logs -f scraper

# FastAPI server logs
docker compose logs -f api
```

### Stop Services

```bash
docker compose down
```

## Configuration

For complete configuration documentation including environment variables, YAML files, and browser fingerprinting settings, see [Configuration](docs/CONFIGURATION.md).

**Quick links:**

- [**Environment variables**](docs/CONFIGURATION.md#environment-variables-env) - Database and API settings in .env
- [**Scraper settings**](docs/CONFIGURATION.md#scraper-settings-configscrayml) - scrape_interval and max_pages
- [**Anti-detection settings**](docs/CONFIGURATION.md#anti-detection-settings-configanti_detectionyml) - Browser fingerprinting and stealth strategies
- [**Reloading config**](docs/CONFIGURATION.md#reloading-configuration) - How to apply configuration changes

## Anti-Bot Detection

TopicStreams uses sophisticated techniques to make the scraper appear as a real human user, minimizing the risk of being blocked by Google.

For detailed information about anti-detection strategies (Playwright stealth, browser fingerprinting, random delays, etc.), see [Anti-Bot Detection Documentation](docs/ANTI_BOT_DETECTION.md).

**Quick Reference:**
- All anti-detection strategies are configurable via `config/anti_detection.yml` (auto-created from the template on first run)
- See [Configuration](docs/CONFIGURATION.md#anti-detection-settings-configanti_detectionyml) for YAML configuration details

## Scraping Behavior

For detailed information about scraping behavior, monitoring, and scaling strategies, see [Scraping Behavior](docs/SCRAPING_BEHAVIOR.md).

**Quick links:**

- [**Sequential execution**](docs/SCRAPING_BEHAVIOR.md#sequential-execution) - How topics are scraped one after another
- [**Scrape interval**](docs/SCRAPING_BEHAVIOR.md#scrape-interval-behavior) - How scrape_interval controls timing
- [**Monitoring**](docs/SCRAPING_BEHAVIOR.md#monitoring-scrape-performance) - Track scraper performance
- [**Proxy support**](docs/SCRAPING_BEHAVIOR.md#proxy-rotation) - Route the scraper through residential/mobile proxies (in practice required — Google blocks direct automated access to the News tab)

## Authentication & Security

> **Not implemented yet** - For security recommendations and implementation strategies, see [Authentication & Security](docs/AUTHENTICATION_SECURITY.md).

**Quick links:**

- [**Current state**](docs/AUTHENTICATION_SECURITY.md#current-state-localhostlan-only) - Localhost/LAN only, no built-in security
- [**Authentication**](docs/AUTHENTICATION_SECURITY.md#1-authentication--authorization) - API keys, JWT, OAuth2 options
- [**Rate limiting**](docs/AUTHENTICATION_SECURITY.md#2-api-rate-limiting) - Protect against abuse and DDOS
- [**Cloudflare**](docs/AUTHENTICATION_SECURITY.md#3-cloudflare-recommended-for-public-deployment) - Recommended for public deployment

## WebSocket Scalability

> **Not implemented yet** - For scalability recommendations and implementation strategies, see [WebSocket Scalability](docs/WEBSOCKET_SCALABILITY.md).

**Quick links:**

- [**Current state**](docs/WEBSOCKET_SCALABILITY.md#current-state-simple-broadcasting) - Simple in-memory broadcasting
- [**Limitations**](docs/WEBSOCKET_SCALABILITY.md#scalability-limitations) - O(n) broadcast cost, single point of failure
- [**Redis Pub/Sub**](docs/WEBSOCKET_SCALABILITY.md#1-redis-pubsub) - Horizontal scaling with O(1) publish cost
- [**Apache Kafka**](docs/WEBSOCKET_SCALABILITY.md#2-apache-kafka) - For very large-scale deployments (10K+ subscribers)

## API Reference

For complete API documentation including all endpoints, request/response formats, and examples, see [API Reference](docs/API_REFERENCE.md).

**Quick links:**

- [**Topics**](docs/API_REFERENCE.md#topics) - List, add, and delete topics
- [**News**](docs/API_REFERENCE.md#news) - Get news entries for topics with pagination
- [**Logs**](docs/API_REFERENCE.md#logs) - View scraper logs
- [**WebSocket**](docs/API_REFERENCE.md#websocket) - Real-time news updates via WebSocket

## Database Access

For database access, common SQL queries, backup, and restore instructions, see [Database Access](docs/DATABASE_ACCESS.md).

**Quick links:**

- [**psql access**](docs/DATABASE_ACCESS.md#database-access-1) - Connect to PostgreSQL database
- [**Common queries**](docs/DATABASE_ACCESS.md#common-sql-queries) - Topics, news entries, scraper logs, and table sizes
- [**Backup & restore**](docs/DATABASE_ACCESS.md#backup-and-restore) - Database backup and restore procedures

## License

MIT
