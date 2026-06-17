# Why DuckDuckGo is not a supported engine

**Decision (2026-06-17):** DuckDuckGo is **not** supported as a search source.
After implementing it and probing every access surface, we concluded it
hard-blocks automated scraping in a way we can't reliably work around, so it was
removed from `scraper/sources/` and the engine config rather than shipped as a
broken/disabled engine.

This doc records the problem and the observations behind that decision, so the
question doesn't get re-litigated from scratch.

## What we wanted

A `duckduckgo` engine alongside `google`, `bing`, `yahoo`, and `brave` —
scraping DDG's **news** vertical (not web search) and parsing the results into
`NewsEntry` objects, same contract as the other sources.

## What we found (probed from a datacenter IP, 2026-06-17)

DuckDuckGo aggressively fingerprints automated access. Every surface that would
yield **news** is blocked, and the surfaces that respond don't return news:

| Surface | Result |
| ------- | ------ |
| `duckduckgo.com/` (homepage) | 200 — reachable |
| `duckduckgo.com/?q=…&iar=news&ia=news` (news vertical) | 200 but **redirects to a static block page**, `duckduckgo.com/static-pages/418.html` ("I'm a teapot"). No results. |
| `news.js` JSON API (what DDG's own frontend calls for news) | Unusable — it requires a `vqd` token, and the only page that carries the token is the news SERP, which is blocked. |
| `lite.duckduckgo.com/lite/?q=…` | Returns results **once**, then `403` under light load. Also: **web** results, not a news vertical. |
| `html.duckduckgo.com/html/?q=…` | Same — 200 once, then `403` / "If this persists…" error. **Web** results, not news. |

So:

- DDG **news** is unreachable from a normal server/datacenter environment.
- The only surfaces that ever respond (`lite` / `html`) return **web search
  results, not news**, and they rate-limit/`403` after a couple of requests.

## Why we didn't pivot to the reachable endpoints

Pivoting the engine to `lite`/`html` would mean shipping a "news" source that
actually returns general **web** results and falls over (`403`) under minimal
load. That's worse than not having the engine: it mislabels web results as news
and would mostly fail. The other four engines genuinely serve news verticals and
were live-validated; DDG can't meet that bar here.

## What would change the decision

DDG might be scrapable from a different environment — a **residential IP** and/or
a **non-headless** browser — where the `vqd` handshake and the news SERP succeed
instead of redirecting to the `418` page. If that's ever needed, the original
implementation approach was: target the news SERP markup
(`article[data-testid="result"]` with a `result-title-a` link), or obtain a
`vqd` token and call `news.js` for JSON results. Re-introduce it as a
`SearchSource` only once it can be live-validated there.

## Footprint of the removal

- Deleted `scraper/sources/duckduckgo.py` and unregistered it from
  `scraper/sources/__init__.py` (`get_source("duckduckgo")` now raises).
- Dropped it from `config/scraper.yml(.example)` and the docs' engine lists.
- Removed its parser/`detect_block` tests.

Block-signal detection for the **supported** engines (Bing/Yahoo/Brave) is
tracked separately in [BLOCK_SIGNAL_CHARACTERIZATION.md](BLOCK_SIGNAL_CHARACTERIZATION.md).
