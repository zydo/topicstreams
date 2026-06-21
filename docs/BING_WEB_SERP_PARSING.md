# Bing Web-Search (WEB vertical) SERP parsing

> Research + rationale behind `BingWebParser` (`scraper/sources/bing.py`).
> Empirical basis: live `bing.com/search?q=` pages captured from the warm scraper
> worker (production exit IP, warm `bing` profile) on 2026-06-21 for the keywords
> `us iran`, `apple`, `thomas jefferson`, `weather`, `bitcoin price`,
> `how to tie a tie`, `serendipity`. Companion to
> [GOOGLE_WEB_SERP_PARSING.md](GOOGLE_WEB_SERP_PARSING.md) and
> [WEB_SEARCH_WARMUP.md](WEB_SEARCH_WARMUP.md).

## How the page was captured

On-demand web search runs on each engine's **warm news session**. To map the DOM
we cloned the live, warm `bing` browser profile inside the `topicstreams-scraper`
container (so we didn't fight the running worker's profile lock) and fetched the
raw `bing.com/search?q=<query>` page on that warm session, scrolling to settle
lazy sections, and saved the HTML.

Do **not** characterise from the host machine or a cold/foreign profile. A cold
local capture returned Bing's **"Rewards — One last step — Please solve…"**
verification page — search chrome only, no `#b_results`, no results at all. The
warm worker on the production exit IP returns the real SERP. (Bing never *hard*-
blocks the news vertical, but the web endpoint will challenge a cold/suspicious
session, so warmth matters here just like it does for Google.)

Two findings shaped the parser before any selector work:

1. **Raw query only.** `SearchRequest` defaults to `sort=DATE, recency=HOUR`
   (inherited from the news pipeline). The web page is the *relevance* page a user
   sees, so `build_url` for WEB emits **only** `q=<query>` (plus `first=` for
   pagination, Bing's 1-based offset) and ignores sort/recency.
2. **Every href is redirect-wrapped.** Bing rewrites each result link through its
   click tracker: `https://www.bing.com/ck/a?…&u=a1<base64>&ntb=1`, where the real
   destination is **base64 in the `u=a1…` param** (the `a1` is an encoding tag).
   The parser must decode this (`_bing_real_url`) — `cite` shows only a breadcrumb
   display URL. Links that decode to a **relative** path or a `bing.com` host are
   Bing-internal (video player, image search) and are dropped.

## The SERP is keyword-dependent

Which components render varies by query intent. Observed presence (✓), with the
post-parse counts of what we keep:

| query            | organic |  news pack   | video pack | entity sidebar |  widgets  | → kept (organic / news) |
| ---------------- | ------: | :----------: | :--------: | :------------: | :-------: | ----------------------: |
| us iran          |      10 | ✓ (23 cards) |     ✓      |  "deep dive"   |     –     |                  10 / 6 |
| apple            |       6 |    ✓ (11)    |     ✓      |  "deep dive"   |     –     |                   6 / 6 |
| thomas jefferson |       9 |      –       |     ✓✓     |  "deep dive"   | genai bio |                   9 / 0 |
| weather          |       7 |      –       |     ✓      |  "deep dive"   |  weather  |                   7 / 0 |
| bitcoin price    |      10 |      ✓       |     ✓      |  "deep dive"   |  finance  |                  10 / 2 |
| how to tie a tie |      10 |      –       |     ✓      |  "deep dive"   |     –     |                   8 / 0 |
| serendipity      |      10 |      –       |     ✓      |  "deep dive"   |     –     |                  10 / 0 |

So: organic results are the stable backbone on every query; hot-news queries add a
deep news carousel; every query gets a video pack and a "Deep dive into …" sidebar
(neither useful — see below).

## What we parse, and how

Each kept component becomes a uniform `WebResult` (`common/model.py`). We keep only
the two kinds that are reliably **followable to a real source** and carry text,
ordered organic-first, deduped by destination URL.

| kind         | marker (selector)                                                      | title / source / snippet                                                                                                     |
| ------------ | ---------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `organic`    | `li.b_algo`                                                            | title `h2 a`, URL decoded from the `/ck/a` redirect, source `.tptt` (publisher name), snippet `.b_caption p`                 |
| `top_story`  | `a.nslite_card_link` / `a.nslist_card_link` in the "News about …" pack | decoded URL + headline (the `title` attr, with its leading `· <age> [· on MSN]` meta stripped); source carried by the domain |
| `discussion` | same anchors, **host in the shared social/forum allow-list**           | reddit/x/medium/… cards, capped harder                                                                                       |

Implementation notes worth keeping:

- **Redirect decoding is mandatory.** `_bing_real_url` decodes `u=a1<base64>`
  (tolerating the URL-safe alphabet), passes a direct absolute href through, and
  rejects relative paths and `bing.com` hosts (internal players/search).
- **Two class spellings.** Bing ships both `nslite_card_link` and `nslist_card_link`
  for news-card anchors; match either.
- **News headline meta.** The card's `title` attr is `"· 3h Headline…"` or
  `"· 45m · on MSN Headline…"`; a regex strips the leading `· <age>`/`· on <src>`
  run so the headline is clean.
- **News pack is capped.** The "News about …" pack is a deep carousel (23 cards on
  `us iran`) that would swamp the organic results, so we keep only the leading
  `_MAX_NEWS` (6); discussions within it are capped harder (`_MAX_DISCUSSIONS`, 3).
- **Shared helpers.** The social/forum host list and `domain_of`/`is_discussion`
  live in `base.py`, shared with Google (and future engines).

## What we deliberately ignore, and why

Same lens as Google: keep things with a **readable text payload** and/or a
**followable URL to a real source**; drop navigation, pure-media, ads, and
promos.

- **Video pack** (`b_vidAns` / `.mc_vtvc`) — every tile links to a Bing-internal
  `/videos/riverview/relatedvideo?…` **player**, not the source video. Thumbnail
  chrome with no followable destination; dropped (mirrors Google's exclusion of
  thumbnail-only carousels).
- **Entity sidebar** (`#b_context`) — on all seven queries it was a **"Deep dive
  into <query>"** Copilot promo (a chat CTA), *not* a Wikipedia-style knowledge
  panel. No description text to surface.
- **Copilot generative answer** (`div.copans_container` → `l_genaibdy` /
  `l_genaidescovr_exp`) — an AI-composed summary on experimental, fast-rotating
  markup. Defer rather than ship a brittle selector over generated (possibly
  unsourced) text.
- **Ads** (`li.b_ad`, `b_adTop`/`b_adBottom`) — commercial, not organic.
- **Pagination / chrome** (`li.b_pag`, header/footer, related searches) — not
  results.

## Known gaps / not yet parsed

- **Weather / finance / dictionary widgets** — `weather`, `bitcoin price`, and
  `serendipity` render structured answer widgets, but (as on Google) their markup
  is noisy and didn't yield a clean value; deferred rather than ship brittle
  selectors. The underlying sites (weather.com, CoinMarketCap, Merriam-Webster)
  still come through as organic results.
- **A real entity knowledge panel** — none of the seven queries produced a
  sourced entity card (only the "Deep dive" promo). Needs a capture where Bing
  renders an actual entity panel to map it.
- **News publisher name** — the carousel card has no stable element for the
  publisher, so `top_story`/`discussion` results carry the source via their domain
  only (the `WebResult.source` field is left unset).

## Maintenance

These are Bing's class names and **will rot** (and the `/ck/a` redirect format can
change). The runtime parse-0 health signal (scraper logs / `/monitor`) is the
backstop: a sustained run of "HTTP 200 but 0 items parsed" flags a layout change.
When re-mapping, re-capture via the warm-worker profile clone (above), not the
host — a cold capture only gets the "One last step" challenge page.
