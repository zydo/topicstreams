# Anti-Bot Detection

> **Configuration:** All anti-detection strategies are configurable via `config.yml` (created from `config.yml.example` template on first-time setup). See [Configuration](docs/CONFIGURATION.md) for details on customizing each strategy.

TopicStreams uses sophisticated techniques to make the scraper appear as a real human user, minimizing the risk of being blocked by Google.

## How It Works

The scraper uses **Playwright** with its bundled **native Chromium** and a runtime-derived browser fingerprint to mimic a genuine user. Notably, **playwright-stealth is disabled by default and must stay that way for Google** — its JS patches are themselves detectable (see [Playwright-Stealth Patches — DISABLED](#3-playwright-stealth-patches--disabled) below).

All strategies below are loaded from `config.yml` and can be enabled/disabled individually.

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
# Loaded from config.yml
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

One **persistent** browser context **per engine** is launched and reused for the
whole run (each engine runs in its own worker — see
[Execution model](SCRAPING_BEHAVIOR.md#execution-model-one-worker-per-engine)),
configured to match a real Chrome user (all values configurable in YAML). Each
worker's persistent context keeps that engine's cookies/cache on disk across
container restarts under its own profile directory
(`.browser_profiles/<engine>`); all engines share one fingerprint and one exit
IP (see [Single identity by design](#5-single-identity-by-design)):

```python
# All values loaded from config.yml (see scraper/browser.py:_launch_context)
context = p.chromium.launch_persistent_context(
    str(profile_dir),                       # per-engine on-disk profile, survives restarts
    headless=True,
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
        "longitude": anti_detection_config.geolocation_longitude,
    },
    color_scheme=anti_detection_config.color_scheme,
    extra_http_headers=_build_headers(profile),
)
```

**Key points:**

- **User Agent**: Derived at startup from the installed browser version (see `detect_fingerprint` in `scraper/browser.py`) — a stale hardcoded UA is an instant CAPTCHA
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

| Detection Vector      | Covered by                                          |
| --------------------- | --------------------------------------------------- |
| `navigator.webdriver` | `--disable-blink-features=AutomationControlled`     |
| User agent / brands   | Runtime-derived fingerprint (`detect_fingerprint`) |

### 4. Memory Management

Each engine worker runs one long-lived persistent context, which would otherwise
grow unbounded over thousands of sweeps — on a swap-less host this once exhausted
RAM and livelocked the box (see [postmortem](POSTMORTEM_2026-06-13_OOM_HANG.md)).
Two measures bound that growth:

- **Fresh page per topic**: each scrape opens a new page and closes it in a
  `finally`, so per-page DOM/JS state never accumulates (`scrape_topic` in
  `scraper/scraper.py`).
- **Context recycling**: each worker closes and relaunches its Chromium context
  every `scraper.browser_recycle_cycles` sweeps (default 50). The on-disk
  persistent profile (cookies/cache) survives the recycle, so identity is
  preserved while accumulated memory is released (`scraper/worker.py`).

**Other configurable behaviours:**

- **Proactive pacing** (`scraper.pacing`): a per-engine floor on the interval
  between requests, the primary throttle (see
  [Proactive pacing](SCRAPING_BEHAVIOR.md#proactive-pacing-vs-reactive-cooldown)).
- **Randomized scheduling**: each worker schedules topics on a per-topic-interval
  min-heap with boot stagger and per-reschedule jitter (see
  [Scheduling](SCRAPING_BEHAVIOR.md)), so the request pattern is non-deterministic
  and a benched engine simply resumes its due topics rather than re-covering a
  fixed head-of-list order.
- **Human-Like Behaviour** (`page_interaction.human_simulation`): random
  scrolling and mouse movement during page loads to simulate reading; the ranges
  are tunable in `config.yml`.

### 5. Single identity by design

The scraper deliberately runs **one** consistent fingerprint, not a rotating
pool. The single identity is derived at startup from the installed Chromium
version (`detect_fingerprint` in `scraper/browser.py`) so the claimed UA always
matches the real browser — the property Google actually checks. UA/profile
rotation is **not** used: against a single residential IP it adds little (the IP,
not the UA, is the dominant signal) and a mismatched or implausible rotated
fingerprint is itself a detection risk. To distribute load across identities,
the supported lever is multiple residential/mobile **proxy** exits (see
[Proxy Support](SCRAPING_BEHAVIOR.md#proxy-rotation)), not UA rotation.

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
- IP-based detection is the dominant signal — a clean fingerprint doesn't help if the IP is flagged
- High request rates will still trigger blocks (and now trigger the adaptive per-engine cooldown — see [Scraping Behavior](SCRAPING_BEHAVIOR.md))

For high-volume or 24/7 scraping, route through residential/mobile [proxies](SCRAPING_BEHAVIOR.md#proxy-rotation) to distribute load across multiple IPs.

**Best practices:**

- Match timezone/geolocation to your server's IP location
- Keep `scrape_interval` reasonable (default 60s is safe) - see `config.yml`
- Monitor scraper logs for HTTP 429 (rate limit) or 403 (blocked)

See [Configuration](CONFIGURATION.md) to customize settings.
