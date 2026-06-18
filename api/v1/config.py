"""Frontend runtime configuration.

Exposes the handful of settings the UI needs (feed page size, status poll
cadence, WebSocket reconnect backoff) so they're tunable from the environment
without rebuilding the frontend. The app fetches this once at startup
(see api/static/app.js); a failed fetch leaves the UI on its baked-in defaults.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from common.settings import settings

router = APIRouter(prefix="/config", tags=["config"])


class ConfigResponse(BaseModel):
    feed_page_size: int = Field(..., description="Default feed page size for the UI")
    status_poll_interval_ms: int = Field(
        ..., description="UI status-strip refresh interval (ms)"
    )
    ws_reconnect_base_ms: int = Field(
        ..., description="WebSocket reconnect backoff base (ms)"
    )
    ws_reconnect_max_ms: int = Field(
        ..., description="WebSocket reconnect backoff cap (ms)"
    )


@router.get("")
async def get_config() -> ConfigResponse:
    return ConfigResponse(
        feed_page_size=settings.feed_page_size,
        status_poll_interval_ms=settings.status_poll_interval_ms,
        ws_reconnect_base_ms=settings.ws_reconnect_base_ms,
        ws_reconnect_max_ms=settings.ws_reconnect_max_ms,
    )
