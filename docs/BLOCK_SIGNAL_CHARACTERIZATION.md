# Search-Engine Block Signal Characterization (Plan)

> Status: **planned, not started.** To be run in a dedicated, isolated,
> different-IP environment — **not** production or a personal IP. This file is
> self-contained so it can be picked up without the original chat context.

## Goal

Learn how Bing, Yahoo, and Brave respond when they bot-/DDoS-block a scraper,
and encode those signals into each source's `detect_block`, the way Google's
`/sorry/` redirect already is. Also fix DuckDuckGo's block detection (it
currently misses its real block page).

## Background: how block detection works today

The scraper is engine-pluggable. Each engine implements `SearchSource` in
`scraper/sources/` (`google.py`, `bing.py`, `yahoo.py`, `brave.py`,
`duckduckgo.py`). The generic runner `scraper/scraper.py` navigates to the
results URL, then asks the source two things relevant here:

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

## Current per-engine state (as of 2026-06-17)

| Engine | `detect_block` today | Grounded in a real observation? |
| ------ | -------------------- | ------------------------------- |
| **Google** | `/sorry/` redirect (definitive) + captcha keywords (`captcha`, `unusual traffic`, from `anti_detection.captcha_detection.keywords`) | ✅ Yes (CAPTCHA work 2026-06-11/12) |
| **Bing** | `return None` (stub) | ❌ No — never observed a block |
| **Yahoo** | `return None` (stub) | ❌ No |
| **Brave** | `return None` (stub) | ❌ No |
| **DuckDuckGo** | matches body text `"anomaly"` / `"if this error persists"` | ⚠️ Guess — and it **misses** the real block (see below) |

Bing/Yahoo/Brave were deliberately left as `None`: we'd only ever seen their
*success* pages, and hardcoding a guessed pattern risks false-positiving on real
results (the same trap Google's keyword matching warns about).

### DuckDuckGo finding (observed 2026-06-17)

Requesting DDG's news vertical
(`https://duckduckgo.com/?q=...&iar=news&ia=news`) from a headless/datacenter
browser returns HTTP `200` but **redirects to a block page**:

```
final URL: https://duckduckgo.com/static-pages/418.html?bno=...&is_tor=0&...
page title: DuckDuckGo - Protection. Privacy. Peace of mind.
items found: 0
```

DDG's news results are JS-rendered behind a `vqd` token handshake and it
fingerprints automated access. The current `detect_block` does **not** catch
this (no `static-pages/418` check), so an enabled DDG would log as
"success, 0 items" and falsely trip the parse-0 selector-rot signal. DDG ships
disabled by default for this reason.

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
4. **Fix DuckDuckGo** while here: detect the `duckduckgo.com/static-pages/418`
   redirect (and an `iar=news` request landing on a non-news URL).
5. **Consider a generic heuristic** in the runner/base: flag a block when the
   final URL navigated *off the engine's results page* (host/path no longer
   matches the expected results URL). This would catch Google `/sorry/`, DDG
   `418`, and most redirect-style blocks without per-engine guesses.
6. **Self-documenting fallback:** log the final URL + a body snippet on
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
