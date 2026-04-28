# Anti-Bot Detection

> **Configuration:** All anti-detection strategies are configurable via `config/anti_detection.yml` (created from `config/anti_detection.yml.example` template on first-time setup). See [Configuration](docs/CONFIGURATION.md) for details on customizing each strategy.

TopicStreams uses sophisticated techniques to make the scraper appear as a real human user, minimizing the risk of being blocked by Google.

## How It Works

The scraper uses **Playwright** (real Google Chrome browser) combined with **playwright-stealth** patches to hide automation signals and mimic genuine user behavior.

All strategies below are loaded from `config/anti_detection.yml` and can be enabled/disabled individually.

### 1. Browser Launch Arguments

```python
# Loaded from config/anti_detection.yml
browser_args = anti_detection_config.browser_args  # Configurable
browser.launch(
    headless=True,
    channel="chrome",                # Use real Google Chrome, not Chromium
    args=browser_args,
    ignore_default_args=["--enable-automation"],  # Remove automation flag
)
```

Default arguments (configurable in YAML):
```python
[
    "--no-sandbox",                                # Docker compatibility
    "--disable-setuid-sandbox",                    # Docker compatibility
    "--disable-dev-shm-usage",                     # Prevent /dev/shm crashes in Docker
    "--window-size=1920,1080",                     # Consistent viewport
    "--disable-background-timer-throttling",       # Keep timers accurate
    "--disable-backgrounding-occluded-windows",    # Prevent background throttling
    "--disable-renderer-backgrounding",            # Prevent renderer throttling
]
```

- `ignore_default_args=["--enable-automation"]` removes the `--enable-automation` flag that Chromium sets by default, preventing `navigator.webdriver` from being exposed

### 2. Realistic Browser Context

The browser context is configured to match a real Chrome user (all values configurable in YAML):

```python
# All values loaded from config/anti_detection.yml
context = browser.new_context(
    user_agent=profile.user_agent,
    viewport={
        "width": anti_detection_config.viewport_width,
        "height": anti_detection_config.viewport_height,
    },
    locale=anti_detection_config.locale,
    timezone_id=anti_detection_config.timezone_id,
    permissions=anti_detection_config.permissions,
    geolocation={
        "latitude": anti_detection_config.geolocation_latitude,
        "longitude": anti_detection_config.geolocation_longitude
    },
    color_scheme=anti_detection_config.color_scheme,
    extra_http_headers=_build_headers(profile),
)
```

**Key points:**

- **User Agent**: Determined by the active fingerprint profile (Chrome on Windows/macOS/Linux)
- **Permissions**: Configurable browser permissions (default: geolocation)
- **Timezone & Geolocation**: Recommended to match your server's IP location (see [Configuration](CONFIGURATION.md))
- **HTTP Headers**: Base headers from config merged with profile-specific Sec-CH-UA headers (see below)

### HTTP Headers Optimization

Modern browsers send additional headers that indicate navigation intent and browser capabilities. The base headers are configured via `http_headers.headers` in `config/anti_detection.yml`, and the Sec-CH-UA headers are automatically merged from the active fingerprint profile:

```python
def _build_headers(profile: FingerprintProfile) -> dict:
    base_headers = dict(anti_detection_config.http_headers)
    base_headers["Sec-Ch-Ua"] = profile.sec_ch_ua
    base_headers["Sec-Ch-Ua-Mobile"] = "?0"
    base_headers["Sec-Ch-Ua-Platform"] = profile.sec_ch_ua_platform
    return base_headers
```

**Base Headers** (from `http_headers.headers` config):

| Header                      | Value                                 | Purpose                         |
| --------------------------- | ------------------------------------- | ------------------------------- |
| `Accept`                    | `text/html,application/xhtml+xml,...` | Content negotiation             |
| `Accept-Language`           | `en-US,en;q=0.9`                      | Language preference             |
| `Sec-Fetch-Dest`            | `document`                            | Destination of navigation       |
| `Sec-Fetch-Mode`            | `navigate`                            | Navigation mode                 |
| `Sec-Fetch-Site`            | `none`                                | Origin-destination relationship |
| `Sec-Fetch-User`            | `?1`                                  | User-initiated navigation       |
| `Upgrade-Insecure-Requests` | `1`                                   | HTTPS preference                |

**Profile-Specific Headers** (merged dynamically from active `FingerprintProfile`):

| Header               | Source                       | Purpose                   |
| -------------------- | ---------------------------- | ------------------------- |
| `Sec-Ch-Ua`          | `profile.sec_ch_ua`          | Browser brand and version |
| `Sec-Ch-Ua-Mobile`   | `"?0"`                       | Mobile device indicator   |
| `Sec-Ch-Ua-Platform` | `profile.sec_ch_ua_platform` | Operating system platform |

This ensures Sec-CH-UA headers always match the user agent being used, which is critical for avoiding detection.

### 3. Playwright-Stealth Patches

After creating each browser context, we apply stealth patches at the context level (configurable via `playwright_stealth.enabled` in YAML):

```python
# Loaded from config/anti_detection.yml
stealth = Stealth() if anti_detection_config.playwright_stealth_enabled else None

# Applied to each context (not individual pages)
context = browser.new_context(...)
if stealth:
    stealth.apply_stealth_sync(context)
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

To prevent memory leaks in long-running scrapers, each topic gets a fresh page (configurable via `page_isolation.enabled` in YAML). When using `per_topic` profile rotation, a new context is also created per topic:

```python
for topic in topics:
    if need_context_per_topic:
        # New context per topic (for per_topic rotation)
        context = browser.new_context(profile, ...)
        if stealth:
            stealth.apply_stealth_sync(context)

    page = context.new_page()

    try:
        scrape_news(page, topic)
    finally:
        page.close()
        if need_context_per_topic:
            context.close()
```

**Additional configurable strategies:**

- **Random Delays** (`random_delays.enabled`): Random delay between topics to mimic human behavior
- **Randomized Order** (`randomized_order.enabled`): Shuffle topic order each cycle to avoid deterministic patterns
- **Fingerprint Profile Rotation** (`user_agent_rotation.enabled`): Rotate through profiles with matching UA + Sec-CH-UA headers
- **Human-Like Behavior**: Random scrolling and mouse movements during page loads to simulate reading

### 5. Fingerprint Profile Rotation

To avoid static browser fingerprints that can be flagged by Google, the scraper supports rotating through multiple fingerprint profiles. Each profile bundles a user agent with matching Sec-CH-UA headers:

```python
# Loaded from config/anti_detection.yml
if anti_detection_config.user_agent_rotation_enabled:
    strategy = anti_detection_config.user_agent_rotation_strategy  # "per_cycle" or "per_topic"

    if strategy == "per_cycle":
        # Rotate once per scrape cycle (all topics use same profile)
        profile = profiles[cycle_count % len(profiles)]
    elif strategy == "per_topic":
        # Rotate for each topic (each topic gets different profile)
        profile = profiles[topic_index % len(profiles)]
```

**Configuration:**

```yaml
browser_fingerprint:
  user_agent_rotation:
    enabled: true
    strategy: "per_topic"  # "per_cycle" or "per_topic"
    profiles:
      - user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/131.0.0.0 ..."
        sec_ch_ua: '"Chromium";v="131", "Not_A Brand";v="24"'
        sec_ch_ua_platform: '"Windows"'
      - user_agent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ... Chrome/131.0.0.0 ..."
        sec_ch_ua: '"Chromium";v="131", "Not_A Brand";v="24"'
        sec_ch_ua_platform: '"macOS"'
      # ... more Chrome-only profiles across Windows, macOS, and Linux
```

**Why this matters:**

- Cloud VM IPs often have static user agents that get flagged
- Each profile changes the entire browser fingerprint (UA + headers), not just the user agent string
- Only Chrome profiles are used — Firefox/Safari UAs on a Chromium browser are detectable
- Helps avoid pattern detection by Google's anti-bot systems

**Strategy comparison:**

| Strategy    | Description             | Performance                      | Stealth |
| ----------- | ----------------------- | -------------------------------- | ------- |
| `per_cycle` | One UA per scrape cycle | Faster (fewer context creations) | Good    |
| `per_topic` | Different UA per topic  | Slower (more context creations)  | Better  |

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
- User-Agent rotation helps, but IP-based detection is still possible
- High request rates will still trigger blocks

For high-volume or 24/7 scraping, consider [proxy rotation](SCRAPING_BEHAVIOR.md#proxy-rotation) to distribute load across multiple IPs.

**Best practices:**

- Match timezone/geolocation to your server's IP location
- Keep `scrape_interval` reasonable (default 60s is safe) - see `config/scraper.yml`
- Monitor scraper logs for HTTP 429 (rate limit) or 403 (blocked)

See [Configuration](CONFIGURATION.md) to customize settings.
