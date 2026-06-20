# Scrape observability

TopicStreams surfaces scrape behavior two ways: a single health signal on the
wire masthead (`GET /api/v1/status`), and a richer ops console at **`/monitor`**
backed by `GET /api/v1/metrics`. This page documents the richer view.

> **Scope choice.** This is a single-user Docker-Compose deployment, so
> observability is a built-in page rather than a Prometheus + Grafana stack.
> The data lives in Postgres tables the app already uses; no extra services.
> (A Prometheus exposition endpoint could be added later without disturbing
> this — the aggregation queries are in `common/database.py`.)

## The `/monitor` page

Open `http://<host>/monitor` (a `monitor` chip in the wire masthead links to
it). It polls `GET /api/v1/metrics?window=…` on the UI status cadence and
renders:

- **Overall strip** — active topics, total filed, overall scrape success, feed
  freshness, last cycle duration, and scrapes-in-window (blocked / failed).
- **Engines table** — one row per engine: health dot + label, scrapes, success
  %, fetch latency (avg / p95), items parsed, 0-parse count (selector-rot
  signal), blocks (429/403/503), failures, last HTTP status, last scrape time.
- **Recent cycles** — a sparkline of per-cycle durations (green ok / red
  failed) plus a list (duration, topics, parsed, new events).
- **Recent failures** — newest-first failed scrapes with engine, topic, status,
  and error message.

The **Window** selector (1h / 6h / 24h) re-queries the endpoint; the choice is
remembered across reloads. The page shares the wire UI's theme + palette.

## Per-engine `health`

Computed by `classify_engine` in `api/v1/metrics.py` from the window's
aggregate row. It's a triage **hint** — the raw counts are always shown
alongside it, so the label isn't the whole story.

| Label      | Meaning                                                                           |
| ---------- | --------------------------------------------------------------------------------- |
| `idle`     | No scrapes for this engine in the window.                                         |
| `blocked`  | The most recent scrape was a throttle/block: HTTP 429 / 403 / 503, or a connection-level teardown with no HTTP status (e.g. `ERR_CONNECTION_CLOSED`). |
| `cooldown` | The scraper currently has the engine benched; shows a countdown to the next probe. |
| `parsing`  | Sustained selector rot: ≥3 scrapes, ≥1 success, and every success parsed 0 items. |
| `degraded` | Success rate below 0.75 (includes a total failure: 0%).                           |
| `healthy`  | Otherwise.                                                                        |

`blocked` keys off the **latest** scrape so an engine that recovered shows
healthy, and one currently throttled shows blocked — even if its long-run
success rate is high. A network-level block (no HTTP status) is recognized via
`common/block_signals.is_network_block`. `parsing` requires a sustained run (≥3
scrapes) so a single quiet hour for one topic doesn't trip it.

`cooldown` is sourced differently from the others: each engine worker owns an
in-process `EngineCooldownTracker`, and the scraper's supervisor snapshots them
all to the `engine_cooldowns` table once per `scrape_interval` (the API can't see
the live trackers across the process boundary). It overrides the log-derived label while an engine is
benched — including engines that have produced no logs in the window and would
otherwise drop off the table. A stale snapshot (scraper down) is ignored.

## What gets captured, and what `duration_ms` means

- **`scraper_logs.duration_ms`** — wall-clock of the navigation itself
  (`page.goto` through `domcontentloaded`), per page-attempt. It deliberately
  **excludes** the anti-detection settle wait and human-simulation scroll/mouse
  jitter, which would otherwise dominate the number with intentional delay.
  So it reflects real results-page fetch latency. Nullable for legacy rows and
  for attempts that failed before navigation completed.
- **`scraper_cycles`** — one row per engine worker's sweep over the topics:
  `started_at`, `finished_at`, `duration_seconds`, `topics_count`,
  `entries_parsed`, `new_events`, `success`, `error`, and `engine` (which
  worker; null for legacy single-loop rows). Persisted so the monitor can plot
  per-engine sweep durations over time.

Both are purged on the same retention window as news/logs
(`news_retention_days`, each scrape cycle).

## Schema evolution

`postgres/init.sql` is the canonical schema (fresh volumes). Because it only
runs when the Postgres data dir is empty, additive changes to an **existing**
volume are applied by `db.ensure_schema()` (`common/database.py`), called at
startup by **both** the API and the scraper. The statements are
`IF NOT EXISTS` and each takes an `AccessExclusiveLock`, so the two processes
racing at boot is safe: the second blocks on the lock, then sees the object
exists and no-ops. `ensure_schema()` is what added `duration_ms` and the
`scraper_cycles` table to running deployments without a manual migration.
