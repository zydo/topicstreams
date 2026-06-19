# Authentication & Security

TopicStreams ships a few built-in controls; beyond them it assumes a
**localhost/LAN or behind-a-reverse-proxy** deployment.

## Built-in controls

- **Bearer-token auth on the REST API** — when any token is configured, **every**
  `/api/v1/*` REST endpoint requires an `Authorization: Bearer <token>` header
  (`api/auth.py`, applied as a router-level dependency). Valid tokens are the
  union of two sources:
  - `TOPICSTREAMS_API_KEY` (env) — a comma-separated bootstrap/break-glass set,
    fixed for the process lifetime (changing it needs a container recreate).
  - the `api_keys` DB table — managed at runtime via
    `scripts/manage_api_keys.py` and read through a short TTL cache
    (`api_key_cache_ttl_seconds`, default 30s), so adding or disabling a token
    goes live **without a restart**.

  With both sources empty, auth is a no-op (dev mode — every endpoint is open).
  WebSocket connections are **not** yet authenticated.
- **The WebSocket endpoint streams *existing* topics only — it does not create
  them.** Connecting to an unknown or inactive topic closes the socket with code
  `1008`, so the stream can't be abused to add scraper targets anonymously (it's
  unauthenticated, so this matters independently of the REST auth above).
- **Rate limiting** — in-memory sliding-window limiter (120 req/60s) per client
  IP (`RateLimitMiddleware` in `api/main.py`). Behind a proxy, set
  `TRUSTED_PROXY_COUNT` so it keys on the real client IP from `X-Forwarded-For`
  (taken Nth-from-right, so prepended entries can't be spoofed). It evicts only
  inactive IPs under memory pressure, so active clients keep their counts.
- **CORS** — configurable via the `CORS_ORIGINS` env var (defaults to `*`).

## Not covered (add before public exposure)

- No user accounts, roles, or sessions; no HTTPS termination; no DDoS protection.
- **WebSocket** streams are unauthenticated (deferred). REST GET endpoints *are*
  covered once a token is configured; WS auth is the remaining gap.
- Tokens are stored in plaintext and there's no per-token usage audit trail.
- The rate limiter is **per-process** (not shared across replicas) and IP-based,
  so clients behind one NAT/VPN/proxy egress share a bucket. For multi-instance
  or precise limiting, use a shared store (Redis) or your proxy/CDN edge limiter.
- WebSocket connections bypass the HTTP rate limiter (Starlette middleware is
  HTTP-only); topic creation is still protected by the WS fix + the authed POST.

## Recommended Solutions (further hardening)

### 1. Authentication & Authorization

#### API Key Authentication (Simple) — already built in

The project already enforces **Bearer-token** auth on every `/api/v1/*` route via
a router-level dependency (`api/auth.py`), with tokens sourced from the
`TOPICSTREAMS_API_KEY` env var and the runtime-managed `api_keys` table. See
[Managing tokens](../README.md#managing-tokens) for day-to-day use. For
reference, the equivalent check is roughly:

```python
# api/auth.py — required on every route via the v1 router's dependencies.
async def require_api_key(credentials = Security(HTTPBearer(auto_error=False))):
    valid = settings.api_keys | db_backed_keys_cached()   # env ∪ DB tokens
    if not valid:
        return                                            # dev mode: open
    if credentials is None or not any(
        secrets.compare_digest(credentials.credentials, k) for k in valid
    ):
        raise HTTPException(401, {"error": "UNAUTHORIZED", ...})
```

#### JWT Token Authentication (Advanced)

```python
# User login returns JWT token
# All subsequent requests include: Authorization: Bearer <token>
# Supports user roles, expiration, refresh tokens
```

#### OAuth2/OpenID Connect

- Integrate with existing identity providers (Google, GitHub, Auth0)
- Best for multi-user scenarios

### 2. API Rate Limiting

Protect against abuse and DDOS:

```python
# A library-based alternative to the built-in RateLimitMiddleware:
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.get("/api/v1/topics")
@limiter.limit("100/minute")  # Max 100 requests per minute per IP
async def get_topics():
    ...
```

### 3. Cloudflare (Recommended for Public Deployment)

Put Cloudflare in front of your service:

```plaintext
Internet → Cloudflare → Your Server
```

#### Free Tier Includes

- DDoS protection (automatic)
- SSL/TLS encryption (automatic)
- CDN caching (for API responses if configured)
- Web Application Firewall (WAF) rules
- Rate limiting (configurable rules)
- Bot protection
- Analytics and logging

#### Paid Tiers Add

- Advanced WAF rules
- Image optimization
- Argo smart routing (faster)
- Higher rate limits

### 4. Additional Security Measures

#### CORS Configuration

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://yourdomain.com"],  # Specific domains only
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)
```

#### Monitoring & Alerting

- Log all authentication failures
- Monitor API usage patterns
- Alert on unusual activity (sudden traffic spikes, repeated 401s)
