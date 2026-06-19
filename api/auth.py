"""Bearer token authentication dependency for the REST API.

Valid tokens are the union of two sources:

- ``TOPICSTREAMS_API_KEY`` (env) — a comma-separated bootstrap/break-glass set,
  fixed for the process lifetime.
- the ``api_keys`` DB table — managed at runtime and read through a short TTL
  cache, so adding or disabling a token takes effect within
  ``api_key_cache_ttl_seconds`` *without* restarting the server.

Auth is a no-op only when *both* sources are empty (dev mode — every endpoint is
open).
"""

import logging
import secrets
import threading
import time

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from common import database as db
from common.settings import settings

logger = logging.getLogger(__name__)

# auto_error=False so we control the 401 envelope (and stay a no-op when no
# tokens are configured) rather than emitting FastAPI's default error shape.
_bearer_scheme = HTTPBearer(auto_error=False)

# TTL cache of the DB-backed token set. Guarded by a lock so a burst of requests
# past expiry collapses to a single refresh (the rest see the fresh value on the
# double-check). On a DB error we keep serving the last-known set and back off,
# so a transient hiccup never bricks auth.
_cache_lock = threading.Lock()
_cached_db_keys: frozenset[str] = frozenset()
_cache_expires_at: float = 0.0
_ERROR_BACKOFF_SECONDS = 5.0


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "error": "UNAUTHORIZED",
            "message": "Invalid or missing API token",
            "status": "error",
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


def _refresh_db_keys() -> frozenset[str]:
    """Re-read the active DB tokens if the cache has expired (blocking). Safe to
    call from a threadpool; returns the cached set on a DB error."""
    global _cached_db_keys, _cache_expires_at
    with _cache_lock:
        now = time.monotonic()
        if now < _cache_expires_at:
            return _cached_db_keys
        ttl = settings.api_key_cache_ttl_seconds
        try:
            keys = frozenset(db.get_active_api_keys())
        except Exception:
            # Keep the last-known set and retry soon; env keys still enforce auth.
            logger.warning(
                "API key cache refresh failed; reusing last-known set", exc_info=True
            )
            _cache_expires_at = now + min(ttl, _ERROR_BACKOFF_SECONDS)
            return _cached_db_keys
        _cached_db_keys = keys
        _cache_expires_at = now + ttl
        return keys


async def _valid_db_keys() -> frozenset[str]:
    # Hot path: cache still valid — no threadpool hop, no DB hit.
    if time.monotonic() < _cache_expires_at:
        return _cached_db_keys
    return await run_in_threadpool(_refresh_db_keys)


def warm_api_key_cache() -> None:
    """Prime the cache at startup (called from the app lifespan) so the very
    first request doesn't pay the refresh, and so a later DB blip serves a warm
    set rather than failing open from a cold cache."""
    global _cache_expires_at
    _cache_expires_at = 0.0  # force a refresh regardless of prior state
    _refresh_db_keys()


def reset_api_key_cache() -> None:
    """Drop the cached set (used by tests for isolation between cases)."""
    global _cached_db_keys, _cache_expires_at
    with _cache_lock:
        _cached_db_keys = frozenset()
        _cache_expires_at = 0.0


async def require_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    """Require a valid ``Authorization: Bearer <token>`` header.

    No-op when no tokens are configured in either source (dev mode). Otherwise
    the request must present a Bearer token matching the env bootstrap set or an
    active DB token.
    """
    valid_keys = settings.api_keys | await _valid_db_keys()
    if not valid_keys:
        return
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    # Constant-time compare against each configured token so a mismatch doesn't
    # leak timing information about how many characters matched.
    token = credentials.credentials
    if not any(secrets.compare_digest(token, key) for key in valid_keys):
        raise _unauthorized()
