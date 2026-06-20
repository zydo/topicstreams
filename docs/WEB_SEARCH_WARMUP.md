# Web-Search Session Warm-up (keep-alive heartbeat)

> Design rationale for the per-engine **keep-alive heartbeat** that the planned
> on-demand web-search feature depends on. Empirical basis gathered 2026-06-20;
> companion to [ANTI_BOT_DETECTION.md](ANTI_BOT_DETECTION.md) and
> [BLOCK_SIGNAL_FINDINGS.md](BLOCK_SIGNAL_FINDINGS.md).

## Context

On-demand web search reuses **each engine's existing warm per-engine Chromium
session** (the one its news worker already drives) rather than spinning up a
second set of sessions — one request stream per engine per exit IP, one shared
pacing/cooldown budget. See [SCRAPING_BEHAVIOR.md](SCRAPING_BEHAVIOR.md) for the
per-engine worker model.

That reuse has a failure mode: a session that makes **no recent successful
requests** goes *cold*. This happens when there are **no tracked topics**, when
topics are sparse (long idle gaps between news scrapes), right after a **fresh
deploy**, or after a long **cooldown**. A web search arriving on a cold session
is the worst case for getting CAPTCHA'd.

## What we observed (2026-06-20, "us iran")

In-container spikes, same exit IP, same Chromium, same fingerprint — only the
profile/timing varied:

- **Cold profile + the default web endpoint** (`/search?q=`, no `tbm=nws`)
  redirected to `/sorry/` (CAPTCHA) in a clean same-moment comparison, while a
  **warm profile** (a clone of the live `google` *news* profile) returned the
  full results page. → warmth helped.
- **But ~40 min later a fresh cold profile passed web search with no warming.**
  Nothing changed but elapsed time and recent request history.
- The news vertical (`tbm=nws`) passes **cold** — production news workers
  cold-start from near-empty profiles and stay healthy. The **default web
  endpoint is stricter** and CAPTCHA-prone when the session is cold.

### The model this implies

Cold-vs-warm is **not deterministic**. CAPTCHA risk on the web endpoint is gated
by a combination of:

1. **Session trust** — the profile's accumulated Google cookies (`NID`, etc.),
   built up by successful requests over time. Thin/cold profiles have little.
2. **Exit-IP reputation** — a suspicion level for the shared IP that *drifts*:
   a burst of failed/`/sorry` requests raises it (cold then fails); a calm period
   of successful traffic lowers it (cold can pass).

Warmth **lowers the CAPTCHA probability**; it does not flip a hard switch. The
worst case — cold *and* idle session hit by a web search while IP suspicion
happens to be elevated — is exactly what the warm-up removes.

## Why the warm-up is necessary

The keep-alive eliminates the "cold/idle" half of that risk. By issuing periodic
**benign, successful requests**, it:

- keeps the profile's **trust cookies fresh** (raising session trust), and
- keeps a **steady stream of successful traffic** so the session never goes fully
  cold and the IP's reputation stays in good standing,

both of which lower the chance a real web search lands on a cold session and gets
CAPTCHA'd. Without it, a deployment with zero tracked topics would have sessions
that never make a request — guaranteed cold — and web search would CAPTCHA far
more often.

**Why the heartbeat uses the NEWS vertical (not web):** news passes cold but web
does not, so a cold session can only bootstrap via news. A web-vertical heartbeat
on a cold session would itself CAPTCHA — chicken-and-egg. News warms the session;
once warm, web search works.

## Design

- **One news-vertical idle keep-alive per engine session.**
- **Random benign query** from a small hardcoded set (e.g. `"weather tomorrow"`,
  `"local news"`, `"breaking news"`) — generic, always-has-results, nothing
  topical/sensitive that would pollute intent signals. Rotate so restarts don't
  repeat the same query.
- **At startup, then every ~10 min with random jitter** (so the cadence isn't a
  metronome). The exact interval is a tunable guess — we don't know the precise
  "how warm is warm enough," and over-frequent heartbeats waste the shared pacing
  budget.
- **Idle-only**: any real news scrape or web search resets the timer; the
  heartbeat fires only when the session has otherwise been idle past the
  interval. In the common case (topics present) it rarely fires — a near-zero
  overhead safety net, not a constant tax.
- Rides the engine's existing **pacing floor + cooldown**, and its requests are
  counted in metrics (or labelled) so they aren't mistaken for organic load.

## Caveat — it's a risk reducer, not a guarantee

Because CAPTCHA risk is probabilistic (IP reputation can be elevated for reasons
outside our control), web search **must still tolerate an occasional CAPTCHA**:
on a block, fall back to a **healthy engine** (we run 4 and already track
per-engine cooldown) and/or retry. The keep-alive lowers the rate; the fallback
handles the residue.

**Do not** aggressively probe cold-vs-warm CAPTCHA rates from the production exit
IP: failed probes spend the IP's reputation, which is **shared with the live news
scraper**, and can degrade real scraping. Characterize on a separate IP / offline
harness if hard numbers are ever needed.
