# Search-Engine Block Signal Characterization (Plan)

> Status: **run 2026-06-17** at the scraper's sequential ~1 req/s, then a
> **concurrency follow-up 2026-06-18** (async httpx, 60–116 req/s) to trip
> Bing/Yahoo — results in [BLOCK_SIGNAL_FINDINGS.md](BLOCK_SIGNAL_FINDINGS.md).
> Brave's and Google's signals are grounded. The concurrency run settled the
> Bing/Yahoo question: **Bing never hard-blocks** (silent per-IP throughput
> throttle, ~50k requests served as HTTP 200), and **Yahoo blocks** after ~250
> rapid requests with a persistent empty **HTTP 500** (no parseable challenge
> page) — so neither needs a `detect_block` body signal. This file is the plan
> /background, kept self-contained for re-runs.

## Goal

Learn how Bing, Yahoo, and Brave respond when they bot-/DDoS-block a scraper,
and encode those signals into each source's `detect_block`, the way Google's
`/sorry/` redirect already is.

(DuckDuckGo is **not** a supported engine — it hard-blocks scraping; see
[DUCKDUCKGO_UNSUPPORTED.md](DUCKDUCKGO_UNSUPPORTED.md). The `static-pages/418`
redirect documented there is a useful reference example of a soft-block
signal.)

## Background: how block detection works today

The scraper is engine-pluggable. Each engine implements `SearchSource` in
`scraper/sources/` (`google.py`, `bing.py`, `yahoo.py`, `brave.py`). The generic
runner `scraper/scraper.py` navigates to the results URL, then asks the source
two things relevant here:

- `detect_block(final_url, html) -> str | None` — return a reason string if the
  response is a block/CAPTCHA page rather than results.

There are two **generic** safety nets in the runner that catch blocks
regardless of `detect_block`:

1. **HTTP status handling** — responses with status in
   `anti_detection.http_error_handling.monitored_codes` (default `429, 403,
   503`) are logged as failures.
2. **Parse-0 / selector-rot health signal** — a run of successful (HTTP 200)
   scrapes that parse **0 items** across the board flips server-side health to
   `parsing` (see `api/v1/status.py`, `compute_health`).

The gap these miss is a **soft block**: an HTTP `200` whose *body* is a
block/challenge/redirect page. Those need a per-engine (or generic-redirect)
`detect_block`. This is exactly why `detect_block` exists.

## Current per-engine state (as of 2026-06-18)

| Engine     | `detect_block` today                                                                                                                | Grounded in a real observation?                            |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| **Google** | `/sorry/` redirect (definitive) + captcha keywords (`captcha`, `unusual traffic`, from `anti_detection.captcha_detection.keywords`) | ✅ Yes (CAPTCHA work 2026-06-11/12; reconfirmed 2026-06-17) |
| **Brave**  | captcha-interstitial body markers (`decided to schedule a captcha`, `page:"/captcha"`)                                              | ✅ Yes (blocked ~250 reqs in; 2026-06-17)                   |
| **Bing**   | `return None` (stub)                                                                                                                | ✅ Yes — **never hard-blocks**; silent throttle (2026-06-18, ~50k reqs) |
| **Yahoo**  | `return None` (stub)                                                                                                                | ✅ Yes — blocks via empty **HTTP 500**, no body (2026-06-18, ~250 reqs) |

For both 429-style blockers (Google, Brave) the HTTP `429` net fires first; the
per-engine `detect_block` is the backup for a `200`-served challenge. Bing and
Yahoo correctly stay `None`, now for grounded reasons rather than absence of
data (2026-06-18 concurrency run):

- **Bing** never presents a challenge: even at ~75 req/s sustained for 10 min
  (~45k requests in one run) every response was HTTP 200 with real results. Its
  only defence is silently slow-rolling the connection to a ~60–76 req/s per-IP
  ceiling. There is no block page to detect — `detect_block` stays `None`.
- **Yahoo** *does* block, but not with a parseable page: after ~250 rapid
  requests it serves an **empty (0-byte) HTTP 500** from its `Server: ATS` edge
  with `Connection: close`, persisting as an IP cooldown. A real browser nav
  then fails with `net::ERR_CONNECTION_CLOSED` — caught by the runner's outer
  exception handler as a failed scrape — *before* any body exists for
  `detect_block` to inspect. So `detect_block` stays `None`; the honest-failure
  path is the navigation-error + parse-0 nets, not a body signal. (Optional
  hardening: add `500` to `monitored_codes` so a non-browser/200-path 500 is
  flagged too — see findings.)

For a worked example of a soft-block signal, see DuckDuckGo's `static-pages/418`
redirect in [DUCKDUCKGO_UNSUPPORTED.md](DUCKDUCKGO_UNSUPPORTED.md) (DDG itself is
not a supported engine).

## The plan

Run all of this **in an isolated environment on a different IP** that we are
willing to get temporarily blocked. Do not point it at production or a personal
IP.

1. **Trip the block.** For each of Bing, Yahoo, and Brave, send flooding
   requests at the smallest feasible interval (reuse the real `build_url` per
   engine; vary topics) until the engine DDoS-/bot-blocks the client.
2. **Capture the signal.** When blocked, record the full response:
   - final URL (after redirects) and HTTP status,
   - any redirect target / path (the Google analogue is `/sorry/`),
   - a snippet of the body (title, distinctive text, challenge markers),
   - response headers worth keying on (e.g. `Retry-After`, challenge cookies).
   Save raw HTML samples for the regression fixtures.
3. **Encode it.** Wire each engine's signal into its `detect_block`
   (`scraper/sources/{bing,yahoo,brave}.py`), analogous to Google's `/sorry/`.
   Add a parser/`detect_block` test with the captured fixture.
4. **Consider a generic heuristic** in the runner/base: flag a block when the
   final URL navigated *off the engine's results page* (host/path no longer
   matches the expected results URL). This would catch Google `/sorry/` and
   most redirect-style blocks without per-engine guesses.
5. **Self-documenting fallback:** log the final URL + a body snippet on
   parse-0 scrapes so that, in normal operation, engines reveal their block
   pages over time without a deliberate flood.

## Safety / ethics notes

- Isolated, disposable, different-IP environment only.
- This is characterizing block behavior of engines **we already scrape**, to
  make our own scraper fail honestly (better health signals) — not to evade
  detection or amplify load in normal operation.
- Expect the test IP to be rate-limited/blocked for a while; don't reuse it for
  real scraping immediately after.

## Pointers

- Sources: `scraper/sources/*.py` (`detect_block`, `build_url`).
- Runner: `scraper/scraper.py` (`_scrape_one_page` — where `detect_block` and
  HTTP-status handling are invoked).
- Health: `api/v1/status.py` (`compute_health`, parse-0 → `parsing`).
- Config: `anti_detection.captcha_detection.keywords`,
  `anti_detection.http_error_handling.monitored_codes`.
- Engine docs: `docs/SCRAPING_BEHAVIOR.md` (Search Engines section).
