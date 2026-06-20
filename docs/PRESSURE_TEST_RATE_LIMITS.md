# Per-Engine Rate-Limit Pressure Test (2026-06-20)

**Goal:** a rule-of-thumb for how many flat-out queries each search engine
tolerates from a single egress IP before it hard-blocks (429 / 403 / 503 /
CAPTCHA), with every artificial delay removed so the only cost per request is
real Chromium render + network.

**Egress IP:** `35.197.42.202` (host NAT; the live scraper shares this IP).
**Method:** standalone harness `scripts/pressure_test.py`, run inside the
scraper image with the live scraper paused. It reuses the production
`SearchSource` classes (identical URLs, headers, block detection) and the
runtime-detected Chrome fingerprint, but fires an endless stream of *distinct*
keywords back-to-back with **no pacing floor, no `page_settle`, no
human-simulation scroll/mouse**. Engines were run **sequentially**, one at a
time, each stopped at the **first confirmed block** (3 consecutive block
signals) or a safety cap of 2000 requests / 540 s.

> Why a standalone harness and not "add 1,000,000 topics": after the
> `b3d25e2` scheduler rewrite the per-engine request rate is **decoupled from
> topic count**. The event-driven min-heap *staggers* every topic across
> `[0, scrape_interval]` and re-arms each independently, and the `_Pacer`
> floors the gap between requests. A large `scrape_interval` therefore *spreads*
> requests out (the opposite of the old per-sweep burst), and the pace floor
> caps throughput regardless of how many topics exist. To measure raw tolerance
> you must bypass both — which is exactly what the harness does.

## Results

| Engine | Outcome | Queries OK before block | Sustained rate | Block signal | Render latency (median / p95) | Flat-out ceiling¹ |
|--------|---------|------------------------:|---------------:|--------------|------------------------------:|------------------:|
| **Brave**  | 🔴 **blocked** | **7** (in ~9.6 s) | ~44 req/min | `HTTP 429` (×3, ~120 ms — instant reject) | 801 / 1167 ms | ~75 req/min |
| **Google** | 🔴 **blocked** | **93** (in ~61.6 s) | ~91 req/min | `HTTP 429` (×3) | 271 / 421 ms | ~221 req/min |
| **Yahoo**  | 🟢 no block | **≥687** (full 540 s) | ~76 req/min | — (all 200) | 550 / 667 ms | ~109 req/min |
| **Bing**   | 🟢 no block | **≥981** (full 540 s) | ~109 req/min | — (all 200) | 277 / 398 ms | ~217 req/min |

¹ `60000 / median_latency` — the rate a single browser could hit if render+network
were the only limit. Sustained rate is lower because of per-request page
new/close overhead. For Yahoo/Bing the "queries before block" figures are
**lower bounds** — the test budget ran out, not the engine's tolerance.

## Rule of thumb

- **Brave — ~5–7 queries, then back off.** Strictest by far. It does not
  meter by rate so much as by a tiny burst budget: the 8th flat-out request
  429'd regardless, ~10 s in. Treat it as a canary, not a workhorse (the config
  already lists it under `saturation.canary_engines`). Give it a long per-engine
  pace floor and expect frequent cooldowns.
- **Google — ~90 queries per minute.** Blocked at request 93 right around the
  1-minute mark while sustaining ~90 req/min, which reads like a **~90 req/min
  budget**. Stay comfortably under it — target ≤ ~60 req/min for headroom.
- **Yahoo — ≥75 req/min, 600+ queries, no throttle observed.** Robust at this
  rate; true ceiling is above what we measured.
- **Bing — ≥100 req/min, 950+ queries, no throttle observed.** The most
  tolerant engine tested; true ceiling is above what we measured.

**Ranking (most → least tolerant): Bing ≳ Yahoo ≫ Google ≫ Brave.**

## Caveats

1. **This is the *raw* ceiling, not a deployable rate.** All anti-detection
   timing was stripped to find the boundary. The production scraper deliberately
   operates far below this (pace floor + per-topic interval) to avoid promoting
   soft throttles into hard IP-level blocks on the shared exit IP.
2. **Per-IP and time-dependent.** Tolerances are measured from one IP at one
   point in time; they reset/drift and differ per IP, per proxy, and with prior
   reputation. Re-run before treating any number as fixed.
3. **Robust-engine figures are lower bounds.** Yahoo and Bing were capped by the
   9-minute / 2000-request test budget, not by blocking.
4. **Brave is burst-limited, not rate-limited** — "queries before block"
   (~7) is the meaningful number for it, not req/min.

## Operational implications for the production scheduler

- With defaults (`scrape_interval` 60 s, `pacing.default_min_interval` 2.0 s),
  each worker is capped at ~30 req/min. That is safe for Google (well under
  ~90/min), Yahoo, and Bing — but is still risky for **Brave**, whose ~7-request
  burst budget can be exhausted even at a modest steady rate. Consider a
  Brave-specific `pacing.per_engine` floor (several seconds) so it never bursts.
- The adaptive cooldown (`scraper/cooldown.py`) is the correct backstop for
  Brave/Google: both returned clean `429`s that `BLOCK_STATUSES` already catches,
  so a block benches the engine and a single probe resumes it.

## Reproducing

```bash
docker stop topicstreams-scraper           # pause the live scraper
docker compose run --rm -T \
  -v "$PWD/scripts/pressure_test.py:/app/scripts/pressure_test.py:ro" \
  -v "$PWD/.ptest_out:/out" \
  scraper python -u /app/scripts/pressure_test.py <engine> \
    --max-requests 2000 --max-seconds 540 --consec-block 3 --out /out/<engine>.json
docker start topicstreams-scraper          # restore the live scraper
```

Per-request rows and summaries are written to `.ptest_out/<engine>.json`.
