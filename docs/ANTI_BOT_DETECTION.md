# Anti-Bot Detection

> **Configuration:** All anti-detection strategies are configurable via `config/anti_detection.yml` (created from `config/anti_detection.yml.example` template on first-time setup). See [Configuration](docs/CONFIGURATION.md) for details on customizing each strategy.

TopicStreams uses sophisticated techniques to make the scraper appear as a real human user, minimizing the risk of being blocked by Google.

## How It Works

The scraper uses **Playwright** with its bundled **native Chromium** and a runtime-derived browser fingerprint to mimic a genuine user. Notably, **playwright-stealth is disabled by default and must stay that way for Google** — its JS patches are themselves detectable (see [Playwright-Stealth Patches — DISABLED](#3-playwright-stealth-patches--disabled) below).

All strategies below are loaded from `config/anti_detection.yml` and can be enabled/disabled individually.

> **Hard-won findings (verified 2026-06-11):** Google CAPTCHAs `/search` whenever
> any of these is true, regardless of everything else:
>
> 1. **The claimed UA version doesn't match the real browser** (e.g. a hardcoded
>    `Chrome/131` UA on a v149 browser). The scraper therefore derives the UA from
>    the installed browser version at startup instead of using a static string.
> 2. **`navigator.webdriver` is `true`** — requires
>    `--disable-blink-features=AutomationControlled`.
> 3. **The browser runs under CPU emulation** (amd64 image via Rosetta 2 on Apple
>    Silicon). This is why the scraper uses Playwright's bundled Chromium on the
>    host's native architecture, not `channel="chrome"`: Google Chrome has no
>    Linux arm64 build.

### 1. Browser Launch Arguments

```python
# Loaded from config/anti_detection.yml
browser_args = anti_detection_config.browser_args  # Configurable
browser.launch(
    headless=True,
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
    "--disable-gpu",                               # No GPU in the container
    "--window-size=1920,1080",                     # Consistent viewport
    "--disable-blink-features=AutomationControlled",  # navigator.webdriver = false
]
```

- The arg set is deliberately minimal: extra feature-disable switches make the
  browser *less* like stock Chrome and were part of why Google blocked the scraper
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

- **User Agent**: Derived at startup from the installed browser version (see `_detect_fingerprint` in `scraper/main.py`) — a stale hardcoded UA is an instant CAPTCHA
- **Permissions**: Configurable browser permissions (default: geolocation)
- **Timezone & Geolocation**: Recommended to match your server's IP location (see [Configuration](CONFIGURATION.md))
- **HTTP Headers**: Only the Sec-CH-UA trio matching the derived UA (see below)

### HTTP Headers

Playwright's `extra_http_headers` are applied to **every** request (documents, XHR, images). Real browsers vary headers like `Accept` and `Sec-Fetch-*` per request type, so forcing them globally is itself a detection signal — the scraper used to do this and it contributed to CAPTCHA blocks. Only the Sec-CH-UA client hints (which genuinely are constant across requests) are set, matching the derived user agent:

```python
def _build_headers(profile: FingerprintProfile) -> dict:
    base_headers = dict(anti_detection_config.http_headers)  # empty by default
    base_headers["Sec-Ch-Ua"] = profile.sec_ch_ua
    base_headers["Sec-Ch-Ua-Mobile"] = "?0"
    base_headers["Sec-Ch-Ua-Platform"] = profile.sec_ch_ua_platform
    return base_headers
```

| Header               | Source                       | Purpose                   |
| -------------------- | ---------------------------- | ------------------------- |
| `Sec-Ch-Ua`          | `profile.sec_ch_ua`          | Browser brand and version |
| `Sec-Ch-Ua-Mobile`   | `"?0"`                       | Mobile device indicator   |
| `Sec-Ch-Ua-Platform` | `profile.sec_ch_ua_platform` | Operating system platform |

This ensures Sec-CH-UA headers always match the user agent being used, which is critical for avoiding detection.

### 3. Playwright-Stealth Patches — DISABLED

playwright-stealth support exists in the code (configurable via `playwright_stealth.enabled` in YAML) but is **disabled by default, and must stay disabled for Google**: bisection on 2026-06-11 showed Google detects the stealth JS patches themselves (fake plugins, monkey-patched native functions) and CAPTCHAs `/search` whenever they are applied — the identical setup passes with stealth off.

The signals stealth used to mask are covered without JS patching:

| Detection Vector      | Covered by                                        |
| --------------------- | ------------------------------------------------- |
| `navigator.webdriver` | `--disable-blink-features=AutomationControlled`   |
| User agent / brands   | Runtime-derived fingerprint (`_detect_fingerprint`) |

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

Google's JavaScript sees an unmodified Chromium with one launch-flag tweak:

```javascript
navigator.webdriver        // false (via --disable-blink-features=AutomationControlled)
navigator.userAgent        // "... Chrome/<real major>.0.0.0 ..." (version-matched, no "Headless")
navigator.languages        // ["en-US"]
navigator.platform         // "Linux x86_64" (matches the real container OS)
```

No JS objects are patched — that is the point. Patched natives are themselves
detectable and were getting the scraper blocked.

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
