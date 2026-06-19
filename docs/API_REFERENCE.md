## API Reference

Base URL: `http://localhost:5000/api/v1`

> **Note:** Replace `5000` with your `HOST_PORT` value if you changed it in `.env` (e.g., `http://localhost:80/api/v1` for production).

### Authentication

When the server has any API token configured, **all** REST endpoints below
require an `Authorization: Bearer <token>` header; without a valid token they
return `401 UNAUTHORIZED`. When no token is configured the API is open (dev
mode). Tokens come from the `TOPICSTREAMS_API_KEY` env var and/or the
runtime-managed `api_keys` table — see
[Authentication & Security](../README.md#authentication--security). The examples
below omit the header for brevity (dev mode); add `-H "Authorization: Bearer
<token>"` when auth is on. WebSocket streams are not authenticated.

```bash
curl -H "Authorization: Bearer <token>" http://localhost:5000/api/v1/topics
```

### Topics

#### List Topics

```http
GET /api/v1/topics
```

**Query Parameters:**

| Parameter | Type    | Default | Description                            |
| --------- | ------- | ------- | -------------------------------------- |
| `all`     | boolean | `false` | Include inactive (soft deleted) topics |

**Response:**

```json
[
  {
    "id": 1,
    "name": "artificial intelligence",
    "created_at": "2025-12-03T10:30:00",
    "is_active": true
  }
]
```

**Example:**

```bash
# Get active topics only
curl http://localhost:5000/api/v1/topics

# Get all topics including inactive
curl http://localhost:5000/api/v1/topics?all=true
```

#### Add Topic

```http
POST /api/v1/topics
```

**Request Body:**

```json
{
  "name": " Climate    CHANGE "
}
```

**Response:** `201 Created` (empty body)

**Notes:**

- Topic names are automatically normalized (lowercased, trimmed), in above example, name is normalized as ```climate change```
- Adding an existing inactive topic reactivates it
- Adding an existing active topic is idempotent (no error)
- Requires `Authorization: Bearer <token>` when the server has API auth configured (see [Authentication](#authentication))

**Example:**

```bash
curl -X POST http://localhost:5000/api/v1/topics \
  -H "Content-Type: application/json" \
  -d '{"name": "Quantum Computing"}'
```

#### Delete Topic

```http
DELETE /api/v1/topics/{topic_name}
```

**Path Parameters:**

| Parameter    | Type   | Description                     |
| ------------ | ------ | ------------------------------- |
| `topic_name` | string | Topic name (will be normalized) |

**Response:** `200 OK` (empty body)

**Notes:**

- Soft delete (marks `is_active = false`)
- Deleting a non-existent topic succeeds (idempotent)
- Use URL encoding for topics with spaces: `Quantum%20Computing` or `Quantum+Computing`
- In this example, the topic with normalized name `quantum computing` will be soft deleted
- Requires `Authorization: Bearer <token>` when the server has API auth configured (see [Authentication](#authentication))

**Example:**

```bash
curl -X DELETE http://localhost:5000/api/v1/topics/Quantum%20Computing
```

### News

News uses **cursor pagination** keyed on the entry `id` (which is monotonic
with scrape time, so it matches `scraped_at` order). To page backward through
older entries, pass the `next_before_id` from the previous response as
`before_id`. Cursor pagination is immune to the offset drift that live
insertions cause at the top of the feed.

#### Get News (all topics)

```http
GET /api/v1/news
```

A single chronological stream across **all active topics**, newest first.
Entries from soft-deleted (inactive) topics are excluded.

**Query Parameters:**

| Parameter   | Type    | Default | Range | Description                                                                              |
| ----------- | ------- | ------- | ----- | ---------------------------------------------------------------------------------------- |
| `limit`     | integer | `20`    | 1-100 | Number of entries per page                                                               |
| `before_id` | integer | —       | ≥1    | Return only entries older than this id (cursor)                                          |
| `engine`    | string  | —       | —     | Show only entries surfaced by this engine (e.g. `bing`). Orthogonal to the topic filter. |

#### List Feed Engines

```http
GET /api/v1/news/engines
```

Returns the distinct engines that have surfaced a feed event **within the last 7
days**, sorted — e.g. `["bing", "google"]`. Powers the UI engine filter so it
offers only engines with recent data; an engine that stops producing (disabled
or long rate-limited) ages out of the list on its own.

#### Get News for Topic

```http
GET /api/v1/news/{topic_name}
```

**Path Parameters:**

| Parameter    | Type   | Description                     |
| ------------ | ------ | ------------------------------- |
| `topic_name` | string | Topic name (will be normalized) |

**Query Parameters:**

| Parameter   | Type    | Default | Range | Description                                              |
| ----------- | ------- | ------- | ----- | -------------------------------------------------------- |
| `limit`     | integer | `20`    | 1-100 | Number of entries per page                               |
| `before_id` | integer | —       | ≥1    | Return only entries older than this id (cursor)          |
| `engine`    | string  | —       | —     | Show only entries surfaced by this engine (e.g. `bing`). |

**Response:**

```json
{
  "entries": [
    {
      "id": 123,
      "topic": "artificial intelligence",
      "title": "AI Breakthrough in Healthcare",
      "url": "https://example.com/article",
      "domain": "example.com",
      "source": "Tech News",
      "snippet": "A short excerpt of the article shown under the headline.",
      "scraped_at": "2025-12-03T10:45:00",
      "engines": ["bing", "google"]
    }
  ],
  "limit": 20,
  "next_before_id": 123,
  "topic": "artificial intelligence",
  "total": 150
}
```

**Notes:**

- Results ordered by `id DESC` (newest first).
- `next_before_id` is the cursor for the next (older) page, or `null` when the earliest entry has been reached.
- `topic` and `total` are populated only by the single-topic endpoint; the all-topics endpoint returns `null` for both.
- `engines` lists every search engine that surfaced the entry (deduped across engines). The `engine` filter restricts which entries are returned, but each returned entry still shows its full `engines` list. `total` reflects the active `engine` filter.
- `snippet` is a short excerpt/blurb for display only (may be `null`). It is **not** part of the article identity; when several engines or re-scrapes excerpt the same article differently, the longest is kept. The same `snippet` field is included in the WebSocket payload (see [Real-Time News Updates](#real-time-news-updates)).

**Example:**

```bash
# Newest 20 entries for a topic
curl http://localhost:5000/api/v1/news/Artificial%20Intelligence

# Next (older) page — pass the previous response's next_before_id
curl "http://localhost:5000/api/v1/news/artificial+intelligence?limit=20&before_id=104"

# Newest 5 across all topics
curl "http://localhost:5000/api/v1/news?limit=5"
```

### Status & Metrics

#### Health Status

```http
GET /api/v1/status
```

The scrape-health signal, computed server-side from recent scraper logs.

**Response:** `{ state, label, detail, active_topics, total_news }`, where `state` is one of `live | degraded | errors | parsing | stalled | idle`. `parsing` means scrapes return HTTP 200 but parse 0 items (a search engine's markup may have changed, or a silent block).

#### Metrics

```http
GET /api/v1/metrics?window=3600
```

Operational counters plus a per-engine scrape breakdown, a recent-cycle
timeline, and recent failures. Powers the built-in **`/monitor`** ops page.

**Query Parameters:**

| Parameter | Type    | Default | Description                                          |
| --------- | ------- | ------- | ---------------------------------------------------- |
| `window`  | integer | `3600`  | Aggregation window in seconds (60 … 604800 / 7 days) |

**Response:** the lightweight fields below (kept for back-compat) plus a rich
dashboard payload. Aggregates are computed over the `window`; latency
percentiles (`avg`/`p50`/`p95`) ignore unmeasured attempts.

| Field                    | Type          | Description                               |
| ------------------------ | ------------- | ----------------------------------------- |
| `active_topics`          | integer       | Watched (active) topics                   |
| `total_news`             | integer       | Feed events across active topics          |
| `scrape_success_rate`    | float \| null | Overall scrape success rate in the window |
| `feed_freshness_seconds` | float \| null | Age of the newest feed event, in seconds  |
| `generated_at`           | string        | When this response was assembled (UTC)    |
| `window_seconds`         | integer       | Aggregation window actually used          |
| `overall`                | object        | Totals over the window (see below)        |
| `engines`                | array         | Per-engine aggregates, engine name A→Z    |
| `recent_cycles`          | array         | Newest-first per-cycle summaries          |
| `recent_failures`        | array         | Newest-first failed scrapes               |

Each `engines[*]` entry carries `scrapes`, `successes`, `success_rate`,
`entries_parsed`, `zero_parse` (successful scrapes that parsed 0 items — a
selector-rot signal), `failures`, `blocked` (failures with HTTP 429/403/503),
`avg_latency_ms` / `p50_latency_ms` / `p95_latency_ms`, `last_scrape_at`,
`last_success`, `last_http_status`, `http_status_breakdown`,
`cooldown_seconds_remaining` (seconds until the scraper next probes a benched
engine, else null) / `cooldown_failures`, and a heuristic `health` label:
`healthy | degraded | blocked | parsing | cooldown | idle` (see
[docs/OBSERVABILITY.md](OBSERVABILITY.md)). Each `recent_cycles[*]` carries
`started_at`, `finished_at`, `duration_seconds`, `topics_count`,
`entries_parsed`, `new_events`, `success`, `error`.

> Every API response also carries an `X-Process-Time-Ms` header with the request's processing time.

### Logs

#### Get Scraper Logs

```http
GET /api/v1/logs
```

**Query Parameters:**

| Parameter | Type    | Default | Range | Description                     |
| --------- | ------- | ------- | ----- | ------------------------------- |
| `limit`   | integer | `20`    | 1-100 | Number of log entries to return |

**Response:**

```json
[
  {
    "id": 456,
    "topic": "artificial intelligence",
    "scraped_at": "2025-12-03T10:50:00",
    "success": true,
    "http_status_code": 200,
    "error_message": null
  },
  {
    "id": 455,
    "topic": "climate change",
    "scraped_at": "2025-12-03T10:49:30",
    "success": false,
    "http_status_code": 429,
    "error_message": null
  }
]
```

**Notes:**

- Results ordered by `scraped_at DESC` (newest first)
- One log entry = one webpage load attempt
- `success = false` indicates scraping failure (check `http_status_code` and `error_message`)

**Example:**

```bash
# Get last 10 scraper logs
curl http://localhost:5000/api/v1/logs?limit=10
```

### WebSocket

#### Real-Time News Updates

```bash
websocat ws://localhost:5000/api/v1/ws/news/{topic_name}
```

**Path Parameters:**

| Parameter    | Type   | Description                     |
| ------------ | ------ | ------------------------------- |
| `topic_name` | string | Topic name (will be normalized) |

**Behavior:**

- The topic must already exist; create it via ```POST /api/v1/topics``` first. Connecting to an unknown or inactive topic closes the socket with code `1008`. (The WS never creates topics — auto-creating let unauthenticated clients add scraper load.)
- Pushes JSON messages when new news entries are scraped
- Connection stays open until client disconnects

**Message Format:** the same `NewsEntry` shape the REST feed returns (including
`snippet` and `engines`), one JSON object per new feed event.

```json
{
  "id": 789,
  "topic": "artificial intelligence",
  "title": "Breaking: New AI Model Released",
  "url": "https://example.com/breaking-news",
  "domain": "example.com",
  "source": "Tech Times",
  "snippet": "A short excerpt of the article shown under the headline.",
  "scraped_at": "2025-12-03T10:55:00",
  "engines": ["google"]
}
```

**Example:**

```bash
# Using websocat
websocat ws://localhost:5000/api/v1/ws/news/Bitcoin

# With formatted output
websocat ws://localhost:5000/api/v1/ws/news/bitcoin | jq

# Using JavaScript
const ws = new WebSocket('ws://localhost:5000/api/v1/ws/news/bitcoin');
ws.onmessage = (event) => {
  const news = JSON.parse(event.data);
  console.log('New article:', news.title);
};
```

### Error Responses

All errors return JSON with this structure:

```json
{
  "error": "ERROR_CODE",
  "message": "Human-readable error message",
  "status": "error"
}
```

**Common HTTP Status Codes:**

| Code  | Error Type              | Description                                     |
| ----- | ----------------------- | ----------------------------------------------- |
| `400` | `BAD_REQUEST`           | Invalid request (e.g., topic name too long)     |
| `401` | `UNAUTHORIZED`          | Missing or invalid `Authorization: Bearer` token (when auth is enabled) |
| `422` | `VALIDATION_ERROR`      | Request validation failed (see `details` field) |
| `500` | `INTERNAL_SERVER_ERROR` | Unexpected server error                         |

**Example Error:**

```json
{
  "error": "VALIDATION_ERROR",
  "message": "Invalid request parameters",
  "details": [
    {
      "loc": ["query", "limit"],
      "msg": "ensure this value is less than or equal to 100",
      "type": "value_error"
    }
  ],
  "status": "error"
}
```

