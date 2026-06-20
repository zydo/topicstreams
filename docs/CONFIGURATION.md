# Configuration

TopicStreams uses two configuration files, both at the repo root:

- `.env` — secrets and values Docker Compose needs at startup (DB credentials,
  ports, API bootstrap token, proxy). Read as environment variables.
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

| Variable               | Default | Description                                                                                                                                                                                                                              |
| ---------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `TOPICSTREAMS_API_KEY` | (unset) | Comma-separated **bootstrap** bearer tokens. When set (or when the `api_keys` table has an active token), **all** `/api/v1/*` endpoints require `Authorization: Bearer <token>`; unset + empty table = open (dev mode). Editing this var needs a container recreate (`docker compose up -d api`); for live add/disable use the `api_keys` table via `scripts/manage_api_keys.py`. See [Authentication & Security](../README.md#authentication--security). |
| `CORS_ORIGINS`         | `*`     | Comma-separated allowed origins for browser requests.                                                                                                                                                                                    |
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
  engine_strategy: all  # superseded by per-engine workers (kept for back-compat)
  cooldown:             # Reactive backstop: per-engine backoff after a throttle/block
    enabled: true
    base_seconds: 300   # window after the first block; doubles per consecutive block
    max_seconds: 3600   # cap on the exponential window (1h)
  pacing:               # Primary throttle: per-engine min seconds between requests
    default_min_interval: 2.0
    jitter_ratio: 0.25
    per_engine:
      brave: 4.0        # engines that throttle sooner get a longer floor
  saturation:           # When to scale to another exit IP
    canary_engines: [brave]  # excluded from the IP-saturation count (trip first)
    robust_threshold: 2      # this many robust engines cooling at once => saturated
```

Each enabled engine runs in its **own parallel worker** (see
[Execution model](SCRAPING_BEHAVIOR.md#execution-model-one-worker-per-engine));
`pacing` is the primary rate control and `cooldown` is the reactive backstop.

| Setting                  | Default                        | Description                                                                                                                                                                                                                                                                                |
| ------------------------ | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `scrape_interval`        | `60`                           | Per-engine **sweep period**: each worker targets one full pass over the topics per interval (finishing early → waits the remainder; running long → falls behind at its safe pace). See [Scrape Interval Behavior](SCRAPING_BEHAVIOR.md#scrape-interval-behavior).                          |
| `max_pages`              | `1`                            | Number of result pages to scrape. Increase if you have high-volume topics or longer intervals.                                                                                                                                                                                             |
| `engines`                | `[google, bing, yahoo, brave]` | Search engines to scrape, as a list. Each runs in its own parallel worker. Available: `google`, `bing`, `yahoo`, `brave`. (DuckDuckGo is not supported — it hard-blocks scraping; see [docs/DUCKDUCKGO_UNSUPPORTED.md](DUCKDUCKGO_UNSUPPORTED.md).) See [Search Engines](SCRAPING_BEHAVIOR.md#search-engines). |
| `engine_strategy`        | `all`                          | **Superseded** by per-engine workers — every enabled engine now runs in its own worker regardless of this value. Retained for back-compat; may be removed.                                                                                                                                  |
| `browser_recycle_cycles` | `50`                           | Recycle each engine's Chromium context every N sweeps to release accumulated memory (the on-disk persistent profile survives). Guards against unbounded context growth; see [postmortem](POSTMORTEM_2026-06-13_OOM_HANG.md).                                                                |
| `cooldown.enabled`       | `true`                         | **Reactive backstop.** When an engine throttles/blocks (HTTP 429/403/503 or a detected block page), bench it for an exponential backoff window and send one probe before resuming. `pacing` (below) is the primary throttle. The `/monitor` health label reflects it.                       |
| `cooldown.base_seconds`  | `300`                          | Backoff window after an engine's first block; doubles per consecutive block.                                                                                                                                                                                                               |
| `cooldown.max_seconds`   | `3600`                         | Cap on the exponential cooldown window.                                                                                                                                                                                                                                                    |
| `pacing.default_min_interval` | `2.0`                     | **Primary throttle.** Floor on seconds between consecutive requests for one engine. Pacing under a known-safe rate avoids tripping blocks that would escalate on the shared exit IP. See [Proactive pacing](SCRAPING_BEHAVIOR.md#proactive-pacing-vs-reactive-cooldown).                    |
| `pacing.jitter_ratio`    | `0.25`                         | Random extra fraction (0..1) added per interval so the cadence isn't perfectly regular.                                                                                                                                                                                                    |
| `pacing.per_engine`      | `{brave: 4.0}` (example)       | Per-engine overrides of the min interval (engine → seconds). Give strict engines a longer floor instead of discovering it by getting blocked.                                                                                                                                               |
| `saturation.canary_engines` | `[brave]`                   | Engines excluded from the exit-IP saturation count (they trip first by nature, so their cooling isn't evidence the IP is saturated). See [Saturation signal](SCRAPING_BEHAVIOR.md#exit-ip-saturation-signal).                                                                              |
| `saturation.robust_threshold` | `2`                       | How many **robust** (non-canary) engines must be cooling at once before the exit IP is flagged as saturated — the cue to scale to another machine/IP.                                                                                                                                       |

### API Tuning Settings (`config.yml`, `api:` section)

API-side tuning knobs — DB pool/connection, rate limiting, data retention, feed,
scrape-health thresholds, DB retry, and the frontend poll/WebSocket cadence —
live in the `api:` section of `config.yml`. Every key is optional and defaults to
the value shown in `config.yml.example`.

**Precedence:** init args > environment > `.env` > `config.yml` (`api:`) > built-in default. So secrets and values Docker Compose needs at startup (DB credentials, `API_PORT`/`HOST_PORT`, `TOPICSTREAMS_API_KEY`, `SCRAPER_PROXY`) stay in `.env` — they win — while the `api:` section is the preferred surface for tunable defaults. Any key can still be set via the environment to override the YAML for a single deployment.

```bash
cp config.yml.example config.yml
vim config.yml
```

| Setting                        | Default | Description                                                 |
| ------------------------------ | ------- | ----------------------------------------------------------- |
| `db_pool_min_conn`             | `2`     | Minimum DB connections in the pool.                         |
| `db_pool_max_conn`             | `10`    | Maximum DB connections in the pool.                         |
| `db_connect_timeout`           | `10`    | Postgres connect timeout (s).                               |
| `db_keepalives_idle`           | `30`    | Postgres TCP keepalive idle (s).                            |
| `db_keepalives_interval`       | `10`    | Postgres TCP keepalive interval (s).                        |
| `db_keepalives_count`          | `5`     | Postgres TCP keepalive probes before giving up.             |
| `db_retry_max_attempts`        | `3`     | Attempts for transient DB errors.                           |
| `db_retry_delay_seconds`       | `0.1`   | Initial backoff between DB retries (s); doubles each retry. |
| `news_retention_days`          | `30`    | Each cycle purges news + scraper logs older than this.      |
| `api_key_cache_ttl_seconds`    | `30`    | How long the DB-backed API key set is cached before re-reading — i.e. the delay before an `api_keys` add/disable goes live (no restart). `0` = re-read every request. |
| `rate_limit_calls`             | `120`   | Max requests per client IP per `rate_limit_period`.         |
| `rate_limit_period`            | `60`    | Rate-limit window (s).                                      |
| `rate_limit_max_tracked`       | `10000` | Client IPs tracked before the stale-IP eviction sweep.      |
| `feed_engines_window_days`     | `7`     | Engine filter lists engines seen within this window.        |
| `feed_page_size`               | `20`    | Default feed page size (API + UI), 1–100.                   |
| `health_log_window`            | `30`    | Recent scraper logs read for the health signal.             |
| `health_stale_min_seconds`     | `300`   | Floor for the "stalled" threshold.                          |
| `health_stale_max_seconds`     | `1800`  | Ceiling for the "stalled" threshold.                        |
| `health_stale_default_seconds` | `900`   | "stalled" threshold when the cadence can't be inferred.     |
| `status_poll_interval_ms`      | `30000` | UI status-strip refresh interval (ms).                      |
| `ws_reconnect_base_ms`         | `5000`  | WebSocket reconnect backoff base (ms).                      |
| `ws_reconnect_max_ms`          | `30000` | WebSocket reconnect backoff cap (ms).                       |

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
      - "--disable-gpu"
      - "--window-size=1920,1080"
      - "--disable-blink-features=AutomationControlled"  # navigator.webdriver = false

  # SUPERSEDED by scraper.pacing — the per-engine worker model paces each engine
  # itself, so this is no longer consulted (kept for back-compat).
  random_delays:
    enabled: true
    min_seconds: 2   # Minimum delay between topics
    max_seconds: 5   # Maximum delay between topics

  # Page-interaction timings (speed vs. block-risk) and the human-simulation
  # scroll/mouse jitter applied after each page loads. Every key is optional.
  page_interaction:
    nav_timeout_ms: 30000
    selector_timeout_ms: 5000
    settle_min_ms: 1500
    settle_max_ms: 3000
    human_simulation:
      scroll_steps_min: 2
      scroll_steps_max: 4
      # ... scroll/mouse ranges; see config.yml.example for the full list

  browser_fingerprint:
    # NOTE: the user agent and Sec-CH-UA are derived at RUNTIME from the
    # installed Chromium version (scraper/browser.py:detect_fingerprint), so there
    # is no static user_agent key here — a stale hardcoded UA is an instant
    # CAPTCHA. Only the context/identity values below are configured.
    viewport_width: 1920
    viewport_height: 1080
    locale: "en-US"
    timezone_id: "America/Los_Angeles"     # Recommended to match server/proxy IP
    geolocation_latitude: 37.3273          # Recommended to match server/proxy IP
    geolocation_longitude: -121.954        # Recommended to match server/proxy IP
    color_scheme: "light"
    permissions:
      - "geolocation"

  captcha_detection:
    enabled: true
    # Keep keywords SPECIFIC to the block page — a bare "captcha" false-positives
    # because real results pages mention it in Google's own inline JS.
    keywords:
      - "unusual traffic from your computer network"
      - "our systems have detected unusual traffic"

  http_error_handling:
    enabled: true
    monitored_codes:
      - 429  # Rate limiting
      - 403  # Forbidden/blocked
      - 503  # Service unavailable

  http_headers:
    enabled: true
    # Empty by default, and that is intentional. Playwright applies extra_http_headers
    # to EVERY request (documents, XHR, images); forcing Accept / Sec-Fetch-* globally
    # is itself a detection signal because real browsers vary them per request type
    # (verified 2026-06-11 — see ANTI_BOT_DETECTION.md). The Sec-CH-UA trio is added
    # in code from the runtime-derived fingerprint, not here. Leave empty unless you
    # have a specific reason.
    headers: {}
```

> **No UA/fingerprint rotation.** The scraper runs a single runtime-derived
> identity by design (see [Single identity by design](ANTI_BOT_DETECTION.md#5-single-identity-by-design)).
> There is no `user_agent_rotation`, `profiles`, or `page_isolation` config —
> load is distributed across IPs via [proxies](#proxy-configuration), not user agents.

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

- YAML config: edit `config.yml` at the repo root (not the `config.yml.example` template)
- Environment variables: edit the `.env` file in the project root

**After editing YAML configuration files, restart the scraper to apply changes:**

```bash
docker compose restart scraper
```

**For database or API settings changes (`.env`), recreate the containers** so
Compose re-reads `.env` (it's injected via `env_file` at container-creation time,
so a plain `docker compose restart` keeps the old values):

```bash
docker compose up -d            # recreates services whose env changed
# add --force-recreate if it reports "up to date"
```

> **API bearer tokens are an exception.** Adding or disabling a token in the
> `api_keys` table (via `scripts/manage_api_keys.py`) takes effect within
> `api_key_cache_ttl_seconds` with **no restart**. Only the `TOPICSTREAMS_API_KEY`
> env var needs the recreate above.

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
