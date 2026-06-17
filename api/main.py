"""FastAPI application entry point for the TopicStreams news aggregation API.

This module sets up the FastAPI application with custom JSON formatting,
database lifecycle management, and API v1 routes.
"""

import json
import logging
import uvicorn
from contextlib import asynccontextmanager
from time import time
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from starlette.middleware.base import BaseHTTPMiddleware

from common.database import close_pool
from common.logging_config import configure_logging
from common.settings import settings
from .exceptions import TopicStreamsException
from .v1.router import router as v1_router
from .v1.websocket.manager import manager as websocket_manager

configure_logging(settings.log_format)
logger = logging.getLogger(__name__)


class TimingMiddleware(BaseHTTPMiddleware):
    """Add an X-Process-Time-Ms header with the request duration."""

    async def dispatch(self, request: Request, call_next):
        start = time()
        response = await call_next(request)
        response.headers["X-Process-Time-Ms"] = f"{(time() - start) * 1000:.1f}"
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window in-memory rate limiter, keyed by client IP.

    Behind reverse proxies, set ``trusted_proxies`` so the client IP is read
    from ``X-Forwarded-For`` instead of the proxy's address. The IP is taken as
    the Nth entry from the *right*, which is spoof-resistant: a client can
    prepend fake entries, but can't forge the address each trusted proxy
    appends. With ``trusted_proxies=0``, ``X-Forwarded-For`` is ignored and the
    direct peer IP is used.

    State is in-memory and per-process (not shared across replicas).
    """

    def __init__(
        self,
        app,
        calls: int = 120,
        period: int = 60,
        trusted_proxies: int = 0,
        max_tracked: int = 10000,
    ):
        super().__init__(app)
        self.calls = calls
        self.period = period
        self._trusted_proxies = trusted_proxies
        self._max_tracked = max_tracked
        self._requests: dict[str, list[float]] = {}

    def _client_ip(self, request: Request) -> str:
        if self._trusted_proxies > 0:
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                parts = [p.strip() for p in forwarded.split(",") if p.strip()]
                if parts:
                    return parts[-min(self._trusted_proxies, len(parts))]
        return request.client.host if request.client else "unknown"

    def _evict_stale(self, window_start: float) -> None:
        # Drop only inactive IPs (newest hit outside the window), so active
        # clients — including abusers — keep their counts. (A flood of >max_tracked
        # simultaneously-active IPs would need a shared store like Redis.)
        stale = [
            ip
            for ip, hits in self._requests.items()
            if not hits or hits[-1] <= window_start
        ]
        for ip in stale:
            del self._requests[ip]

    async def dispatch(self, request: Request, call_next):
        ip = self._client_ip(request)
        now = time()
        window_start = now - self.period

        recent = [t for t in self._requests.get(ip, ()) if t > window_start]
        if len(recent) >= self.calls:
            self._requests[ip] = recent
            return JSONResponse(
                status_code=429,
                content={
                    "error": "RATE_LIMIT_EXCEEDED",
                    "message": "Too many requests",
                    "status": "error",
                },
            )

        recent.append(now)
        self._requests[ip] = recent

        if len(self._requests) > self._max_tracked:
            self._evict_stale(window_start)

        return await call_next(request)


@asynccontextmanager
async def lifespan(__app: FastAPI):
    websocket_manager.start_listener()
    yield
    await websocket_manager.stop_listener()
    close_pool()


class PrettyJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            separators=(", ", ": "),
        ).encode("utf-8")


app = FastAPI(
    title="TopicStreams API",
    lifespan=lifespan,
    default_response_class=PrettyJSONResponse,
)

app.add_middleware(
    RateLimitMiddleware,
    calls=120,
    period=60,
    trusted_proxies=settings.trusted_proxy_count,
)

# Added last so it's outermost — measures the full request, including the
# rate-limiter short-circuit.
app.add_middleware(TimingMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files for the web UI
static_dir = Path("/app/api/static")  # In-container path
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


# Root endpoint to serve the web UI
@app.get("/")
async def read_root():
    """Serve the main web UI page."""
    index_file = static_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse(
        status_code=404,
        content={"error": "Web UI not found", "message": "Static files not available"},
    )


app.include_router(v1_router)


@app.exception_handler(TopicStreamsException)
async def topicstreams_exception_handler(
    __request: Request, exc: TopicStreamsException
):
    logger.error(f"TopicStreamsException: {exc.message} (Error code: {exc.error_code})")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "error": exc.error_code or "UNKNOWN_ERROR",
            "message": exc.message,
            "status": "error",
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(__request: Request, exc: RequestValidationError):
    logger.error(f"Validation error: {exc.errors()}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "VALIDATION_ERROR",
            "message": "Invalid request parameters",
            "details": exc.errors(),
            "status": "error",
        },
    )


@app.exception_handler(Exception)
async def general_exception_handler(__request: Request, exc: Exception):
    logger.error(f"Unexpected error: {type(exc).__name__}: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "INTERNAL_SERVER_ERROR",
            "message": "An unexpected error occurred",
            "status": "error",
        },
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=settings.api_port)
