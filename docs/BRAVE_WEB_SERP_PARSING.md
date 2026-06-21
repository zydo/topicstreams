# Brave Web-Search (WEB vertical) SERP parsing

> Research + rationale behind `BraveWebParser` (`scraper/sources/brave.py`).
> Empirical basis: live `search.brave.com/search?q=` pages captured from a clone
> of the warm `brave` profile (production exit IP) on 2026-06-21 for the keywords
> `us iran`, `apple`, `thomas jefferson`, `weather`, `bitcoin price`,
> `how to tie a tie`, `serendipity`. Companion to
> [GOOGLE_WEB_SERP_PARSING.md](GOOGLE_WEB_SERP_PARSING.md),
> [BING_WEB_SERP_PARSING.md](BING_WEB_SERP_PARSING.md),
> [YAHOO_WEB_SERP_PARSING.md](YAHOO_WEB_SERP_PARSING.md), and
> [WEB_SEARCH_WARMUP.md](WEB_SEARCH_WARMUP.md).

## How the page was captured — and why Brave is special here

Brave is the engine's **canary** (`canary_engines: [brave]`): it rate-limits
first, and its web endpoint is **stricter than its news endpoint**. When Brave
flags traffic it serves an in-place CAPTCHA interstitial (HTTP 429, body
`decided to schedule a captcha` / `page:"/captcha"`, no `#results`) — the same
signal `BraveSource.detect_block` already keys on (see
[BLOCK_SIGNAL_FINDINGS.md](BLOCK_SIGNAL_FINDINGS.md)).

The operational catch: the Brave **news** worker scrapes continuously at the
per-IP rate-limit threshold, so a web-search request issued *while news scraping
runs* lands on an already-throttled IP and gets CAPTCHA'd — **even on a warm
profile** (verified 2026-06-21: a clone of the live warm `brave` profile got the
interstitial on all 7 queries while the news worker was running). Profile warmth
does not overcome it; the rate-limit/IP-reputation axis dominates (the model in
[WEB_SEARCH_WARMUP.md](WEB_SEARCH_WARMUP.md)).

So to characterise the DOM we **stopped the news worker** (brought the stack
down) to let the exit-IP rate-limit recover, then fetched the raw
`search.brave.com/search?q=<query>` page from a **host-side clone of the warm
`brave` profile** (extracted from the `topicstreams_browser_profiles` volume) on
the now-unloaded production IP, scrolling to settle lazy sections, and saved the
HTML. Capturing this way — warm profile, clean IP, no competing news load — is
what returns the real SERP; under load it only returns the challenge page. (Even
so, Brave re-throttled after ~5 rapid queries, its documented per-IP window — the
last two keywords were captured after a short pause.)

> **Implication for the live feature:** on-demand web search against Brave will
> CAPTCHA far more readily than the other engines whenever Brave news is under
> load. The keep-alive/fallback design in `WEB_SEARCH_WARMUP.md` (fall back to a
> healthy engine on a block) is the backstop; Brave should not be the primary web
> engine while it carries heavy news load on the same IP.

Two findings shaped the parser before any selector work:

1. **Raw query only.** `SearchRequest` defaults to `sort=DATE, recency=HOUR`
   (inherited from the news pipeline). The web page is the *relevance* page a user
   sees, so `build_url` for WEB emits **only** `q=<query>` (plus `offset=` for
   pagination, Brave's 0-based page index) and ignores sort/recency.
2. **Direct URLs + `data-type` tagging.** Brave's web SERP is server-rendered and
   refreshingly clean: every result is a `div.snippet` carrying a stable
   **`data-type`** attribute (`web`, `cluster`, `ad`, …), so kinds never have to
   be told apart by guessing which block comes first. And every result href is the
   **direct destination URL** — no redirect wrapper to decode (unlike Bing's
   `/ck/a` or Yahoo's news `/RU=`).

## The SERP is keyword-dependent

Which components render varies by query intent. Observed presence (✓), with the
post-parse counts of what we keep:

| query            | organic | knowledge panel (`#infobox`) | "In the News" cluster |  ad   | weather widget | → kept (panel / organic / news) |
| ---------------- | ------: | :--------------------------: | :-------------------: | :---: | :------------: | ------------------------------: |
| us iran          |      19 |         ✓ Wikipedia          |           –           |   –   |       –        |                      1 / 19 / 0 |
| apple            |      14 |         ✓ Wikipedia          |      ✓ (9 cards)      |   ✓   |       –        |                      1 / 14 / 6 |
| thomas jefferson |      20 |         ✓ Wikipedia          |           –           |   –   |       –        |                      1 / 20 / 0 |
| weather          |      20 |         ✓ Wikipedia          |           ✓           |   –   |       ✓        |                      1 / 20 / 6 |
| bitcoin price    |      20 |         ✓ Wikipedia          |           ✓           |   –   |       –        |                      1 / 20 / 6 |
| how to tie a tie |      20 |              –               |           –           |   ✓   |       –        |                      0 / 19 / 0 |
| serendipity      |      19 |         ✓ Wikipedia          |           –           |   –   |       –        |                      1 / 19 / 0 |

So: organic results are the stable backbone on every query; most queries also get
a Wikipedia-sourced knowledge panel; hot/entity queries add an "In the News"
publisher carousel. (The news cluster is capped at `_MAX_NEWS`, hence ≤6 kept.)

## What we parse, and how

Each kept component becomes a uniform `WebResult` (`common/model.py`), ordered by
how directly it answers a lookup, deduped by destination URL.

| kind              | marker (selector)                                                              | title / source / snippet                                                                                                                                                                                                                   |
| ----------------- | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `knowledge_panel` | `section#infobox` (only when it carries a "Wikipedia" source link)             | title = the query; description = the entity sentence (text of the section ending in the "Wikipedia" attribution link, with that word stripped); source `Wikipedia`; URL the `en.wikipedia.org` link                                        |
| `organic`         | `div.snippet[data-type="web"]`                                                 | title `.title` (its `title` attr, else text), the **direct** `a.l1` URL, source = the brand in `.site-name-content` (its first child div), snippet `.generic-snippet .content` with the leading `span.t-secondary` "N ago -" date stripped |
| `top_story`       | `a.enrichment-card-item` in the `data-type="cluster"` ("In the News") carousel | headline `.line-clamp-2`, direct publisher URL, source = the card's `.enrichment-card-site` domain label                                                                                                                                   |
| `discussion`      | same cards, **host in the shared social/forum allow-list**                     | reddit/x/medium/… cards, capped harder                                                                                                                                                                                                     |

Implementation notes worth keeping:

- **Knowledge panel keys on the source link, not a hashed class.** Brave's
  `#infobox` is heavily Svelte-hashed (`svelte-1adoobh`, …) and fragile. We don't
  match those: we find the `a[href*="wikipedia.org"]` whose text is exactly
  `"Wikipedia"` (the source attribution, *not* the entity-title link that also
  points to Wikipedia), and take its parent section's text as the description.
  Robust to class churn; degrades to "no panel" if the attribution link is gone.
- **News cluster links to real publishers.** Unlike Yahoo's news carousel (all
  `yahoo.com/news` aggregator links), Brave's "In the News" cards link **directly
  to the publisher** (`macrumors.com`, `nbcnews.com`, …) and carry a headline — so
  they're worth keeping, like Bing's/Google's news packs. Capped at `_MAX_NEWS`
  (it's a 9+-card carousel), discussions within it capped harder
  (`_MAX_DISCUSSIONS`).
- **Stable selectors only.** We rely on the same semantic hooks the news parser
  already trusts (`div.snippet`, `data-type`, `.title`, `.site-name-content`,
  `.generic-snippet`) and Brave's `data-type`/`a.l1`/`enrichment-card-*` markers,
  never the `svelte-*` hashes.
- **Shared helpers.** The social/forum host list and `domain_of`/`is_discussion`
  live in `base.py`, shared with Google/Bing.

## What we deliberately ignore, and why

Same lens as the other engines: keep things with a **readable text payload** and a
**followable URL to a real source**; drop ads, promos, and pure-widget chrome.

- **Ads** (`div.snippet[data-type="ad"]`, `id="search-ad"`, carrying
  `data-headline-text`/`data-landing-page`) — commercial, not organic.
- **Weather widget** (`.rich-weather-content`) — a structured forecast, but on
  Svelte-hashed markup that (as on Google/Bing/Yahoo) didn't yield a clean value;
  deferred rather than ship a brittle selector. weather.com / wunderground still
  come through as organic results.

## Known gaps / not yet parsed

- **Weather / finance / dictionary widgets** — the `weather` query renders a
  `.rich-weather-content` forecast and `serendipity` a definition-style infobox,
  but their markup is noisy; deferred. The underlying sites still appear as
  organic results, and the `serendipity` definition is also captured via its
  `#infobox` knowledge panel.
- **Standalone video / discussion clusters** — none of the seven queries rendered
  a dedicated `data-type` video or forum cluster; discussions only appeared as
  social/forum cards *inside* the "In the News" carousel (handled). If Brave ships
  a distinct discussions cluster, it'll need its own marker mapped.
- **News publisher display name** — `top_story`/`discussion` carry the card's
  `.enrichment-card-site` label, which is the **domain** (`nbcnews.com`), not a
  pretty publisher name.

## Maintenance

These are Brave's class names / `data-type` values and **will rot** (the `svelte-*`
hashes especially — which is why the parser avoids them). The runtime parse-0
health signal (scraper logs / `/monitor`) is the backstop: a sustained run of
"HTTP 200 but 0 items parsed" flags a layout change. When re-mapping, re-capture
from a **clone of the warm `brave` profile on an unloaded IP** (pause the news
worker first) — capturing while Brave news scrapes only returns the
`decided to schedule a captcha` interstitial.
