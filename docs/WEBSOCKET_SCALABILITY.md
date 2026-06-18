# WebSocket Scalability

> **Status:** real-time fanout already rides on **Postgres `LISTEN/NOTIFY`**, which
> works across multiple API replicas as-is. The only genuinely
> multi-replica-unsafe piece is the in-process rate limiter. None of this is a
> bottleneck for the single-instance Docker-Compose deployment this project
> targets.

## How fanout works today

TopicStreams does **not** broadcast from the process that scrapes. New articles
flow to clients through the database:

1. The scraper inserts news; a Postgres trigger fires `NOTIFY news_updates,
   '<topic>:<id>'` (see `postgres/init.sql`).
2. Each API process holds a dedicated `LISTEN news_updates` connection
   (`api/v1/websocket/manager.py`, `_postgres_listener`). The payload is just
   `topic:id` — not the article — which sidesteps `NOTIFY`'s 8 KB payload limit.
3. On a notification the process re-fetches the entry and sends it to its own
   locally-connected WebSocket clients (`_broadcast_to_topic`).

Postgres delivers every `NOTIFY` to **all** listening connections. So Postgres
is already acting as the pub/sub bus — the role a Redis Pub/Sub layer would
otherwise play.

## What this means for multiple replicas

If you ran N API replicas behind a load balancer, **WebSocket fanout already
works**: each replica has its own `LISTEN` connection, each receives the
notification independently, and each fans out to the clients connected to it.
The per-replica `for connection in subscribers: send()` loop is `O(local
connections)`, which is exactly what horizontal scaling distributes — a client
is only ever served by the one replica it connected to.

### The one open item: the rate limiter

The HTTP rate limiter (`RateLimitMiddleware` in `api/main.py`) keeps its
sliding-window counters **in process**. With N replicas, a client's requests
spread across them, so the effective limit is up to N× the configured value.
This is a mild correctness drift, not a failure mode. Options, cheapest first:

- **Rate-limit at the edge** — nginx/Traefik/Cloudflare in front of the replicas
  enforces a single shared limit. Usually you already have this proxy.
- **Sticky routing** — hash clients to replicas (IP hash) so each client's
  counter lives on one replica.
- **Shared store** — move the counter to Redis for a precise global limit. This
  is the only case that actually warrants adding Redis, and it's a real lift for
  one counter — do it only if the edge/sticky options don't fit.

## Scaling ceiling

Postgres `LISTEN/NOTIFY` comfortably handles this app's notification volume (one
notify per new feed event). It is not a 100K-subscriber message bus, but the
single-user, self-hosted deployment here is nowhere near that. If you ever
genuinely outgrow it (thousands of concurrent subscribers across many
replicas), the migration is to publish to Redis Pub/Sub (or Kafka for
replay/persistence) instead of `NOTIFY`, with each replica subscribing and
fanning out locally — i.e. the same topology, a different bus. There is no
reason to do this preemptively.
