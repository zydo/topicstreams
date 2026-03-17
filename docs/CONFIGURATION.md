# Configuration

TopicStreams uses two types of configuration files:
- `.env` file for database and API settings (via environment variables)
- YAML files in `config/` directory for scraper and anti-detection settings

Copy `.env.example` to `.env` to get started with default values.

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
| `API_PORT`  | `5000`  | Port inside the container where FastAPI listens                  |
| `HOST_PORT` | `5000`  | Port exposed on the host (set to `80` for production deployment) |

> **Note:** The `HOST_PORT` is mapped to `API_PORT` (e.g., `HOST_PORT=80` and `API_PORT=5000` means the app listens on container port 5000 but is accessible via host port 80). For production deployments, set `HOST_PORT=80` to use the standard HTTP port.

## YAML Configuration Files

Scraper and anti-detection settings are configured via YAML files in the `config/` directory. These files are mounted into the containers at runtime and can be edited without rebuilding.

### Scraper Settings (`config/scraper.yml`)

```yaml
scraper:
  scrape_interval: 60  # Seconds between scrape cycles
  max_pages: 1         # Maximum pages to scrape per topic
```

| Setting           | Default | Description                                                                                                                                                                                        |
| ----------------- | ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scrape_interval` | `60`    | Interval in seconds between scrape cycles (measured from start to start). Set to `0` or negative for continuous scraping with no delay. See [Scrape Interval Behavior](SCRAPING_BEHAVIOR.md#scrape-interval-behavior). |
| `max_pages`       | `1`     | Number of result pages to scrape. Increase if you have high-volume topics or longer intervals.                                                                                                     |

### Anti-Detection Settings (`config/anti_detection.yml`)

```yaml
anti_detection:
  playwright_stealth:
    enabled: true  # Apply playwright-stealth patches to hide automation

  browser_args:
    enabled: true
    args:
      - "--no-sandbox"
      - "--disable-setuid-sandbox"
      - "--disable-blink-features=AutomationControlled"

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
      Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
      Accept-Language: "en-US,en;q=0.9"
```

### Key Browser Fingerprinting Settings

| Setting                 | Default               | Description                                                                                                                                                                       |
| ---------------------- | --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `timezone_id`          | `America/Los_Angeles` | Browser timezone identifier. Recommended to match your server's IP location. [List of timezones](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) |
| `geolocation_latitude`  | `37.3273`             | Latitude coordinate (default: San Jose, CA)                                                                                                                                       |
| `geolocation_longitude` | `-121.954`            | Longitude coordinate (default: San Jose, CA)                                                                                                                                      |

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

## Reloading Configuration

After editing YAML configuration files, restart the scraper to apply changes:

```bash
docker compose restart scraper
```

For database or API settings changes (`.env`), restart the entire stack:

```bash
docker compose restart
```

## See Also

- [Anti-Bot Detection](ANTI_BOT_DETECTION.md) - Detailed anti-detection strategies documentation
- [Scraping Behavior](SCRAPING_BEHAVIOR.md) - How scrape_interval and max_pages affect scraping
- [Database Access](DATABASE_ACCESS.md) - Direct database access instructions
