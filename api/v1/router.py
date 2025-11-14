"""Main router for API v1, combining all v1 endpoints."""

from fastapi import APIRouter

from . import topics, news, logs
from .websocket import news as websocket_news


router = APIRouter(prefix="/api/v1")

router.include_router(topics.router)
router.include_router(news.router)
router.include_router(logs.router)
router.include_router(websocket_news.router)
