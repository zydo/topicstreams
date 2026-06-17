# Configuration

TopicStreams uses two types of configuration files:

- `.env` file for database and API settings (via environment variables)
- YAML files in `config/` directory for scraper and anti-detection settings

## First-Time Setup

Before running the application, create your `.env` file from the template:

```bash
# Copy environment variables template
cp .env.example .env

# Now start the application
docker compose up
```

The YAML configuration files are created automatically: if `config/scraper.yml` or `config/anti_detection.yml` is missing at startup, it is copied from its `.yml.example` template (a warning is logged). To customize settings before the first run, create and edit them manually:

```bash
cp config/scraper.yml.example config/scraper.yml
cp config/anti_detection.yml.example config/anti_detection.yml
vim config/scraper.yml
vim config/anti_detection.yml
```

**How YAML configuration works:**
- Template files (`.yml.example`) are tracked in git and contain sensible defaults
- Your local `.yml` files are gitignored and contain your custom settings
- You can modify your `.yml` files anytime, and changes will be preserved
- To reset to defaults, delete your local `.yml` files; they will be re-created from the templates on next startup

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

| Variable    | Default | Description                                                      |
| ----------- | ------- | ---------------------------------------------------------------- |
| `API_PORT`   | `5000`  | Port inside the container where FastAPI listens                  |
| `HOST_PORT`  | `5000`  | Port exposed on the host (set to `80` for production deployment) |
| `LOG_FORMAT` | `text`  | `text` (human-readable) or `json` (structured logs, one object per line) |

> **Note:** The `HOST_PORT` is mapped to `API_PORT` (e.g., `HOST_PORT=80` and `API_PORT=5000` means the app listens on container port 5000 but is accessible via host port 80). For production deployments, set `HOST_PORT=80` to use the standard HTTP port.

### Security Settings

| Variable              | Default | Description                                                                                                                                              |
| --------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `API_KEY`             | (unset) | When set, `POST`/`DELETE /api/v1/topics` require a matching `X-API-Key` header. Unset = writes are open (dev mode).                                      |
| `CORS_ORIGINS`        | `*`     | Comma-separated allowed origins for browser requests.                                                                                                   |
| `TRUSTED_PROXY_COUNT` | `0`     | Reverse proxies in front of the app. `>0` makes the rate limiter read the client IP from `X-Forwarded-For` (Nth entry from the right); `0` = direct, header ignored. Set this to match your proxy chain, or one IP rate-limits everyone. |

> **Behind a reverse proxy:** set `TRUSTED_PROXY_COUNT` to the number of proxies between the client and the app (usually `1`). Lock the origin so it only accepts traffic from those proxies — otherwise `X-Forwarded-For` can be spoofed by hitting the app directly.

### Data Retention

| Variable              | Default | Description                                                                                                  |
| --------------------- | ------- | ----------------------------------------------------------------------------------------------------------- |
| `NEWS_RETENTION_DAYS` | `30`    | Each scrape cycle purges news (feed events + orphaned articles) **and** scraper logs older than this window. |

## YAML Configuration Files

Scraper and anti-detection settings are configured via YAML files in the `config/` directory. These files are mounted into the containers at runtime and can be edited without rebuilding.

### Scraper Settings (`config/scraper.yml`)

```yaml
scraper:
  scrape_interval: 60  # Seconds between scrape cycles
  max_pages: 1         # Maximum pages to scrape per topic
  engines:
    enabled: [google]  # Search engines to scrape, in priority order
    strategy: fallback # How enabled engines are combined: fallback | all | rotate
```

| Setting             | Default      | Description                                                                                                                                                                                                            |
| ------------------- | ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scrape_interval`   | `60`         | Interval in seconds between scrape cycles (measured from start to start). Set to `0` or negative for continuous scraping with no delay. See [Scrape Interval Behavior](SCRAPING_BEHAVIOR.md#scrape-interval-behavior). |
| `max_pages`         | `1`          | Number of result pages to scrape. Increase if you have high-volume topics or longer intervals.                                                                                                                         |
| `engines.enabled`   | `[google]`   | Search engines to scrape, in priority order. Available: `google`, `bing`, `yahoo`, `brave`, `duckduckgo`. See [Search Engines](SCRAPING_BEHAVIOR.md#search-engines).                                                  |
| `engines.strategy`  | `fallback`   | How enabled engines are combined per cycle: `fallback` (try in order, stop at the first that returns items), `all` (scrape every engine each cycle), or `rotate` (one engine per cycle, rotating).                     |

### Anti-Detection Settings (`config/anti_detection.yml`)

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
# config/anti_detection.yml
anti_detection:
  proxy:
    enabled: true
    proxies:
      - "http://user:pass@gateway.provider.com:7777"
      - "socks5://user:pass@gateway.provider.com:1080"
```

| Setting          | Default | Description                                                                 |
| ---------------- | ------- | --------------------------------------------------------------------------- |
| `enabled`        | `false` | Enable proxying. Implicitly `true` when `SCRAPER_PROXY` is set.             |
| `proxies`        | `[]`    | Proxy URLs `scheme://[user:pass@]host:port` (http/https/socks5). One is chosen per browser launch. |
| `SCRAPER_PROXY`  | unset   | Single proxy URL (env var). Overrides `proxies` and enables proxying.       |

> **Match the location.** Set `timezone_id` and `geolocation` (above in
> `anti_detection.yml`) to the proxy's exit country — a mismatch is itself a
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
# Delete your local config files (they are re-created from the
# .yml.example templates on next startup)
rm config/scraper.yml config/anti_detection.yml

# Restart the application
docker compose restart
```

## See Also

- [Anti-Bot Detection](ANTI_BOT_DETECTION.md) - Detailed anti-detection strategies documentation
- [Scraping Behavior](SCRAPING_BEHAVIOR.md) - How scrape_interval and max_pages affect scraping
- [Database Access](DATABASE_ACCESS.md) - Direct database access instructions
