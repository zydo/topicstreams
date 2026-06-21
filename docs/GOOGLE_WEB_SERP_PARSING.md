# Google Web-Search (WEB vertical) SERP parsing

> Research + rationale behind `GoogleWebParser` (`scraper/sources/google.py`).
> Empirical basis: live `/search?q=` pages captured from the warm scraper worker
> (production exit IP, warm `google` profile) on 2026-06-20/21 for the keywords
> `us iran`, `apple`, `thomas jefferson`, `weather`, `bitcoin price`,
> `how to tie a tie`, `serendipity`. Companion to
> [WEB_SEARCH_WARMUP.md](WEB_SEARCH_WARMUP.md).

## How the page was captured

On-demand web search runs on each engine's **warm news session** (see the warmup
doc). To map the DOM we ran a temporary, env-gated probe inside the
`topicstreams-scraper` container that did a news warm-up then fetched the raw
`https://www.google.com/search?q=<query>` page on the *same* live context,
scrolling to load lazy sections, and saved the HTML. The probe was reverted after
capture. Do **not** characterise from the host machine or a cloned profile — a
cold/foreign-IP session just gets CAPTCHA'd and isn't representative.

Two findings shaped the parser before any selector work:

1. **Raw query only.** `SearchRequest` defaults to `sort=DATE, recency=HOUR`
   (inherited from the news pipeline). Feeding that into the web endpoint emits
   `&tbs=sbd:1,qdr:h`, which returns a *date-sorted, video-heavy* page — not the
   relevance page a user sees. `build_url` for WEB therefore emits **only**
   `q=<query>` (plus `start` for pagination) and ignores sort/recency.
2. **One wrapper, many components.** Organic results, the news pack, the video
   carousel, and spacers all share the generic `div.MjjYud` wrapper. Selecting
   `MjjYud` + first `h3` conflates them and surfaces whichever block is first
   (usually a video). Each component must be targeted by its **inner** marker.

## The SERP is keyword-dependent

Which components appear varies by query intent. Observed mix (post-parse counts):

| query | answer | KP | widget | organic | top_story | discussion | video |
|---|--:|--:|--:|--:|--:|--:|--:|
| us iran | – | – | – | 7 | 3 | – | 2 |
| apple | – | – | – | 6 | 9 | 5 | 7 |
| thomas jefferson | – | 1 | – | 9 | – | – | 1 |
| weather | – | – | 1 | 9 | – | 3 | 2 |
| bitcoin price | 1 | – | – | 9 | 4 | 5 | 3 |
| how to tie a tie | – | – | – | 6 | 2 | 5 | 4 |
| serendipity | – | – | – | 9 | – | – | 1 |

So: hot-news queries lead with a news pack; entities (`thomas jefferson`) get a
knowledge panel; tools/data (`weather`, `bitcoin price`) get a widget / answer
box; how-to and brand queries pull lots of video + social discussion.

## What we parse, and how

Each component becomes a uniform `WebResult` (`common/model.py`) tagged with a
`kind`. `find_items` emits them ordered by **how directly they answer an
information lookup**, deduped by URL:

| kind | marker (inner container) | title / source / snippet |
|---|---|---|
| `answer` | `div.LGOjhe` (featured snippet) | snippet = answer text; source link climbed from the surrounding result |
| `knowledge_panel` | `div.kno-rdesc` | snippet = entity description; source = the panel's link (usually Wikipedia) |
| `widget` | `#wob_wc` (weather) | snippet = `"NN°, condition"`; no URL |
| `organic` | `div.tF2Cxc` | title `h3`, clean `a[href]`, source `span.VuuXrf`, snippet `div.VwiC3b` |
| `top_story` | `a.WlydOe` in `div[data-news-cluster-id]` | title `div[role=heading]`, source `.MgUUmf` |
| `discussion` | same as top_story, **host in a social/forum allow-list** | reddit/x/medium/… cards, **capped at 5** |
| `video` | standalone `youtube.com/watch` anchors | title (skipping uploader/date headings), channel, text summary when present |

Implementation notes worth keeping:

- **YouTube dedup.** Dedup normalises URLs by dropping the query string — except
  for YouTube, whose video id is `?v=`, so all watch URLs would otherwise
  collapse to one key.
- **Video chips vs. results.** `youtube.com/watch` anchors *inside* a
  `div.tF2Cxc` or carrying `aria-label="… View related links"` are related-link
  chips, not standalone videos — excluded.
- **Video title/channel hygiene.** Some video blocks put an uploader name or
  relative time (`1w`, `Sep 28, 2024`) in a `role=heading`; the title picker
  skips date/duration/relative-time-looking headings, and a trailing date is
  stripped off the channel (`"defragmenteur Sep 28, 2024"` → `"defragmenteur"`).
- **Discussions split + capped.** `data-news-cluster-id` is overloaded (news,
  discussions, jobs, podcasts all use it) with no reliable section label, so the
  news/discussion split is by **host** (allow-list in `_DISCUSSION_DOMAINS`), and
  discussions are capped so a chatty query can't drown the substantive results.

## What we deliberately ignore, and why

Decided against the lens of *"a human or AI agent looking up useful information"* —
keep things with a **readable text payload** and/or a **followable URL**; drop
navigation, pure-media-without-text, ads, and related-questions.

- **People also ask** (`div[data-q]`) — *related questions*, not answers to the
  current query. Better surfaced as query suggestions than as results.
- **Image / short-video carousels** — thumbnail-only, no text; useless to an
  agent, rarely what an info lookup needs.
- **Ads** (`#tads`, `div[data-text-ad]`) — commercial, not organic.
- **Sitelinks** — navigational shortcuts within one site; belong attached to
  their parent organic result, not as standalone entries.
- **Tabs / nav chrome** (All / Images / Maps / `udm=` links) — not results.

## Known gaps / not yet parsed

- **Dictionary box** — `serendipity` is the canonical dictionary query, but the
  structured definition box was **absent** from the captured page (no
  `data-dobid` / `lr_dct` / "Definitions"); nothing to map. Needs a capture where
  it actually renders. (The dictionary *sites* — Merriam-Webster, Cambridge —
  still come through as organic results.)
- **Finance widget** (`bitcoin price`, `apple` stock) — the price card's markup
  is "Currency Converter / Market Summary / feedback-UI" noise that didn't yield
  a clean value; deferred rather than ship brittle selectors.
- **Overloaded clusters** — `data-news-cluster-id` also wraps **Jobs** and
  **Podcasts** packs (seen on `apple`), which currently fall under `top_story`.
  Distinguishing them needs a reliable section-label signal we didn't find.

## Maintenance

These selectors are Google's obfuscated, rotating class names and **will rot**.
The runtime parse-0 health signal (see the scraper logs / `/monitor`) is the
backstop: a sustained run of "HTTP 200 but 0 items parsed" flags a layout change.
When re-mapping, re-capture via the warm-worker probe (above), not the host.
