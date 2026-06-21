# The per-engine task scheduler

> How the scraper schedules every "web page access" an engine makes. Code:
> `scraper/tasks.py` (the model), `scraper/worker.py` (the per-engine scheduler
> loop), `scraper/cooldown.py`, `scraper/saturation.py`. Companion to
> [SCRAPING_BEHAVIOR.md](SCRAPING_BEHAVIOR.md) and
> [WEB_SEARCH_WARMUP.md](WEB_SEARCH_WARMUP.md).

## The model: one engine, one scheduler, a queue of tasks

Every browser request an engine makes is a **Task**. Each engine runs in its own
worker thread (`run_engine_worker`) with its own warm Playwright context, and
that worker is the engine's **scheduler**: it owns the engine's
`EngineTaskQueue`, pace floor (`_Pacer`), adaptive cooldown
(`EngineCooldownTracker`), metrics window, and statistics. Workers never share a
context — one identity per engine on the shared exit IP — so per-engine state
needs no locking (single writer).

Tasks come from **generators** and differ in two ways only: their **priority**
and **what happens to the result**. The scheduler doesn't branch on the kind
beyond dispatching execution — it runs the task and calls the task's own
delivery hook.

| Task | Generator | Priority | Result |
|---|---|---|---|
| `WebSearchTask` | `WebSearchSource` (the cross-process / in-process job queue) | **highest** — preempts news | handed straight back to the waiting caller; **not persisted** |
| `NewsScrapeTask` | `NewsTaskGenerator` (a timer heap keyed by each topic's next-eligible time) | scheduled, ordered by due time | buffered into the metrics window → Postgres |
| `KeepAliveTask` | `KeepAliveGenerator` (idle warm-up) | **lowest** — only when nothing else is ready | none (a benign request to keep the session warm) |

A task's `priority` is `(class, when)` — lowest first: one-off (class 0) before
any scheduled news (class 1, ordered by next-eligible time) before idle
keep-alive (class 2).

## A subscription is a task generator

A tracked topic isn't a task — it's a *generator* of tasks. `NewsTaskGenerator`
emits a `NewsScrapeTask` for a topic each time its next-eligible time comes due,
and on completion **self-reschedules** it one `scrape_interval` (plus jitter)
out. The timer heap **coalesces**: at most one pending entry per topic, so a
slow or benched engine can't pile up duplicate scrapes for the same topic. This
is what keeps the queue (and the backlog health signal below) bounded.

## The queue is a *facade*, not a literal heap

`EngineTaskQueue.pop_ready()` realises the priority order by polling the sources
in order — a claimed one-off, else a due news task, else an idle warm-up —
rather than holding everything in one in-memory heap. The reason: one-off web
searches arrive **cross-process** (a row in the `web_search_jobs` table, claimed
by `DbWebSearchQueue`), so they can't sit in an in-memory heap alongside the
time-keyed news tasks. They're *claimed into* the head on demand. The single
queue is the mental model; polling order is the mechanism.

## Two throttles + a reactive backstop (unchanged by the refactor)

- **Per-topic interval** (the timer heap key): a topic is never re-scraped by an
  engine sooner than `scrape_interval` (+ jitter) — the freshness cadence,
  decoupled from topic count.
- **Per-request pace floor** (`_Pacer`): a minimum gap between *any* two requests
  the engine makes. **Priority orders work; the pacer rate-limits it** — a burst
  of one-offs preempts news but still can't exceed the safe per-engine rate.
- **Adaptive cooldown** (`EngineCooldownTracker`): the reactive backstop. A
  block/429 benches the engine for an exponential, capped window, after which a
  single **probe** resumes (clean) or deepens (block) it.

## Cooldown × tasks: what a benched engine does

When `cooldown.decide(engine) == "skip"` (benched), the scheduler does **not**
run scheduled or idle work — but it does **not** silently swallow one-offs
either:

- **One-off web searches → fail fast.** Queued `WebSearchTask`s are rejected
  immediately (`EngineTaskQueue.reject_pending_oneoffs` → each job's `cooling()`
  hand-back: an `EngineCoolingError` to an in-process caller, or a `"cooling"`
  outcome on the cross-process job row). The caller gets a prompt, distinct
  answer instead of waiting out the cooldown window. *(Engine selection / falling
  back to another engine is an upper-layer API concern, deliberately out of scope
  here — each engine only serves itself.)*
- **News scrapes → skip and stay scheduled.** They're time-keyed; they just wait.
- **The single probe is preserved.** When the window expires, whatever surfaces
  first while `decide() == "probe"` *is* the probe (a due news topic, or a
  one-off — a user-facing search naturally doubles as the probe).

## Backlog as a (lagging) health signal

Each worker publishes its queue's `SchedulerHealth` — `overdue_count`,
`max_lateness_seconds`, `pending_oneoffs` — to `SharedEngineState` once per
window. "Overdue" counts active topics whose next-eligible time has passed but
that haven't been popped yet (the topic currently being scraped was already
popped, so it isn't counted): the real scrape backlog.

The supervisor warns (`evaluate_backlog` / `log_backlog`) when an engine's oldest
overdue topic is later than **3× its `scrape_interval`** — it's cycling topics
materially slower than its cadence (pace-floor or cooldown pressure, or simply
too many topics for one engine on this IP). This is a **lagging** complement to
the direct signals — `detect_block`/429 → cooldown and the parse-0 selector-rot
health — not a replacement for them; backlog only grows *after* an engine is
already slow.

It's surfaced on **`/monitor`** too: each worker publishes its backlog to the
`engine_cooldowns` state row (`db.upsert_engine_backlog`), the metrics API returns
`backlog_overdue` / `backlog_lateness_seconds` per engine, and the engine table
shows a `backlog` column plus a **`behind`** health badge when an engine is past
the same 3× threshold (`api/v1/metrics._build_engine`).

## Deliberately out of scope (future work)

- **Cross-engine dispatch / engine selection** for one-offs (primary+fallback,
  combine results, route around a benched engine) — an API-layer decision; the
  scheduler implements per-engine serving only.
- **Postgres → WebSocket/pubsub fan-out** of new news entries — downstream of the
  scheduler, which stops at "results produced / entries written."
- **A result cache** for one-offs (today they're purely transient).
