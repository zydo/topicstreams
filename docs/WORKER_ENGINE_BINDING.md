# Worker ↔ engine binding & on-demand-search concurrency

> Design record (2026-06-21). Why each search engine is bound to one worker /
> warm browser session, the concurrency limit that surfaces for **on-demand web
> search**, the backpressure fix we shipped, and the future direction
> (decoupling workers from engines into a warm-session pool). Companion to
> [TASK_SCHEDULER.md](TASK_SCHEDULER.md), [WEB_SEARCH_WARMUP.md](WEB_SEARCH_WARMUP.md),
> and [SCRAPING_BEHAVIOR.md](SCRAPING_BEHAVIOR.md).

## TL;DR

- **Today:** 1 engine = 1 worker = 1 warm browser context (single thread). Simple
  and lock-free, but on-demand web searches for a given engine are served
  **strictly sequentially** (~one warm session, ~5-6s/serve).
- **Symptom:** concurrent `/api/v1/search` requests mostly `timeout` (only ~4 fit
  in the 25s window); it was *not* cooldown and *not* parallel-but-slow.
- **Shipped (Layer 1):** per-engine **in-flight cap** (`max_in_flight`,
  default 4) → the N+1th request is rejected fast with **HTTP 429 + Retry-After**
  instead of a silent 25s hang; the worker also skips serving jobs older than the
  request timeout. Plus a planned web-search **observability** panel on `/monitor`.
- **Future:** for real search concurrency, **decouple workers from engines** — a
  *pool* of warm "generalist" sessions pulling from a shared cross-engine queue.
  The catch that doesn't go away: the rate limit is **per-(engine × exit-IP)**, so
  a pool needs **shared per-engine pacing + cooldown** (reintroducing the
  cross-context coordination the current model avoids).

## Why engines are bound to workers today

Introduced with the per-engine parallel workers (2026-06-19; see
`scraper/worker.py`, `scraper/main.py`):

1. **Cross-engine parallelism is detection-neutral.** Engines can't see each
   other's traffic, so running google/bing/yahoo/brave workers in parallel adds
   no per-engine detection risk — the risk is per-engine request *rate*.
2. **Per-engine cooldown maps 1:1 to a single-writer worker** → no locking around
   the cooldown/pacing state (`scraper/cooldown.py`); each worker is the sole
   writer of its own engine's state.
3. **One stable identity per engine.** Each worker has its own persistent profile
   dir (cookies/warmth isolated), and Chromium holds a `SingletonLock` per
   profile — one Chromium process per profile.
4. **Continuous news scraping** per engine fits a dedicated long-lived session.

On-demand web search was layered on by **reusing** each engine's existing warm
news session (see [WEB_SEARCH_WARMUP.md](WEB_SEARCH_WARMUP.md)) rather than
spinning up new sessions — one request stream per engine per exit IP.

## The concurrency limit (the bug report)

On-demand web search (`GET /api/v1/search`, Google-only for now) mostly returned
`timeout`/`blocked` under concurrent load; only some succeeded.

**Live diagnosis (2026-06-21):**

- **Not cooldown.** Google had `failures=0`, `next_probe_at=NULL` throughout; a
  benched engine returns `unavailable`, not `timeout`.
- **Not "parallel but slow."** One worker on one context in one thread → two
  Google searches *cannot* run at once. They're served **strictly sequentially,
  FCFS** (`claim_web_search_job ORDER BY created_at`), and the worker drains the
  web queue **before** news (web is polled first each turn).
- **It's capacity.** A single isolated search serves in ~4s, but the `request_timeout`
  is 25s and each serve is ~5-6s (incl. the ~2s pace floor), so only ~4 concurrent
  fit; the rest wait past 25s and time out. A web job that lands behind an
  in-flight ~15s news scrape eats into the window too.

So the behavior already matched "served if your turn comes in time, else timeout"
— there was no logic bug, just an unbounded queue with a silent failure mode.

## What we shipped — Layer 1 (backpressure + skip-stale)

Commit `59acd42`. The chosen mechanism was a **semaphore (max in-flight = N)**,
*not* a strict "singleton acquire-or-fail":

- A strict singleton is just **N=1** — it rejects any request arriving while one
  is serving, throwing away cheap useful queueing.
- The semaphore admits up to **N** in-flight (pending+claimed) jobs per engine
  (they wait their turn, served sequentially) and rejects the **N+1th fast**.
  Singleton is the degenerate N=1 case.

Implementation:
- `scraper.web_search.max_in_flight` (default 4) — sized to how many serves fit
  in `request_timeout` (~6s each → ~4).
- **Capacity-checked enqueue** (`db.enqueue_web_search` with `max_in_flight`): an
  atomic `INSERT … SELECT … WHERE count(in-flight) < N` that returns no row when
  full. Soft cap (a hair of over-admission under heavy races is fine).
- Dispatcher (`api/websearch.py`): on a full engine, record status **`busy`** and
  fall back to an engine with room (if fan-out is enabled); the endpoint maps
  `busy` → **HTTP 429 + `Retry-After`**. Scoped to **web search only** — news is
  internal/scheduled with no caller to fail-fast.
- **Skip stale jobs:** the worker no longer claims jobs older than the request
  timeout (`claim_web_search_job(max_age_seconds=…)`) — a requester that already
  gave up shouldn't burn the single serve slot.
- **Observability (planned, same Layer 1):** web one-offs bypass
  `scraper_logs`/`scraper_cycles` (news-only) and the job row is deleted after the
  API reads it, so failures leave no durable trace and `/monitor` shows nothing.
  Add per-request logging (`query, engine, outcome, latency, attempts`) + a
  web-search panel. (Top of [TODO](../TODO.md) "NEXT UP".)

Verified live: 8 concurrent requests → 4 admitted, 4 immediate `429` (~50ms).

## The architecture discussion (future directions)

### Option A — multiple workers for *one* engine (same IP) → rejected
Two contexts hitting one engine from the same exit IP **doubles that engine's
request rate from the IP** → more blocks, and two identities for one engine looks
suspicious + splits warmth. The right way to scale one engine's throughput is
**more exit IPs** (separate machines) — which the saturation signal
(`scraper/saturation.py`) already flags. More contexts per IP fights the rate we
pace against.

### Option B — multi-engine fan-out → partial, deferred
Spread concurrent searches across the existing per-engine workers (N engines =
N concurrent). Real, and it's the "add other engines later" already planned — but
currently web search is Google-only by request (`web_search.engines = [google]`),
so this is parked.

### Option C — decouple workers from engines (warm-session pool) → the real future fix
A **pool of M warm "generalist" sessions**, each able to serve *any* engine,
pulling from a **shared cross-engine task queue**. Benefits:
- **M-way concurrency for on-demand search** — a Google burst spreads across M
  contexts (load-balanced), directly solving the limit above.
- Arguably **more realistic** (one browser visiting multiple engines) — though
  this is a bonus, not the main driver.

**The constraint that does NOT disappear:** the rate limit is
**per-(engine × exit-IP)** regardless of which context sends the request. So a
pool needs **shared per-engine pacing + cooldown across the whole pool** (e.g.,
M contexts must not all hit Google at once) — reintroducing the cross-context
coordination/locking that today's single-writer-per-engine model avoids. It also
**dilutes each engine's warmth** across more contexts (each visits a given engine
less often). Manageable, but real.

**Trade summary:** simple, lock-free, engine-serialized (today) ↔ concurrent,
load-balanced, needs shared per-engine rate/cooldown state (pool).

## Recommendation / when to revisit

- Keep the **per-engine worker** model for now — it's simple and correct, and
  Layer 1 backpressure makes the concurrency limit graceful and honest.
- When on-demand search volume justifies real concurrency, build **Option C**:
  warm-session pool + shared cross-engine queue + shared per-engine pacer/cooldown
  (a generalization of fan-out). Don't scale a single engine by adding contexts on
  one IP (Option A) — add IPs instead.
