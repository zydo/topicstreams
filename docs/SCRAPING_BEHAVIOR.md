# Scraping Behavior

## Search Engines

Scraping is **pluggable across search engines**. Each engine implements the
`SearchSource` contract (`scraper/sources/`) — how to build its results URL,
find result items, parse an item, and detect a block — so the runner
(`scraper/scraper.py`) stays engine-agnostic.

All four supported engines are enabled by default (`engine_strategy: all`).

| Engine      | Name (config) | Status                                                  |
| ----------- | ------------- | ------------------------------------------------------- |
| Google News | `google`      | On by default; News tab, newest-first, past hour.       |
| Bing News   | `bing`        | On by default. Date sort + freshness via `qft` filters. |
| Yahoo News  | `yahoo`       | On by default. Links unwrapped from Yahoo's redirector. |
| Brave News  | `brave`       | On by default. Freshness via `tf`; relevance-ranked.    |

**DuckDuckGo is not supported** — it hard-blocks automated access, so it cannot
be scraped reliably. See [DUCKDUCKGO_UNSUPPORTED.md](DUCKDUCKGO_UNSUPPORTED.md)
for the investigation and decision.

Cross-engine duplicates are free: a news row's id is derived from its
normalized URL, so the same article seen on two engines collapses to one feed
event via the `topic_news` junction. Which engines surfaced each feed event
(and when each first did) is recorded in `topic_news_engines`, so an article
found by both Google and Bing is stored once but attributed to both. The feed
API and UI expose an **engine filter** (orthogonal to the topic filter), and
each feed entry carries the full list of engines that found it.

## Execution model: one worker per engine

Each enabled engine runs in its **own worker thread** with its own Playwright
instance and its own persistent browser context (`scraper/worker.py`,
`scraper/browser.py`). The workers run **in parallel** and don't know about each
other — the only shared state is the database (cross-engine duplicates resolve
there via the URL-derived news id) and a small in-memory snapshot the supervisor
reads for the saturation signal.

```plaintext
supervisor (scraper/main.py)
├── worker:google ─ sweep topics → sleep → repeat   ┐
├── worker:bing   ─ sweep topics → sleep → repeat   │ run concurrently,
├── worker:yahoo  ─ sweep topics → sleep → repeat   │ each at its own pace
└── worker:brave  ─ sweep topics → sleep → repeat   ┘
```

**Why per-engine workers?** Engines can't see each other's traffic, so running
them concurrently does **not** raise any single engine's request rate — parallel
across engines is detection-neutral. Mapping one worker to one engine also lines
up with the per-engine cooldown (each worker is the sole owner of its own
cooldown state), and gives graceful degradation: a struggling engine benches
itself while the others keep going.

Within a single engine, topics are still scraped **one at a time and paced** (see
below) — the parallelism is *across* engines, never across topics on the same
engine (two topics hitting Google at once from one IP is a burst pattern that
engine *can* see).

> The `scraper.engine_strategy` setting (`all` / `fallback` / `rotate`) predates
> this model and no longer governs execution: every enabled engine now runs in
> its own worker. The key is retained for back-compat and may be removed.

### Topic order and coverage fairness

- Each worker **reshuffles** the topic list at the start of every sweep
  (`randomized_order.enabled` in `config.yml`), so a slow or benched engine
  doesn't always cover the head of the list — coverage averages out across
  sweeps.
- A benched engine skips its topics **without** spending pacing time, then
  probes once when its cooldown window expires (see [cooldown](#proactive-pacing-vs-reactive-cooldown)).

## Proactive pacing vs. reactive cooldown

Pacing is the **primary** throttle; cooldown is the **reactive backstop**.

- **Proactive pacing** (`scraper.pacing`): each worker enforces a per-engine
  *floor* on the interval between requests (`default_min_interval`, with
  `per_engine` overrides and a `jitter_ratio`). This keeps each engine at a
  known-safe rate by design.
- **Adaptive cooldown** (`scraper.cooldown`): if the pace is still too fast and a
  429/403/503/block lands, the engine benches itself for an exponential backoff
  window and probes once before resuming.

**Why pace proactively instead of relying on cooldown alone?** Cooldown only acts
*after* a block. Leaning on it as the throttle means oscillating across the
block line — and sustained tripping can promote a soft 429 into a hard CAPTCHA or
IP-level ban, which is attached to the **shared exit IP** and would poison *every*
engine on that machine. Pacing keeps you under the line; cooldown catches the
misjudgment.

Set a longer floor for engines that throttle sooner (Brave is the usual one)
rather than discovering their limit by getting blocked:

```yaml
scraper:
  pacing:
    default_min_interval: 2.0   # seconds between requests, per engine
    jitter_ratio: 0.25
    per_engine:
      brave: 4.0
```

## Scrape interval behavior

`scrape_interval` (default 60s) is the **sweep period each worker targets**: one
full pass over the topics per interval.

- **Few topics** — the sweep finishes early and the worker waits out the
  remainder of the interval (≈ one sweep per interval).
- **Many topics** — the per-request pace floor dominates, the sweep runs longer
  than the interval, and the engine simply falls behind at its safe rate (best
  effort). No worker is ever forced to exceed its safe pace to "keep up."

So if you have, say, 120 topics and a 60s interval, an engine paced at 0.5s/topic
can complete a sweep in ~60s, but one paced at 4s/topic (Brave) cannot — it'll
sweep more slowly and cover fewer topics per minute. That's expected. When the
*robust* engines (not just the canary) start falling behind and blocking, the
[saturation signal](#exit-ip-saturation-signal) tells you the exit IP is at
capacity and it's time to scale out.

### Result pages

Each sweep scrapes only the first `max_pages` (default 1) pages per topic. This
assumes that between an engine's sweeps, the new articles per topic don't exceed
one page (~10 entries). For high-volume topics or long intervals (>5 min),
increase `max_pages` to 2–3.

## Exit-IP saturation signal

All workers share one exit IP, so there's a ceiling on how many topics that IP
can serve before engines start getting throttled. The supervisor watches the
per-engine cooldown snapshots and flags **saturation** — but it *weights* them:

- **Canary engines** (`saturation.canary_engines`, default `[brave]`) trip first
  by nature, so their cooling is a signal about *that engine*, not the IP. They
  are excluded from the count.
- Saturation is flagged only when at least `saturation.robust_threshold`
  (default 2) **robust** (non-canary) engines are cooling at the same time. That
  is the cue to **scale horizontally** — divide traffic across machines with
  different exit IPs — rather than to slow everything down.

When saturated, the scraper logs a loud `EXIT IP SATURATION SUSPECTED` warning
naming the throttled robust engines.

```yaml
scraper:
  saturation:
    canary_engines: [brave]
    robust_threshold: 2
```

## Per-engine cycle accounting

Each worker records its sweep as a row in `scraper_cycles`, tagged with its
`engine` (the `/monitor` cycle timeline shows one row per engine per sweep). The
supervisor handles the cross-engine housekeeping that must happen once: purging
old rows, publishing the per-engine cooldown snapshot for the monitor, and
evaluating the saturation signal.

## Monitoring Scrape Performance

Each worker logs a per-engine summary at the end of every sweep:

```bash
# Watch per-engine sweeps in real-time
docker compose logs -f scraper | grep 'swept'
```

### Example Output

```plaintext
topicstreams-scraper  | INFO - [google] swept 50 topics, 312 entries, 47 new events
topicstreams-scraper  | INFO - [brave] swept 18 topics, 41 entries, 6 new events
```

The richer view is the `/monitor` page (per-engine health, latency percentiles,
and the per-engine cycle timeline) — see [OBSERVABILITY.md](OBSERVABILITY.md).

### What to Look For

If a robust engine consistently falls behind (few topics/sweep) or you see an
`EXIT IP SATURATION SUSPECTED` warning, the exit IP is at capacity — consider:
- Increasing `scrape_interval` in `config.yml`
- Reducing `max_pages` (scrape fewer pages per topic)
- Reducing the number of tracked topics
- **Scaling out to another machine/exit IP** (the saturation signal's intent)

If only the **canary** engine (Brave) throttles, that's expected for it alone —
give it a longer `pacing.per_engine` floor rather than scaling out.

If you see frequent HTTP 429 or 403 errors in logs (check via [scraper logs API](../API_REFERENCE.md#get-scraper-logs)), tune the per-engine `pacing` floor and/or:
- For high-volume needs, see [Proxy Rotation](#proxy-rotation) below
- Review anti-detection settings in `config.yml`

---

# Proxy Rotation

> **Implemented — and in practice required.** Google blocks automated browsers
> from `/search` (including the News tab) even from a residential IP, so without
> a proxy the scrape returns only CAPTCHA (`/sorry/`) pages. Use **residential
> or mobile** proxies; datacenter proxies are blocked just like a direct
> connection.

## How It Works

The scraper reads a proxy from configuration once and routes **every** engine
worker's browser context through it (`scraper/browser.py:build_proxy`). One
endpoint is chosen per run — residential gateways rotate their exit IP
server-side, so a single sticky endpoint is the common setup, while a longer list
varies the identity across container restarts.

All engine workers currently share **one** exit IP. That is what the
[saturation signal](#exit-ip-saturation-signal) measures the capacity of: when
the robust engines start throttling, the remedy is more exit IPs (today: run
additional scraper instances on separate machines/proxies), since each
additional IP is an independent per-engine rate budget.

## Configuration

Set a proxy in **either** place (the env var wins):

```bash
# .env  (recommended — no image rebuild, keeps credentials out of the image)
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

`http`, `https`, and `socks5` schemes are supported. **Match `timezone_id` and
`geolocation`** (also in `config.yml`) to the proxy's exit country, or
the mismatch itself becomes a detection signal.

## Advanced: Different Personas per Proxy

> **Illustrative / not implemented.** Each engine worker has its own persistent
> context (so cookies don't mix), but they all share one fingerprint and one
> exit IP. Per-proxy personas are a possible future enhancement; the snippet
> below is conceptual.

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

## What's implemented vs. not

**Implemented today:** reading a proxy from `SCRAPER_PROXY` / `config.yml`,
choosing one endpoint per browser launch, and routing the persistent context
through it. Residential gateways rotate their exit IP server-side, so a single
sticky endpoint already varies the apparent IP.

**Not implemented** (would be future work if ever needed): in-app proxy-pool
health checks / failover, per-proxy personas, and binding different engine
workers to different exit IPs from one process. Engine workers run concurrently
but all share a single exit IP and one fingerprint identity, so these snippets
marked *illustrative* are conceptual. Horizontal scale-out today means running
additional scraper instances, each with its own proxy.

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

### See Also

- [Anti-Bot Detection](ANTI_BOT_DETECTION.md) - Current stealth measures
- [Configuration](../README.md#yaml-configuration-files) - YAML configuration settings
- [API Reference - Logs](API_REFERENCE.md#logs) - Monitor scraper performance via API
