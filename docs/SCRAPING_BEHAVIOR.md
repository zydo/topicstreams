# Scraping Behavior

## Sequential Execution

Topics are scraped **one after another sequentially**, not concurrently:

```python
for topic in topics:
    scrape_news(page, topic)
    # Next topic starts after previous completes
```

### Why Sequential?

- Avoids unusually high QPS (queries per second) that could trigger Google's rate limiting
- Reduces the chance of being blocked
- Simulates natural browsing behavior (humans don't open 10 Google searches simultaneously)

### Topic Order

- Topics are **randomized** at the start of each cycle (`shuffle(topics)`) to avoid deterministic request pattern
- Different loop iterations scrape topics in different orders
- Further mimics human behavior and distributes load
- Configurable via `randomized_order.enabled` in `config/anti_detection.yml`

## Scrape Interval Behavior

The `scrape_interval` setting in `config/scraper.yml` (default: 60 seconds) controls how often to scrape **all topics**:

### Normal Case (scraping finishes within interval)

```plaintext
Cycle 1: Scrape all topics (30s) → Wait 30s → Cycle 2 starts at exactly 60s
```

### Long-Running Case (scraping exceeds interval)

```plaintext
Cycle 1: Scrape all topics (90s) → No wait → Cycle 2 starts immediately at 90s
```

### Result Pages

During each cycle, only the first `max_pages` (default: 1) pages of each topic are scraped. This strategy assumes that between scrape intervals, the number of new articles per topic doesn't exceed one page (typically up to 10 entries).

**If you have high-volume topics or longer intervals** (e.g., >5 minutes), increase `max_pages` to 2-3 to avoid missing articles.

### Key Points

- The interval is **from the start of one cycle to the start of the next**
- If scraping takes longer than the interval, the next cycle starts **immediately** after completion
- No cycles are skipped - every topic gets scraped eventually

## Monitoring Scrape Performance

To monitor how long each cycle takes:

```bash
# Watch scraping performance in real-time
docker compose logs -f scraper | grep 'topics took'
```

### Example Output

```plaintext
topicstreams-scraper  | 2025-12-03 22:47:50,978 - INFO - 50 topics took 72.1s (exceeds 60s interval), starting next cycle immediately
```

```plaintext
topicstreams-scraper  | 2025-12-03 22:49:27,978 - INFO - 5 topics took 8.3s, waiting 51.7s until next scrape...
```

### What to Look For

If cycles consistently exceed the interval, consider:
- Increasing `scrape_interval` in `config/scraper.yml`
- Reducing `max_pages` (scrape fewer pages per topic)
- Reducing the number of tracked topics

If you see frequent HTTP 429 or 403 errors in logs (check via [scraper logs API](../API_REFERENCE.md#get-scraper-logs)), you're being rate-limited or blocked:
- For high-volume needs, see [Proxy Rotation](#proxy-rotation) below
- Review anti-detection settings in `config/anti_detection.yml`

---

# Proxy Rotation

> **Implemented — and in practice required.** Google blocks automated browsers
> from `/search` (including the News tab) even from a residential IP, so without
> a proxy the scrape returns only CAPTCHA (`/sorry/`) pages. Use **residential
> or mobile** proxies; datacenter proxies are blocked just like a direct
> connection.

## How It Works

The scraper reads a proxy from configuration and routes its persistent browser
context through it (`scraper/main.py:_build_proxy`). One endpoint is chosen per
browser launch — residential gateways rotate their exit IP server-side, so a
single sticky endpoint is the common setup, while a longer list varies the
identity across container restarts.

## Configuration

Set a proxy in **either** place (the env var wins):

```bash
# .env  (recommended — no image rebuild, keeps credentials out of the image)
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

`http`, `https`, and `socks5` schemes are supported. **Match `timezone_id` and
`geolocation`** (also in `anti_detection.yml`) to the proxy's exit country, or
the mismatch itself becomes a detection signal.

## Advanced: Different Personas per Proxy

> **Illustrative / not implemented.** The scraper currently runs a single
> persistent identity. Per-proxy personas are a possible future enhancement;
> the snippet below is conceptual.

For maximum stealth, pair each proxy with a unique browser fingerprint ("persona"):

```python
personas = {
    "proxy1": {
        "timezone": "America/New_York",
        "geolocation": {"latitude": 40.7128, "longitude": -74.0060},
        "user_agent": "Chrome/131.0.0.0 on Windows",
        "viewport": {"width": 1920, "height": 1080},
    },
    "proxy2": {
        "timezone": "Europe/London",
        "geolocation": {"latitude": 51.5074, "longitude": -0.1278},
        "user_agent": "Chrome/131.0.0.0 on macOS",
        "viewport": {"width": 1440, "height": 900},
    },
    # ...more personas
}
```

This makes each proxy appear as a completely different user (different location, device, browser).

## Benefits

- **Distribute load** - Requests come from different IPs, avoiding single-IP rate limits
- **Reduce blocking risk** - Even if one proxy gets blocked, others continue working
- **Enable concurrency** - Scrape multiple topics in parallel without triggering detection
- **24/7 operation** - Sustained high-volume scraping becomes feasible
- **Geographic diversity** - Appear as users from different locations

## Implementation Considerations

- Proxy pool management and health checks
- Persona configuration and rotation strategy
- Error handling for proxy failures

## When You Need This

### You Probably DON'T Need Proxies If:

- Scraping ≤10 topics with default settings (60s interval)
- Sequential scraping is fast enough for your use case
- You're okay with occasional rate limiting

### You SHOULD Consider Proxies If:

- Scraping >20 topics with aggressive intervals (<30s)
- Need true real-time updates (interval near 0)
- Switching to concurrent scraping for performance
- Experiencing frequent HTTP 429/403 blocks

## Future Plans

This feature is not currently implemented but is on the roadmap. Contributions welcome!

### See Also

- [Anti-Bot Detection](ANTI_BOT_DETECTION.md) - Current stealth measures
- [Configuration](../README.md#yaml-configuration-files) - YAML configuration settings
- [API Reference - Logs](API_REFERENCE.md#logs) - Monitor scraper performance via API
