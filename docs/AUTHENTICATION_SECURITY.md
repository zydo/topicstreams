# Authentication & Security

TopicStreams ships a few built-in controls; beyond them it assumes a
**localhost/LAN or behind-a-reverse-proxy** deployment.

## Built-in controls

- **API key on writes** — when the `API_KEY` env var is set, `POST` and
  `DELETE /api/v1/topics` require a matching `X-API-Key` header (`api/auth.py`).
  Unset = open (dev mode).
- **Topic creation is the only state-changing action, and it's gated by that
  key.** The WebSocket endpoint streams *existing* topics only — it does **not**
  create them. Connecting to an unknown or inactive topic closes the socket with
  code `1008`, so the stream can't be abused to add scraper targets anonymously.
- **Rate limiting** — in-memory sliding-window limiter per client IP
  (`RateLimitMiddleware` in `api/main.py`).
- **CORS** — configurable via the `CORS_ORIGINS` env var (defaults to `*`).

## Not covered (add before public exposure)

- No user accounts, roles, or sessions; no HTTPS termination; no DDoS protection.
- Read endpoints (GET and WebSocket streams) are unauthenticated by design.
- The rate limiter is per-process and keyed on `request.client.host`; behind a
  proxy you'd add `X-Forwarded-For` handling and a shared store.

## Recommended Solutions (further hardening)

### 1. Authentication & Authorization

#### API Key Authentication (Simple)

```python
# The project already gates writes via an X-API-Key dependency (api/auth.py).
# A broader middleware variant that protects every route would look like:
@app.middleware("http")
async def verify_api_key(request: Request, call_next):
    api_key = request.headers.get("X-API-Key")
    if api_key not in valid_api_keys:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    return await call_next(request)
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
