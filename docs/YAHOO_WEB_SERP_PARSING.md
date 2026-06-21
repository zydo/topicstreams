# Yahoo Web-Search (WEB vertical) SERP parsing

> Research + rationale behind `YahooWebParser` (`scraper/sources/yahoo.py`).
> Empirical basis: live `search.yahoo.com/search?p=` pages captured from the warm
> scraper worker (production exit IP, warm `yahoo` profile) on 2026-06-21 for the
> keywords `us iran`, `apple`, `thomas jefferson`, `weather`, `bitcoin price`,
> `how to tie a tie`, `serendipity`. Companion to
> [GOOGLE_WEB_SERP_PARSING.md](GOOGLE_WEB_SERP_PARSING.md),
> [BING_WEB_SERP_PARSING.md](BING_WEB_SERP_PARSING.md), and
> [WEB_SEARCH_WARMUP.md](WEB_SEARCH_WARMUP.md).

## How the page was captured

Same methodology as Bing: clone the live, warm `yahoo` browser profile inside the
`topicstreams-scraper` container (so we don't fight the running worker's profile
lock) and fetch the raw `search.yahoo.com/search?p=<query>` page on that warm
session, scrolling to settle lazy sections, then save the HTML.

Unlike Bing/Google, a cold capture of Yahoo's web SERP was **not** CAPTCHA-walled
in testing — but Yahoo *does* cool an IP under load (a persistent empty HTTP 500
from its `ATS` edge; see `docs/BLOCK_SIGNAL_FINDINGS.md`), so we still characterise
from the warm worker on the production exit IP to stay representative.

Two findings shaped the parser before any selector work:

1. **Raw query only.** `SearchRequest` defaults to `sort=DATE, recency=HOUR`
   (inherited from the news pipeline). The web page is the relevance page a user
   sees, so `build_url` for WEB emits **only** `p=<query>` (plus `b=` for
   pagination, Yahoo's 1-based result offset) and ignores sort/recency.
2. **Organic URLs are direct.** Yahoo's *news* vertical wraps links in an
   `r.search.yahoo.com/.../RU=<url>/` redirector, but the modern **web** SERP's
   organic links are the **real destination URL**, served directly on the title
   anchor. No decoding needed (the parser still unwraps a legacy `/RU=` redirect
   defensively).

## The SERP is keyword-dependent

Which extra components appear varies by query, but the organic list is the
constant. Observed presence (✓), with the count of organic results we keep:

| query            | organic kept |   news carousel    |   right rail   |  ads  | widgets |
| ---------------- | -----------: | :----------------: | :------------: | :---: | :-----: |
| us iran          |            7 | ✓ (yahoo.com/news) | disambiguation |   –   |    –    |
| apple            |            7 |         –          |       –        | ✓ (3) |    –    |
| thomas jefferson |            7 |         –          | disambiguation |   –   |    –    |
| weather          |            7 |         –          |       –        |   –   | weather |
| bitcoin price    |            7 |         ✓          |       –        |   –   | finance |
| how to tie a tie |            7 |         –          |       –        |   –   |    –    |
| serendipity      |            7 |         –          |       –        |   –   |    –    |

Organic results are a clean, stable 7-per-page on every query; the news carousel,
right rail, ads, and widgets come and go (and none is parsed — see below).

## What we parse, and how

We parse **only organic results** — the one Yahoo component that is reliably
useful and points at a real source. Each becomes a uniform `WebResult` tagged
`ORGANIC`, deduped by destination URL.

| kind      | marker (selector)                                                                 | title / source / snippet                                                                                                                                                                |
| --------- | --------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `organic` | `div.algo` (title anchor `div.compTitle a[href]`, excluding `data-matarget="ad"`) | title `div.compTitle a h3`, the **direct** URL on the anchor, source = the brand in the breadcrumb (anchor text before the URL: `"Reuters https://…"` → `Reuters`), snippet `.compText` |

Implementation notes worth keeping:

- **Title vs. breadcrumb.** Yahoo packs the brand, the display URL breadcrumb, and
  the title all inside one `compTitle` anchor. The real title is the inner `h3`;
  the brand is the breadcrumb text up to the first `http`.
- **Ads reuse the organic markup.** An ad renders as a `div.algo` too, but its
  title anchor carries `data-matarget="ad"` (organic is `"algo"`), so it's skipped
  on that attribute.
- **Direct URLs.** The web SERP gives real hrefs; `_real_url` still unwraps a
  legacy `/RU=<url>/` redirect if one appears, and rejects non-http(s).

## What we deliberately ignore, and why

Same lens as the other engines: keep entries with a readable text payload and a
followable URL **to a real source**; drop aggregation, promos, ads, and nav.

- **News carousel** (`div.tn-carousel.sys_news_auto`) — every card links to
  Yahoo's own `yahoo.com/news/articles/…` aggregator, not the original publisher,
  so all its results collapse to one domain (`yahoo.com`). And for the hot-news
  queries where it appears, those stories are already in the organic list with
  their real-publisher URLs (Reuters, ABC News). Dropped as redundant Yahoo-
  internal aggregation — the same call we made for Bing's Bing-internal video pack.
- **Right rail** (`#right`) — a "See results about" *disambiguation* list (related
  entities, not a single sourced panel), a "Yahoo Scout" AI-results promo, and a
  "Today's trending searches" module. No entity description to surface.
- **Ads** (`data-matarget="ad"`) — commercial, not organic.
- **Weather / finance widgets** — `weather` and `bitcoin price` render answer
  widgets, but (as on Google/Bing) the markup is noisy; the underlying sites
  (weather.com, CoinMarketCap) still come through as organic results.

## Known gaps / not yet parsed

- **No news/discussion kind.** By choice (see above) — Yahoo's news pack is
  Yahoo-mediated. If a future need calls for it, the carousel cards do carry a
  clean `title` attr and a `yahoo.com/news` URL.
- **Entity knowledge panel** — none of the seven queries rendered a sourced entity
  card (only the disambiguation list). Needs a capture where one renders to map it.
- **Answer/featured-snippet widgets** — weather/finance answer boxes are deferred
  as too brittle to map cleanly.

## Maintenance

These are Yahoo's class names (`div.algo`, `div.compTitle`, `.compText`) and **will
rot**. The runtime parse-0 health signal (scraper logs / `/monitor`) is the
backstop: a sustained run of "HTTP 200 but 0 items parsed" flags a layout change.
When re-mapping, re-capture via the warm-worker profile clone (above), not the
host.
