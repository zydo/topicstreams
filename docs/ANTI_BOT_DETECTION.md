# Anti-Bot Detection

> **Configuration:** All anti-detection strategies are configurable via `config/anti_detection.yml`. See [YAML Configuration Files](../README.md#yaml-configuration-files) for details on customizing each strategy.

TopicStreams uses sophisticated techniques to make the scraper appear as a real human user, minimizing the risk of being blocked by Google.

## How It Works

The scraper uses **Playwright** (headless Chromium browser) combined with **playwright-stealth** patches to hide automation signals and mimic genuine user behavior.

All strategies below are loaded from `config/anti_detection.yml` and can be enabled/disabled individually.

### 1. Browser Launch Arguments

```python
# Loaded from config/anti_detection.yml
browser_args = anti_detection_config.browser_args  # Configurable
browser.launch(
    headless=True,
    args=browser_args
)
```

Default arguments (configurable in YAML):
```python
[
    "--no-sandbox",                                   # Docker compatibility
    "--disable-setuid-sandbox",                       # Docker compatibility
    "--disable-blink-features=AutomationControlled"   # Hide automation flag
]
```

- `--disable-blink-features=AutomationControlled` prevents `navigator.webdriver` from being exposed

### 2. Realistic Browser Context

The browser context is configured to match a real macOS Chrome user (all values configurable in YAML):

```python
# All values loaded from config/anti_detection.yml
context = browser.new_context(
    user_agent=anti_detection_config.user_agent,
    viewport={
        "width": anti_detection_config.viewport_width,
        "height": anti_detection_config.viewport_height,
    },
    locale=anti_detection_config.locale,
    timezone_id=anti_detection_config.timezone_id,        # Configurable
    geolocation={
        "latitude": anti_detection_config.geolocation_latitude,
        "longitude": anti_detection_config.geolocation_longitude
    },
    color_scheme=anti_detection_config.color_scheme,
    extra_http_headers=anti_detection_config.http_headers,
)
```

**Key points:**

- **User Agent**: Latest Chrome version (131) on macOS
- **Timezone & Geolocation**: Recommended to match your server's IP location (see [Configuration](../README.md#yaml-configuration-files))
- **HTTP Headers**: Realistic Accept-Language and content type preferences

### 3. Playwright-Stealth Patches

After creating each page, we apply stealth patches (configurable via `playwright_stealth.enabled` in YAML):

```python
# Loaded from config/anti_detection.yml
if anti_detection_config.playwright_stealth_enabled:
    stealth = Stealth()
    stealth.apply_stealth_sync(page)
```

This patches ~20 automation detection vectors:

| Detection Vector      | Before    | After          |
| --------------------- | --------- | -------------- |
| `navigator.webdriver` | `true`    | `false`        |
| `navigator.plugins`   | Empty (0) | 3 fake plugins |
| `window.chrome`       | Missing   | Present        |
| Canvas fingerprints   | Generic   | Realistic      |
| WebGL fingerprints    | Generic   | Realistic      |

### 4. Memory Management & Additional Strategies

To prevent memory leaks in long-running scrapers (configurable via `page_isolation.enabled` in YAML):

```python
# Loaded from config/anti_detection.yml
for topic in topics:
    if anti_detection_config.page_isolation_enabled:
        page = context.new_page()      # Fresh page per topic
    else:
        page = context.new_page()      # Fallback

    if anti_detection_config.playwright_stealth_enabled:
        stealth.apply_stealth_sync(page)

    try:
        scrape_news(page, topic)       # page.goto(...) one or multiple URLs
    finally:
        if anti_detection_config.page_isolation_enabled:
            page.close()                # Always cleanup
```

**Additional configurable strategies:**

- **Random Delays** (`random_delays.enabled`): Random delay between topics to mimic human behavior
- **Randomized Order** (`randomized_order.enabled`): Shuffle topic order each cycle to avoid deterministic patterns

## What Google Sees

After all patches, Google's JavaScript sees:

```javascript
navigator.webdriver        // false (was true)
navigator.plugins.length   // 3 (was 0)
window.chrome              // Object (was undefined)
navigator.languages        // ["en-US", "en"]
navigator.platform         // "MacIntel"
```

## Limitations

**This is NOT perfect invisibility:**

- Google can still detect patterns (same IP scraping many topics)
- Browser fingerprints are static (not randomized per request)
- High request rates will still trigger blocks

For high-volume or 24/7 scraping, consider [proxy rotation](../README.md#proxy-rotation) to distribute load across multiple IPs.

**Best practices:**

- Match timezone/geolocation to your server's IP location
- Keep `scrape_interval` reasonable (default 60s is safe) - see `config/scraper.yml`
- Monitor scraper logs for HTTP 429 (rate limit) or 403 (blocked)

See the [Configuration](../README.md#yaml-configuration-files) section to customize settings.
