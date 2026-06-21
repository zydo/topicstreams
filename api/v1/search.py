"""On-demand web search endpoint (WEB vertical).

Mounts the cross-process web-search bridge as a bearer-authed REST endpoint:
``GET /api/v1/search?q=<query>``. The query is dispatched to a healthy engine
(``api/websearch.dispatch_web_search``), which falls back across engines on a
block / empty / timeout; results are returned, not persisted.

Why REST and not the feed WebSocket: a web search is synchronous request/response
and spends scraper budget, so it belongs on the authenticated REST surface — not
the (deliberately unauthenticated, push-oriented) ``/ws/news`` feed channel.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from api.websearch import dispatch_web_search
from common.config import scraper_config
from common.model import WebResult

router = APIRouter(prefix="/search", tags=["search"])

# Non-success dispatch outcomes → HTTP status. ``ok``/``empty`` are 200 (the
# search ran; empty just found nothing); the rest are upstream/engine failures.
_ERROR_STATUS = {
    "unavailable": 503,  # no healthy engine to try (all cooling / down)
    "timeout": 504,  # no engine answered within the request timeout
    "blocked": 502,  # every attempted engine was blocked / CAPTCHA'd
    "error": 502,  # every attempted engine errored
    "cooling": 502,  # engine benched (normally filtered out before dispatch)
}


class WebSearchResponse(BaseModel):
    query: str
    status: str = Field(
        ..., description="ok | empty (both HTTP 200); failures surface as HTTP status"
    )
    engine: str | None = Field(
        None, description="Engine that produced the results (when ok)"
    )
    attempts: list[str] = Field(
        default_factory=list, description="Engines tried, in order"
    )
    results: list[WebResult] = Field(
        default_factory=list, description="Ranked web results (most-relevant first)"
    )


@router.get("")
async def web_search(
    q: str = Query(..., min_length=1, max_length=256, description="Search query"),
    vertical: str = Query(
        "web", description="Search vertical (only 'web' is supported on demand)"
    ),
) -> WebSearchResponse:
    if vertical != "web":
        raise HTTPException(
            status_code=400,
            detail=f"unsupported vertical '{vertical}'; only 'web' is available",
        )
    if not scraper_config.web_search_enabled:
        raise HTTPException(status_code=503, detail="on-demand web search is disabled")

    result = await dispatch_web_search(q)

    code = _ERROR_STATUS.get(result.status)
    if code is not None:
        # Every attempted engine failed (or none was available); surface why.
        raise HTTPException(
            status_code=code,
            detail={
                "query": result.query,
                "status": result.status,
                "attempts": result.attempts,
            },
        )
    return WebSearchResponse(
        query=result.query,
        status=result.status,
        engine=result.engine,
        attempts=result.attempts,
        results=result.results,
    )
