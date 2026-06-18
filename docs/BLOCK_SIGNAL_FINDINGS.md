# Search-Engine Block Signal Findings

> Companion to [BLOCK_SIGNAL_CHARACTERIZATION.md](BLOCK_SIGNAL_CHARACTERIZATION.md).
> Records what each engine actually did when we deliberately tripped its
> bot/rate-limit block. Run started 2026-06-17.

## Method

- Single topic (`us iran`), single engine per run, `scrape_interval: 1`,
  `max_pages: 1` (see `config/scraper.yml` during the run; restore from
  `config/scraper.yml.charbak`).
- Temporary diagnostic added to `scraper/scraper.py` (`_capture_diagnostic`):
  on HTTP-error, on `detect_block`, and on parse-0 (HTTP 200 but 0 entries) it
  logs final URL + title + body snippet and dumps raw HTML to `/app/dumps`
  (host: `./scraper_dumps/`).
- Watched `docker logs -f topicstreams-scraper` and the API feed
  (`/api/v1/news/us%20iran`).
- Environment: GCP VM `hatchway`, the project's own Docker stack. Authorized by
  the project owner for defensive characterization of engines we already scrape.

### State to restore afterwards

- Topics before run: `anthropic`, `spacex`, `us iran` (all active).
- `config/scraper.yml` before run: `scrape_interval: 60`, no `engines` key
  (defaulted to `["google"]`), `engine_strategy` default `fallback`.
- Temporary changes to revert: `scraper/scraper.py` diagnostic, docker-compose
  `./scraper_dumps` mount, `config/scraper.yml`.

---

## Yahoo

**No block observed.** Flooded Yahoo News (`https://news.search.yahoo.com/...`,
topic `us iran`, past-hour) at ~1 request/second for ~22 minutes
(≈900+ requests, 400+ consecutive logged scrapes in the captured window).

- Every request returned HTTP 200 with **20 parsed items**; `Found 0` count = 0,
  `HTTP ERROR` count = 0, no redirect, no `detect_block` hit, no CAPTCHA.
- Conclusion: Yahoo News tolerates sustained ~1 req/s from a single
  datacenter IP without rate-limiting or challenging. Tripping its block would
  require substantially higher volume or concurrency than the sequential
  scraper produces (~1/s is page-load-bound).
- Signal for `detect_block`: **none captured** at this rate. Leaving Yahoo's
  `detect_block` as `None` remains the right call until a real block is seen.

> Follow-up option if a real Yahoo signal is needed: drive concurrent browser
> contexts (parallel requests) to push well past ~1/s, or run for hours.

**Update (2026-06-18 concurrency run): Yahoo DOES block — see the
[concurrency follow-up](#concurrency-follow-up-2026-06-18) below.** At ~100
req/s it serves real results for ~250 requests, then flips to a persistent empty
**HTTP 500** (`Server: ATS`, `Connection: close`). It is not a parseable
challenge page, so `detect_block` still stays `None`.

## Bing

**No block observed.** Flooded Bing News (`https://www.bing.com/news/search...`,
topic `us iran`, past-hour) at ~1 request/second for ~10 minutes (~440 requests).

- Every request returned HTTP 200 with **10 parsed items** (Bing's page size);
  `Found 0` = 0, `HTTP ERROR` = 0, no redirect, no `detect_block` hit.
- Conclusion: like Yahoo, Bing News tolerates sustained ~1 req/s from a single
  datacenter IP without rate-limiting or challenging.
- Signal for `detect_block`: **none captured** at this rate. Leaving Bing's
  `detect_block` as `None` remains correct until a real block is seen.

**Update (2026-06-18 concurrency run): Bing NEVER hard-blocks — see the
[concurrency follow-up](#concurrency-follow-up-2026-06-18) below.** ~50k requests
at up to ~76 req/s were all HTTP 200 with real results; Bing only silently
throttles per-IP throughput. There is no block page, so `detect_block` stays
`None`.

## Brave

**BLOCKED — real signal captured.** Flooded Brave Search news
(`https://search.brave.com/news?q=us+iran&tf=pd`, topic `us iran`) at ~1
request/second. ~250 successful requests (50 items each) over ~8 minutes, then
a clean transition to a hard block:

```
09:47:14  Found 50 potential news items   <- last good response
09:47:15  HTTP ERROR 429 - Rate limiting detected - Too many requests   <- block
```

- **HTTP status: `429 Too Many Requests`** — the primary, definitive signal.
  Already caught by the generic monitored-codes handler (`429` is in
  `anti_detection.http_error_handling.monitored_codes`), so the scraper already
  fails honestly on a Brave block today.
- **No redirect** — `final_url == intended_url`
  (`https://search.brave.com/news?q=us+iran&tf=pd`); the block is served in
  place, not via a `/sorry/`-style redirect.
- **Body = CAPTCHA interstitial** (HTTP 200 markup served with the 429). Title
  is the normal `Brave Search`, but the body contains:
  - `…flagged as being suspicious and Brave Search decided to schedule a
    captcha for you.`
  - JS state `page:"/captcha"` (normal results pages carry `page:"/search"`).
- Raw fixtures saved: `scraper_dumps/brave_*_429.html` (~72 KB each).

**Proposed `detect_block` for Brave** (`scraper/sources/brave.py`): the 429 is
already handled generically, but for robustness against a 200-served captcha,
key on the body marker:

```python
def detect_block(self, final_url: str, html: str) -> str | None:
    if "decided to schedule a captcha" in html or '"/captcha"' in html:
        return "Brave captcha interstitial"
    return None
```

(Grounded in the 2026-06-17 observation above — distinct from real results,
which never contain the captcha copy or `page:"/captcha"`.)

## Google

**BLOCKED — fastest of all, signal matches the known one.** Flooded Google
Search News tab (`https://www.google.com/search?tbm=nws&...&q=us+iran`, past
hour) at ~1 request/second. Blocked after only **~54 successful requests
(~80 seconds)** — by far the most aggressive engine.

```
09:50:20  Found 10 potential news items   <- last good response
09:50:20  HTTP ERROR 429 ... URL: https://www.google.com/sorry/index?continue=...   <- block
```

- **HTTP status: `429 Too Many Requests`**.
- **Redirect: `redirected=YES`** → `https://www.google.com/sorry/index?continue=<the search URL>&q=<token>`
  — the classic `/sorry/` block page.
- **Body markers**: `Our systems have detected unusual traffic from your
  computer network` + a `captcha-form` — exactly the configured
  `anti_detection.captcha_detection.keywords`.
- Raw fixtures saved: `scraper_dumps/google_*_429.html` (~4 KB each).

**Interaction with existing code:** Google's `/sorry/` redirect is served
*with* HTTP 429, so the generic monitored-codes handler returns first and
`GoogleSource.detect_block` never runs (`blocked the request` count = 0 during
the whole run). The `/sorry/` + keyword logic in `detect_block` is therefore a
**backup** for the case where Google serves the challenge with a 200; the 429
status net is what actually fires today. Both are correct and grounded — no
change needed.

---

## Concurrency follow-up (2026-06-18)

The 2026-06-17 run left Bing/Yahoo open because the production scraper is
**sequential and page-load-bound** (~1 req/s) and neither blocked at that rate.
This run pushed concurrency to settle it. Same environment (hatchway GCP VM,
public IP `35.197.42.202`, the project's own Docker stack; Bing/Yahoo are not
production engines here, so blocking them has no production impact).

### Method — what worked to generate load

- **Pure concurrent Playwright (tried first): could not reach blocking volume.**
  A single Chromium collapses past ~20–40 parallel pages (browser crash →
  cascade of ~21k `TargetClosedError`), and each nav is slow-rolled to ~7–10s,
  so effective throughput stayed **<1 req/s** — no better than sequential.
  Heavy and a dead end.
- **Async httpx (used): reaches real results at high rate.** A throwaway async
  flooder that reuses each engine's real `build_url` with a version-matched
  browser header set. A single httpx GET returns the *real* results page for
  both engines (Bing ~350 KB / 10 items; Yahoo ~154 KB / 20 items), so this is a
  faithful volume test, not a JS-gate artifact. Hundreds of concurrent
  lightweight requests gave **60–116 req/s**. Practical notes: install `brotli`
  (else `Accept-Encoding: br` bodies decode to garbage → 0 items); the scraper
  container is capped at **1.5 GB**, so the flooder *streams* and reads only a
  64 KB body prefix — downloading full 350 KB bodies × 200 concurrent OOM-killed
  it. httpx's TLS fingerprint differs from Chromium, so the block was
  **re-confirmed with a real Playwright nav** (see Yahoo below).

### Bing — NO block, silent throughput throttle

Flooded Bing News at rising concurrency (50 → 500 in-flight). **~50k requests
across runs, every one HTTP 200 with real results — zero 429/403/503, zero
redirects, zero challenge bodies.** The definitive run: **45,316 requests in
602 s** at up to **~76 req/s**, all 200.

- Aggregate throughput plateaus at **~60–76 req/s regardless of concurrency**
  (100, 200, 300, 400, 500 all land there) — Bing slow-rolls each connection so
  a single IP can't exceed that ceiling. This is a **silent per-IP throttle**,
  not a rate-limit *block*: it never escalates to an error status or a CAPTCHA.
- `detect_block` action: **keep `None`.** There is no block page to key on, and
  no status to catch. The relevant defence is simply to stay under the throttle
  ceiling (the scraper's ~1 req/s is two orders of magnitude below it).

### Yahoo — BLOCKS via empty HTTP 500 (real signal captured)

Flooded Yahoo News at ~100–116 req/s. Yahoo served real results for exactly
**~251 requests**, then flipped hard to a **persistent block**:

```
... 200 (×251, real results) ...
500  (×9,913 consecutive, empty body)   <- block, ~13 s in at ~100 req/s
```

- **HTTP 500, empty body (`Content-Length: 0`)**, `Server: ATS` (Yahoo's Apache
  Traffic Server edge), `Cache-Control: private`, `Connection: close`. No
  redirect (`final_url` unchanged), no captcha copy — the body is literally
  empty, so there is nothing for `detect_block` to match.
- **Persistent / IP-scoped:** still 500 on low-rate sequential probes ~15 s
  after the flood stopped — a cooldown block of the IP, not momentary overload.
  (Contrast 2026-06-17: ~900 sequential requests never tripped it.)
- **What the real (Playwright) scraper sees:** `page.goto` raises
  `net::ERR_CONNECTION_CLOSED` (the empty 500 + `Connection: close` looks like a
  dropped connection to Chromium). The runner's outer `except` catches it →
  `success=False` with that error message. So the production scraper **already
  fails honestly** on a Yahoo block; it just isn't labelled a "block".
- Raw fixture saved: `scraper_dumps/yahoo_500_sample.html` (0 bytes — the empty
  body is itself the signal) and `yahoo_httpx_block_*` dumps.
- `detect_block` action: **keep `None`** — there is no body to inspect (goto
  raises before content is read). Optional hardening if we want a Yahoo 500 on a
  *non-browser* path flagged explicitly: add `500` to
  `anti_detection.http_error_handling.monitored_codes`. Today a 500 served *with
  a body* on the 200-path would fall through to parse-0; the empty-500 + browser
  case is already caught as a navigation error.

### Throughput observed (concurrency run)

| Engine | Tool  | Peak req/s | Requests sent      | Block? | Block manifestation                          |
| ------ | ----- | ---------- | ------------------ | ------ | -------------------------------------------- |
| Bing   | httpx | ~76        | ~50k (45,316 max run) | No  | none — slow-roll throttle, all HTTP 200      |
| Yahoo  | httpx | ~116       | ~10k (tripped at ~251) | Yes | empty HTTP 500 (`Server: ATS`), then cooldown |

---

## Summary

At the scraper's sequential ~1 req/s, two of four engines block (Google, Brave)
and two do not (Bing, Yahoo). The 2026-06-18 concurrency run resolved the latter
two: **Bing never hard-blocks** (silent throttle) and **Yahoo blocks via an
empty HTTP 500** once pushed past ~250 rapid requests. Aggressiveness order:
**Google ≫ Brave ≫ Yahoo ≫ Bing (never)**.

| Engine | Requests to block | HTTP status | Redirect                    | Distinctive body markers                                   | `detect_block` action                                                   |
| ------ | ----------------- | ----------- | --------------------------- | ---------------------------------------------------------- | ----------------------------------------------------------------------- |
| Google | ~54 (~80 s)       | 429         | → `/sorry/index?continue=…` | "detected unusual traffic", captcha-form                   | already grounded (/sorry/ + keywords); 429 net fires first — keep as-is |
| Brave  | ~250 (~8 min)     | 429         | none (same URL)             | "decided to schedule a captcha for you", `page:"/captcha"` | add body-marker check (429 net already catches it)                      |
| Bing   | never (~50k @ 76/s) | 200       | none                        | n/a — full results throughout, even under flood            | keep `None` — silent throttle, no block page exists (2026-06-18)        |
| Yahoo  | ~251 (@ ~100/s)   | **500** (empty) | none                  | n/a — 0-byte body, `Server: ATS`, `Connection: close`      | keep `None` — empty body / browser sees `ERR_CONNECTION_CLOSED` (2026-06-18) |

## Throughput observed (peak queries/minute)

The scraper is **sequential** (one page load at a time), so the request rate is
page-load-bound, not set by `scrape_interval` — at `scrape_interval: 1` the
limiter is each request's navigation + 300 ms settle, giving roughly 1 req/s.
Peak = the busiest single 60 s window.

| Engine | Peak req/min | ~req/s | Source of number                                  | Cumulative reqs before block |
| ------ | ------------ | ------ | ------------------------------------------------- | ---------------------------- |
| Yahoo  | **57**       | ~0.95  | measured (capture log, minute 09:09)              | no block (~900+)             |
| Bing   | **~50**      | ~0.8   | derived (~1.0–1.3 s cycles; clean log lost)       | no block (~440)              |
| Google | **47**       | ~0.78  | measured (current container, minute 09:50)        | **~54 (~80 s)**              |
| Brave  | **~33**      | ~0.55  | derived (250 reqs over ~457 s; ~1.7–2.1 s cycles) | **~250 (~8 min)**            |

Notes:
- Brave/Bing peaks are estimates: Brave's and Bing's containers were
  force-recreated when switching engines, discarding their docker logs, and the
  `run_bing.log` capture accidentally retained yahoo lines. Yahoo and Google are
  measured directly.
- Brave's lower req/min is just because each Brave page returns 50 items (more
  parse/load time per cycle), not throttling — it was steady until the 429.
- These are modest rates (~0.5–1 req/s). Google rate-limits at this rate within
  ~80 s; Brave within ~8 min; Yahoo and Bing not at all in the windows tested.
  Tripping Yahoo/Bing would require concurrency to push well above ~60 req/min.

**Key takeaways**

1. The generic HTTP-`429` handler already catches the *real* blocks (Google,
   Brave). The biggest honest-failure win is keeping `429/403/503` in
   `monitored_codes` (it is).
2. A generic "navigated off the results host/path" heuristic (plan step 4)
   would independently catch Google's `/sorry/` redirect; Brave's in-place 429
   would not trip it (URL unchanged), so the per-engine body marker is still
   worth adding for Brave's 200-captcha edge case.
3. Bing and Yahoo did not block at ~1 req/s; the 2026-06-18 concurrency run
   resolved why their `detect_block` stubs should stay `None`: **Bing never
   hard-blocks** (silent per-IP throttle, ~50k requests all HTTP 200) and
   **Yahoo blocks with an empty HTTP 500** (no parseable challenge page; a real
   browser sees `net::ERR_CONNECTION_CLOSED`, already caught as a nav error).
   Neither exposes a body signal to key on. Optional: add `500` to
   `monitored_codes` to flag a Yahoo 500 on the non-browser/200 path.
4. The parse-0 / `_capture_diagnostic` self-documenting logging (plan step 5)
   worked: it captured Brave's captcha body and Google's `/sorry/` body
   automatically, with raw HTML dumped to `scraper_dumps/` for fixtures.
