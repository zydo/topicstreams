# WebSocket Scalability

> **Not implemented yet** - The current WebSocket implementation uses simple broadcasting that doesn't scale beyond a limited number of subscribers.

## Current State: Simple Broadcasting

TopicStreams uses a straightforward approach for WebSocket message distribution:

```python
# Current implementation (simplified)
async def broadcast_news_update(news_entry: NewsEntry):
    for websocket in connected_websockets[topic]:
        try:
            await websocket.send_json(news_entry.dict())
        except ConnectionClosedOK:
            connected_websockets[topic].remove(websocket)
```

### How It Works

- Each WebSocket connection is stored in memory
- When new news arrives, the server iterates through ALL connected clients for that topic
- Messages are sent one-by-one to each subscriber
- Failed connections are cleaned up during broadcasting

## Scalability Limitations

**This approach has significant limitations:**

1. **O(n) Broadcast Cost** - Sending to 1000 subscribers requires 1000 separate send operations
2. **Memory Usage** - All WebSocket connections stored in server memory
3. **Single Point of Failure** - If one server restarts, all connections are lost
4. **No Load Distribution** - All broadcasting load handled by single server instance
5. **Slow Message Delivery** - Large subscriber bases experience delivery delays

## Recommended Solutions

### 1. Redis Pub/Sub

```python
# Future implementation with Redis
import redis
import json

async def publish_news_update(news_entry: NewsEntry):
    redis_client = redis.Redis()
    await redis_client.publish(
        f"news_updates:{news_entry.topic}",
        json.dumps(news_entry.dict())
    )

# WebSocket servers subscribe to Redis channels
async def redis_subscriber():
    pubsub = redis_client.pubsub()
    await pubsub.subscribe("news_updates:*")

    async for message in pubsub.listen():
        if message['type'] == 'message':
            await broadcast_to_local_clients(message['data'])
```

#### Benefits

- **O(1) Publish Cost** - Single publish operation regardless of subscriber count
- **Horizontal Scaling** - Multiple WebSocket servers can subscribe to same Redis channels
- **Fault Tolerance** - Redis handles message queuing and delivery
- **Low Latency** - Optimized binary protocol for message distribution

#### Implementation

- Add Redis to docker-compose.yml
- Modify WebSocket servers to publish/subscribe via Redis
- Each server only manages its local connections
- Redis handles cross-server message distribution

### 2. Apache Kafka

For very large-scale deployments (10K+ subscribers):

```python
# Future implementation with Kafka
from kafka import KafkaProducer

async def publish_news_update(news_entry: NewsEntry):
    producer = KafkaProducer(
        bootstrap_servers=['kafka:9092'],
        value_serializer=lambda v: json.dumps(v).encode()
    )
    producer.send(f"news-updates-{news_entry.topic}", news_entry.dict())
```

#### Benefits

- **Message Persistence** - Messages stored on disk, can replay missed updates
- **Partitioning** - Natural load distribution across multiple consumers
- **Exactly-Once Semantics** - Guaranteed message delivery without duplication
- **Backpressure Handling** - Natural flow control for slow consumers

## When You Need This

### You Probably DON'T Need These Solutions If:

- You have fewer than 100 concurrent WebSocket connections
- Running on a single server with minimal scaling needs
- Message delivery latency of <100ms is acceptable

### You SHOULD Consider These Solutions If:

- Expecting 100+ concurrent WebSocket connections per topic
- Need to scale horizontally across multiple servers
- Require <10ms message delivery latency
- Need fault tolerance and automatic failover
- Running 24/7 with high subscriber volume
