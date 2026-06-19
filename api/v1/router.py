"""Main router for API v1, combining all v1 endpoints."""

from fastapi import APIRouter, Depends

from api.auth import require_api_key

from . import topics, news, logs, status, metrics, config
from .websocket import news as websocket_news

router = APIRouter(prefix="/api/v1")

# Bearer-token auth applied to every REST router. It's a no-op until
# TOPICSTREAMS_API_KEY is set (dev mode), then guards all these endpoints.
_auth = [Depends(require_api_key)]

router.include_router(topics.router, dependencies=_auth)
router.include_router(news.router, dependencies=_auth)
router.include_router(logs.router, dependencies=_auth)
router.include_router(status.router, dependencies=_auth)
router.include_router(metrics.router, dependencies=_auth)
router.include_router(config.router, dependencies=_auth)
# WebSocket auth is deferred (see TODO.md) — left unprotected for now.
router.include_router(websocket_news.router)
