# Authentication & Security

> **Not implemented yet** - The current project is designed for **localhost/LAN access only** and has **no authentication or security features**.

## Current State: Localhost/LAN Only

TopicStreams is intentionally minimal and assumes deployment in a **trusted environment**:

- Perfect for: Local machine, home network, trusted team LAN
- **NOT safe for**: Public internet exposure without additional security layers

### No Built-in Security

- No user authentication or authorization
- No API rate limiting (anyone can flood the API)
- No protection against DDOS or malicious attacks
- No HTTPS/SSL encryption
- No input sanitization beyond basic validation
- No CORS configuration for browser security

## Recommended Solutions

### 1. Authentication & Authorization

#### API Key Authentication (Simple)

```python
# Future implementation example
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
# Future implementation with slowapi
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
