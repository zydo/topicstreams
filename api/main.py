"""FastAPI application entry point for the TopicStreams news aggregation API.

This module sets up the FastAPI application with custom JSON formatting,
database lifecycle management, and API v1 routes.
"""

import json
import logging
import uvicorn
from collections import defaultdict
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
from common.settings import settings
from .exceptions import TopicStreamsException
from .v1.router import router as v1_router
from .v1.websocket.manager import manager as websocket_manager

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window in-memory rate limiter keyed by client IP."""

    def __init__(self, app, calls: int = 120, period: int = 60):
        super().__init__(app)
        self.calls = calls
        self.period = period
        self._requests: dict = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        ip = request.client.host if request.client else "unknown"
        now = time()
        window_start = now - self.period

        hits = self._requests[ip]
        # Evict timestamps outside the current window
        self._requests[ip] = [t for t in hits if t > window_start]

        if len(self._requests[ip]) >= self.calls:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "RATE_LIMIT_EXCEEDED",
                    "message": "Too many requests",
                    "status": "error",
                },
            )

        self._requests[ip].append(now)

        # Prevent unbounded dict growth if many unique IPs never return
        if len(self._requests) > 10000:
            self._requests.clear()

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

app.add_middleware(RateLimitMiddleware, calls=120, period=60)

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
