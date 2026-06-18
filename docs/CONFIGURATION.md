# Configuration

TopicStreams uses two configuration files, both at the repo root:

- `.env` — secrets and values Docker Compose needs at startup (DB credentials,
  ports, API key, proxy). Read as environment variables.
- `config.yml` — everything else: scraper behavior, anti-detection, and API
  tuning. One file with `scraper:` / `anti_detection:` / `api:` sections that the
  scraper and API processes each read.

## First-Time Setup

Before running the application, create your `.env` file from the template:

```bash
# Copy environment variables template
cp .env.example .env

# Now start the application
docker compose up
```

`config.yml` is created automatically: if it's missing at startup, the scraper
copies it from `config.yml.example` (a warning is logged); the API just falls
back to built-in defaults. To customize before the first run, copy and edit it:

```bash
cp config.yml.example config.yml
vim config.yml
```

**How it works:**
- `config.yml.example` is tracked in git and holds the sensible defaults
- Your local `config.yml` is gitignored and holds your custom settings
- You can edit `config.yml` anytime; changes are preserved
- To reset to defaults, delete `config.yml`; it's re-created from the template on
  next startup (the scraper process) or the API uses built-in defaults

## Environment Variables (.env)

### Database Settings

| Variable            | Default    | Description                                                                                    |
| ------------------- | ---------- | ---------------------------------------------------------------------------------------------- |
| `POSTGRES_HOST`     | `postgres` | PostgreSQL hostname (use Docker service name, `postgres`, for Docker services internal access) |
| `POSTGRES_PORT`     | `5432`     | PostgreSQL port                                                                                |
| `POSTGRES_DB`       | `newsdb`   | Database name                                                                                  |
| `POSTGRES_USER`     | `newsuser` | Database username                                                                              |
| `POSTGRES_PASSWORD` | `newspass` | Database password                                                                              |

> **Note:** The PostgreSQL service is only accessible within the Docker network (not exposed to the host). Simple passwords are acceptable since the database is not publicly accessible. For direct database access, see [Database Access](DATABASE_ACCESS.md).

### API Settings

| Variable     | Default | Description                                                              |
| ------------ | ------- | ------------------------------------------------------------------------ |
| `API_PORT`   | `5000`  | Port inside the container where FastAPI listens                          |
| `HOST_PORT`  | `5000`  | Port exposed on the host (set to `80` for production deployment)         |
| `LOG_FORMAT` | `text`  | `text` (human-readable) or `json` (structured logs, one object per line) |

> **Note:** The `HOST_PORT` is mapped to `API_PORT` (e.g., `HOST_PORT=80` and `API_PORT=5000` means the app listens on container port 5000 but is accessible via host port 80). For production deployments, set `HOST_PORT=80` to use the standard HTTP port.

### Security Settings

| Variable              | Default | Description                                                                                                                                                                                                                              |
| --------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `API_KEY`             | (unset) | When set, `POST`/`DELETE /api/v1/topics` require a matching `X-API-Key` header. Unset = writes are open (dev mode).                                                                                                                      |
| `CORS_ORIGINS`        | `*`     | Comma-separated allowed origins for browser requests.                                                                                                                                                                                    |
| `TRUSTED_PROXY_COUNT` | `0`     | Reverse proxies in front of the app. `>0` makes the rate limiter read the client IP from `X-Forwarded-For` (Nth entry from the right); `0` = direct, header ignored. Set this to match your proxy chain, or one IP rate-limits everyone. |

> **Behind a reverse proxy:** set `TRUSTED_PROXY_COUNT` to the number of proxies between the client and the app (usually `1`). Lock the origin so it only accepts traffic from those proxies — otherwise `X-Forwarded-For` can be spoofed by hitting the app directly.

### Data Retention

| Variable              | Default | Description                                                                                                  |
| --------------------- | ------- | ------------------------------------------------------------------------------------------------------------ |
| `NEWS_RETENTION_DAYS` | `30`    | Each scrape cycle purges news (feed events + orphaned articles) **and** scraper logs older than this window. |

## YAML Configuration (`config.yml`)

Scraper, anti-detection, and API-tuning settings all live in `config.yml` at the
repo root, under three top-level sections: `scraper:`, `anti_detection:`, and
`api:`. The scraper and API processes each read this one file and pull their own
section. The file is baked into the images at build time (so edit it, then
rebuild); the `SCRAPER_PROXY` env var lets you override proxy credentials without
a rebuild.

### Scraper Settings (`config.yml`)

```yaml
scraper:
  scrape_interval: 60  # Seconds between scrape cycles
  max_pages: 1         # Maximum pages to scrape per topic
  # Search engines to scrape, in priority order (a YAML list).
  engines:
    - google
    - bing
    - yahoo
    - brave
  engine_strategy: all  # How enabled engines combine: all | fallback | rotate
```

| Setting           | Default                        | Description                                                                                                                                                                                                                                                                                |
| ----------------- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `scrape_interval` | `60`                           | Interval in seconds between scrape cycles (measured from start to start). Set to `0` or negative for continuous scraping with no delay. See [Scrape Interval Behavior](SCRAPING_BEHAVIOR.md#scrape-interval-behavior).                                                                     |
| `max_pages`       | `1`                            | Number of result pages to scrape. Increase if you have high-volume topics or longer intervals.                                                                                                                                                                                             |
| `engines`         | `[google, bing, yahoo, brave]` | Search engines to scrape, as a list in priority order. Available: `google`, `bing`, `yahoo`, `brave`. (DuckDuckGo is not supported — it hard-blocks scraping; see [docs/DUCKDUCKGO_UNSUPPORTED.md](DUCKDUCKGO_UNSUPPORTED.md).) See [Search Engines](SCRAPING_BEHAVIOR.md#search-engines). |
| `engine_strategy` | `all`                          | How enabled engines combine per cycle: `all` (scrape every engine each cycle), `fallback` (try in order, stop at the first that returns items), or `rotate` (one engine per cycle, rotating).                                                                                              |
| `browser_recycle_cycles` | `50`                    | Recycle the Chromium context every N cycles to release accumulated memory (the on-disk persistent profile survives). Guards against unbounded context growth; see [postmortem](POSTMORTEM_2026-06-13_OOM_HANG.md).                                                                          |

### API Tuning Settings (`config.yml`, `api:` section)

API-side tuning knobs — DB pool/connection, rate limiting, data retention, feed,
scrape-health thresholds, DB retry, and the frontend poll/WebSocket cadence —
live in the `api:` section of `config.yml`. Every key is optional and defaults to
the value shown in `config.yml.example`.

**Precedence:** init args > environment > `.env` > `config.yml` (`api:`) > built-in default. So secrets and values Docker Compose needs at startup (DB credentials, `API_PORT`/`HOST_PORT`, `API_KEY`, `SCRAPER_PROXY`) stay in `.env` — they win — while the `api:` section is the preferred surface for tunable defaults. Any key can still be set via the environment to override the YAML for a single deployment.

```bash
cp config.yml.example config.yml
vim config.yml
```

| Setting                       | Default | Description                                                                                          |
| ----------------------------- | ------- | ---------------------------------------------------------------------------------------------------- |
| `db_pool_min_conn`            | `2`     | Minimum DB connections in the pool.                                                                  |
| `db_pool_max_conn`            | `10`    | Maximum DB connections in the pool.                                                                  |
| `db_connect_timeout`          | `10`    | Postgres connect timeout (s).                                                                        |
| `db_keepalives_idle`          | `30`    | Postgres TCP keepalive idle (s).                                                                     |
| `db_keepalives_interval`      | `10`    | Postgres TCP keepalive interval (s).                                                                 |
| `db_keepalives_count`         | `5`     | Postgres TCP keepalive probes before giving up.                                                      |
| `db_retry_max_attempts`       | `3`     | Attempts for transient DB errors.                                                                    |
| `db_retry_delay_seconds`      | `0.1`   | Initial backoff between DB retries (s); doubles each retry.                                          |
| `news_retention_days`         | `30`    | Each cycle purges news + scraper logs older than this.                                               |
| `rate_limit_calls`            | `120`   | Max requests per client IP per `rate_limit_period`.                                                  |
| `rate_limit_period`           | `60`    | Rate-limit window (s).                                                                               |
| `rate_limit_max_tracked`      | `10000` | Client IPs tracked before the stale-IP eviction sweep.                                               |
| `feed_engines_window_days`    | `7`     | Engine filter lists engines seen within this window.                                                 |
| `feed_page_size`              | `20`    | Default feed page size (API + UI), 1–100.                                                            |
| `health_log_window`           | `30`    | Recent scraper logs read for the health signal.                                                      |
| `health_stale_min_seconds`    | `300`   | Floor for the "stalled" threshold.                                                                   |
| `health_stale_max_seconds`    | `1800`  | Ceiling for the "stalled" threshold.                                                                 |
| `health_stale_default_seconds`| `900`   | "stalled" threshold when the cadence can't be inferred.                                              |
| `status_poll_interval_ms`     | `30000` | UI status-strip refresh interval (ms).                                                               |
| `ws_reconnect_base_ms`        | `5000`  | WebSocket reconnect backoff base (ms).                                                               |
| `ws_reconnect_max_ms`         | `30000` | WebSocket reconnect backoff cap (ms).                                                                |

The frontend reads `feed_page_size` and the poll/WebSocket cadence at startup from `GET /api/v1/config`, so changing them here takes effect on next UI load without a frontend rebuild.

### Anti-Detection Settings (`config.yml`)

```yaml
anti_detection:
  playwright_stealth:
    enabled: false  # Keep disabled — Google detects playwright-stealth's JS patches

  browser_args:
    enabled: true
    args:
      - "--no-sandbox"
      - "--disable-setuid-sandbox"
      - "--disable-dev-shm-usage"
      - "--window-size=1920,1080"
      - "--disable-background-timer-throttling"
      - "--disable-backgrounding-occluded-windows"
      - "--disable-renderer-backgrounding"

  random_delays:
    enabled: true
    min_seconds: 2   # Minimum delay between topics
    max_seconds: 5   # Maximum delay between topics

  randomized_order:
    enabled: true    # Shuffle topic order each cycle

  page_isolation:
    enabled: true    # Create new page per topic (memory management)

  browser_fingerprint:
    enabled: true
    user_agent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ..."
    viewport_width: 1920
    viewport_height: 1080
    locale: "en-US"
    timezone_id: "America/Los_Angeles"     # Recommended to match server IP
    geolocation_latitude: 37.3273          # Recommended to match server IP
    geolocation_longitude: -121.954         # Recommended to match server IP
    color_scheme: "light"
    permissions:
      - "geolocation"

  captcha_detection:
    enabled: true
    keywords:
      - "captcha"
      - "unusual traffic"

  http_error_handling:
    enabled: true
    monitored_codes:
      - 429  # Rate limiting
      - 403  # Forbidden/blocked
      - 503  # Service unavailable

  http_headers:
    enabled: true
    headers:
      # Standard browser headers
      Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
      Accept-Language: "en-US,en;q=0.9"

      # Sec-Fetch headers (modern browser navigation indicators)
      Sec-Fetch-Dest: "document"
      Sec-Fetch-Mode: "navigate"
      Sec-Fetch-Site: "none"
      Sec-Fetch-User: "?1"

      # Upgrade hints
      Upgrade-Insecure-Requests: "1"

      # Sec-Ch-Ua and Sec-Ch-Ua-Platform come from fingerprint profiles,
      # not here — to keep headers consistent with the rotated user agent.
```

**HTTP Headers Configuration:**

| Header Category   | Headers                                                                | Purpose                              |
| ----------------- | ---------------------------------------------------------------------- | ------------------------------------ |
| **Standard**      | `Accept`, `Accept-Language`                                            | Content negotiation                  |
| **Sec-Fetch***    | `Sec-Fetch-Dest`, `Sec-Fetch-Mode`, `Sec-Fetch-Site`, `Sec-Fetch-User` | Navigation context (modern browsers) |
| **Upgrade Hints** | `Upgrade-Insecure-Requests`                                            | HTTPS preference                     |

Sec-Ch-Ua headers are automatically merged from the active fingerprint profile (see [Fingerprint Profile Rotation](#fingerprint-profile-rotation) above), not configured here directly. This ensures headers always match the rotated user agent.

### Key Browser Fingerprinting Settings

| Setting                 | Default               | Description                                                                                                                                                    |
| ----------------------- | --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `timezone_id`           | `America/Los_Angeles` | Browser timezone identifier. Recommended to match your server's IP location. [List of timezones](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) |
| `geolocation_latitude`  | `37.3273`             | Latitude coordinate (default: San Jose, CA)                                                                                                                    |
| `geolocation_longitude` | `-121.954`            | Longitude coordinate (default: San Jose, CA)                                                                                                                   |

### Example Location Configurations

```yaml
# New York
timezone_id: America/New_York
geolocation_latitude: 40.7128
geolocation_longitude: -74.0060

# London
timezone_id: Europe/London
geolocation_latitude: 51.5074
geolocation_longitude: -0.1278

# Tokyo
timezone_id: Asia/Tokyo
geolocation_latitude: 35.6762
geolocation_longitude: 139.6503

# Singapore
timezone_id: Asia/Singapore
geolocation_latitude: 1.3521
geolocation_longitude: 103.8198
```

### Fingerprint Profile Rotation

Fingerprint profile rotation allows the scraper to cycle through multiple realistic browser profiles. Each profile bundles a user agent with matching `Sec-CH-UA` and `Sec-CH-UA-Platform` headers to ensure consistency (only Chrome profiles are used — Firefox/Safari UAs on a Chromium browser are detectable):

```yaml
browser_fingerprint:
  user_agent_rotation:
    enabled: true
    strategy: "per_topic"  # "per_cycle" or "per_topic"
    profiles:
      # Chrome 131 on Windows
      - user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        sec_ch_ua: '"Chromium";v="131", "Not_A Brand";v="24"'
        sec_ch_ua_platform: '"Windows"'
      # Chrome 131 on macOS
      - user_agent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        sec_ch_ua: '"Chromium";v="131", "Not_A Brand";v="24"'
        sec_ch_ua_platform: '"macOS"'
      # Chrome 131 on Linux
      - user_agent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        sec_ch_ua: '"Chromium";v="131", "Not_A Brand";v="24"'
        sec_ch_ua_platform: '"Linux"'
      # Chrome 130 variants (Windows, macOS, Linux) ...
```

**Fingerprint Profile Rotation Settings:**

| Setting    | Default       | Description                                                                      |
| ---------- | ------------- | -------------------------------------------------------------------------------- |
| `enabled`  | `false`       | Enable or disable fingerprint profile rotation                                   |
| `strategy` | `"per_topic"` | `"per_cycle"` (rotate once per scrape cycle) or `"per_topic"` (rotate per topic) |
| `profiles` | `[]`          | List of profiles, each with `user_agent`, `sec_ch_ua`, `sec_ch_ua_platform`      |

**Strategy Comparison:**

| Strategy    | Description                  | Performance                      | Recommended For                     |
| ----------- | ---------------------------- | -------------------------------- | ----------------------------------- |
| `per_cycle` | One profile per scrape cycle | Faster (fewer context creations) | Lower topic counts                  |
| `per_topic` | Different profile per topic  | Slower (new context per topic)   | Higher topic counts, better stealth |

When `user_agent_rotation.enabled` is `false`, the scraper uses the static `user_agent` value.

### Proxy Configuration

Route the browser through a **residential or mobile** proxy. This is effectively
required: Google blocks automated browsers from `/search` (including the News
tab) even from a residential IP, so without a proxy the scrape returns only
CAPTCHA pages. Datacenter proxies are detected and blocked like a direct
connection.

Configure it in **either** place — the `SCRAPER_PROXY` env var takes precedence:

```bash
# .env  (recommended: no image rebuild, keeps credentials out of the image)
SCRAPER_PROXY=http://user:pass@gateway.provider.com:7777
```

```yaml
# config.yml
anti_detection:
  proxy:
    enabled: true
    proxies:
      - "http://user:pass@gateway.provider.com:7777"
      - "socks5://user:pass@gateway.provider.com:1080"
```

| Setting         | Default | Description                                                                                        |
| --------------- | ------- | -------------------------------------------------------------------------------------------------- |
| `enabled`       | `false` | Enable proxying. Implicitly `true` when `SCRAPER_PROXY` is set.                                    |
| `proxies`       | `[]`    | Proxy URLs `scheme://[user:pass@]host:port` (http/https/socks5). One is chosen per browser launch. |
| `SCRAPER_PROXY` | unset   | Single proxy URL (env var). Overrides `proxies` and enables proxying.                              |

> **Match the location.** Set `timezone_id` and `geolocation` (above in
> `config.yml`) to the proxy's exit country — a mismatch is itself a
> detection signal.

## Reloading Configuration

**Editing configuration:**

- YAML configs: Edit the generated `.yml` files in `config/` directory (not the `.yml.example` templates)
- Environment variables: Edit the `.env` file in the project root

**After editing YAML configuration files, restart the scraper to apply changes:**

```bash
docker compose restart scraper
```

**For database or API settings changes (`.env`), restart the entire stack:**

```bash
docker compose restart
```

**To reset configuration to defaults:**

```bash
# Delete your local config (re-created from the template on next startup)
rm config.yml

# Restart the application
docker compose restart
```

## See Also

- [Anti-Bot Detection](ANTI_BOT_DETECTION.md) - Detailed anti-detection strategies documentation
- [Scraping Behavior](SCRAPING_BEHAVIOR.md) - How scrape_interval and max_pages affect scraping
- [Database Access](DATABASE_ACCESS.md) - Direct database access instructions
