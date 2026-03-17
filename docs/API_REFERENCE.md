## API Reference

Base URL: `http://localhost:5000/api/v1`

> **Note:** Replace `5000` with your `HOST_PORT` value if you changed it in `.env` (e.g., `http://localhost:80/api/v1` for production).

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

**Response:** `204 No Content`

**Notes:**

- Topic names are automatically normalized (lowercased, trimmed), in above example, name is normalized as ```climate change```
- Adding an existing inactive topic reactivates it
- Adding an existing active topic is idempotent (no error)

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

**Response:** `204 No Content`

**Notes:**

- Soft delete (marks `is_active = false`)
- Deleting a non-existent topic succeeds (idempotent)
- Use URL encoding for topics with spaces: `Quantum%20Computing` or `Quantum+Computing`
- In this example, the topic with normalized name `quantum computing` will be soft deleted

**Example:**

```bash
curl -X DELETE http://localhost:5000/api/v1/topics/Quantum%20Computing
```

### News

#### Get News for Topic

```http
GET /api/v1/news/{topic_name}
```

**Path Parameters:**

| Parameter    | Type   | Description                     |
| ------------ | ------ | ------------------------------- |
| `topic_name` | string | Topic name (will be normalized) |

**Query Parameters:**

| Parameter | Type    | Default | Range | Description                |
| --------- | ------- | ------- | ----- | -------------------------- |
| `limit`   | integer | `20`    | 1-100 | Number of entries per page |
| `offset`  | integer | `0`     | ≥0    | Pagination offset          |

**Response:**

```json
{
  "topic": "artificial intelligence",
  "entries": [
    {
      "id": 123,
      "topic": "artificial intelligence",
      "title": "AI Breakthrough in Healthcare",
      "url": "https://example.com/article",
      "domain": "example.com",
      "source": "Tech News",
      "scraped_at": "2025-12-03T10:45:00"
    }
  ],
  "total": 150,
  "limit": 20,
  "offset": 0
}
```

**Notes:**

- Results ordered by `scraped_at DESC` (newest first)
- Use pagination for large result sets

**Example:**

```bash
# Get first (newest) 20 news entries
curl http://localhost:5000/api/v1/news/Artificial%20Intelligence

# Get entries 21-40 (pagination)
curl http://localhost:5000/api/v1/news/ARTIFICAL+INTELLIGENCE?limit=20&offset=20

# Get latest 5 entries
curl http://localhost:5000/api/v1/news/artificial+intelligence?limit=5
```

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

- Automatically adds the topic if it doesn't exist (starts scraping), no need to ```POST /api/v1/topics``` to add topic in advance
- Pushes JSON messages when new news entries are scraped
- Connection stays open until client disconnects

**Message Format:**

```json
{
  "id": 789,
  "topic": "artificial intelligence",
  "title": "Breaking: New AI Model Released",
  "url": "https://example.com/breaking-news",
  "domain": "example.com",
  "source": "Tech Times",
  "scraped_at": "2025-12-03T10:55:00"
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

