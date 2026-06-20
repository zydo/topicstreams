"""Operational metrics for monitoring the scraper and feed.

The response is a superset of the original lightweight summary: the four scalar
fields (``active_topics``, ``total_news``, ``scrape_success_rate``,
``feed_freshness_seconds``) are kept for back-compat, and a per-engine
breakdown plus recent cycles/failures are added to power the ``/monitor`` ops
page. One call fetches everything the page needs.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from common import database as db
from common.block_signals import is_network_block

router = APIRouter(prefix="/metrics", tags=["metrics"])

# Display-only defaults (not lifted to config — the window is a query param, and
# these counts just bound how much history the page renders).
_DEFAULT_WINDOW_SECONDS = 3600  # 1h
_RECENT_CYCLES = 24
_RECENT_FAILURES = 15

# HTTP statuses treated as throttle/block signals (rate-limit / forbidden /
# unavailable). Drives the per-engine "blocked" health label.
_BLOCK_STATUSES = (429, 403, 503)

# A persisted cooldown snapshot older than this is ignored: the scraper writes
# one every cycle, so a stale snapshot means the scraper is down (the cycle
# timeline surfaces that) and the cooldown countdown is meaningless. Generous
# vs. the scrape interval so a slow cycle doesn't blink the indicator off.
_COOLDOWN_STALE_SECONDS = 600


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _rate(successes: int, scrapes: int) -> float | None:
    return round(successes / scrapes, 4) if scrapes else None


def classify_engine(row: dict) -> str:
    """Per-engine health label from an aggregate row.

    idle    - no scrapes in the window
    blocked - the most recent scrape was a throttle/block: a 429/403/503 status,
              or a connection-level teardown with no HTTP status (e.g. Yahoo's
              ERR_CONNECTION_CLOSED — see common/block_signals.py)
    parsing - sustained selector rot: every successful scrape parsed nothing
              (>=3 scrapes, >=1 success, all successes 0-entry)
    degraded- success rate below 0.75 (includes a total failure: 0%)
    healthy - otherwise

    Heuristic by design — the raw counts (success rate, zero-parse, block count,
    last status) are always shown alongside the label, so it is a triage hint,
    not the whole story.
    """
    scrapes = row["scrapes"]
    if scrapes == 0:
        return "idle"
    if row.get("last_success") is False and (
        row.get("last_http_status") in _BLOCK_STATUSES
        or is_network_block(row.get("last_error_message"))
    ):
        return "blocked"
    successes = row["successes"]
    if successes > 0 and row["zero_parse"] >= successes and scrapes >= 3:
        return "parsing"
    rate = successes / scrapes
    return "degraded" if rate < 0.75 else "healthy"


def _live_cooldown_seconds(cd: dict | None, now: datetime) -> float | None:
    """Seconds until the next probe if this engine is *currently* benched.

    None when not cooling, when the snapshot is stale (scraper likely down), or
    when the probe is already due (remaining <= 0) — in those cases the
    log-derived health label stands on its own.
    """
    if not cd or cd["failures"] <= 0 or cd["next_probe_at"] is None:
        return None
    if (now - cd["updated_at"]).total_seconds() > _COOLDOWN_STALE_SECONDS:
        return None
    remaining = (cd["next_probe_at"] - now).total_seconds()
    return remaining if remaining > 0 else None


def _empty_engine_row(engine: str) -> dict:
    """A zeroed aggregate row for an engine with no scrapes in the window.

    Lets an engine that is benched (hence producing no logs) still appear on the
    monitor — otherwise a long cooldown would make it vanish from the table.
    """
    return {
        "engine": engine,
        "scrapes": 0,
        "successes": 0,
        "entries_parsed": 0,
        "zero_parse": 0,
        "failures": 0,
        "blocked": 0,
        "avg_latency_ms": None,
        "p50_latency_ms": None,
        "p95_latency_ms": None,
        "last_scrape_at": None,
        "last_success": None,
        "last_http_status": None,
        "last_error_message": None,
        "http_status_breakdown": {},
    }


class OverallMetrics(BaseModel):
    scrapes: int = Field(..., description="Page-attempts in the window")
    successes: int = Field(..., description="Successful page-attempts")
    success_rate: float | None = Field(
        None, description="Fraction of scrapes that succeeded (null if none)"
    )
    entries_parsed: int = Field(..., description="Total items parsed")
    zero_parse: int = Field(
        ..., description="Successful scrapes that parsed 0 items (selector-rot signal)"
    )
    failures: int = Field(..., description="Failed page-attempts")
    blocked: int = Field(..., description="Failures with a 429/403/503 status")
    avg_latency_ms: int | None = Field(None, description="Mean fetch latency (ms)")
    p50_latency_ms: int | None = Field(None, description="Median fetch latency (ms)")
    p95_latency_ms: int | None = Field(None, description="p95 fetch latency (ms)")
    last_scrape_at: datetime | None = Field(None, description="Newest scrape in window")


class EngineMetrics(BaseModel):
    engine: str = Field(..., description="Search engine name")
    health: str = Field(
        ...,
        description=(
            "healthy | degraded | blocked | parsing | idle (see classify_engine), "
            "or cooldown when the scraper has the engine benched"
        ),
    )
    scrapes: int
    successes: int
    success_rate: float | None
    entries_parsed: int
    zero_parse: int
    failures: int
    blocked: int
    avg_latency_ms: int | None
    p50_latency_ms: int | None
    p95_latency_ms: int | None
    last_scrape_at: datetime | None
    last_success: bool | None = Field(None, description="Outcome of the newest scrape")
    last_http_status: int | None = Field(
        None, description="HTTP status of the newest scrape"
    )
    http_status_breakdown: dict[str, int] = Field(
        ..., description="Counts per monitored HTTP status (200/429/403/503)"
    )
    cooldown_seconds_remaining: float | None = Field(
        None,
        description="Seconds until the scraper next probes a benched engine "
        "(null when not cooling down)",
    )
    cooldown_failures: int = Field(
        0,
        description="Consecutive block signals driving the current cooldown (0 if none)",
    )


class CycleMetrics(BaseModel):
    started_at: datetime
    finished_at: datetime
    duration_seconds: float = Field(..., description="Wall-clock for the full pass")
    topics_count: int
    entries_parsed: int
    new_events: int = Field(..., description="New feed events filed this cycle")
    success: bool
    error: str | None = None
    engine: str | None = Field(
        None,
        description="Engine whose worker produced this sweep (None for legacy rows)",
    )


class FailureRow(BaseModel):
    topic: str
    engine: str
    scraped_at: datetime
    http_status_code: int | None
    error_message: str | None
    entry_count: int
    duration_ms: int | None


class MetricsResponse(BaseModel):
    # Original lightweight fields (kept for back-compat).
    active_topics: int = Field(..., description="Number of watched (active) topics")
    total_news: int = Field(..., description="Total feed events across active topics")
    scrape_success_rate: float | None = Field(
        None, description="Overall scrape success rate in the window (null if none)"
    )
    feed_freshness_seconds: float | None = Field(
        None, description="Age of the newest feed event in seconds (null if empty)"
    )
    # Rich dashboard payload.
    generated_at: datetime = Field(
        ..., description="When this response was assembled (UTC)"
    )
    window_seconds: int = Field(..., description="Aggregation window actually used")
    overall: OverallMetrics
    engines: list[EngineMetrics] = Field(
        ..., description="Per-engine aggregates, engine name A→Z"
    )
    recent_cycles: list[CycleMetrics] = Field(
        ..., description="Newest-first cycle summaries"
    )
    recent_failures: list[FailureRow] = Field(
        ..., description="Newest-first failed scrapes"
    )


def _build_overall(row: dict) -> OverallMetrics:
    return OverallMetrics(
        scrapes=row["scrapes"],
        successes=row["successes"],
        success_rate=_rate(row["successes"], row["scrapes"]),
        entries_parsed=row["entries_parsed"],
        zero_parse=row["zero_parse"],
        failures=row["failures"],
        blocked=row["blocked"],
        avg_latency_ms=row["avg_latency_ms"],
        p50_latency_ms=row["p50_latency_ms"],
        p95_latency_ms=row["p95_latency_ms"],
        last_scrape_at=row["last_scrape_at"],
    )


def _build_engine(row: dict, cooldown: dict | None = None) -> EngineMetrics:
    """Shape one engine row. ``cooldown`` is its live ``{failures, remaining}``
    when currently benched, which overrides the log-derived health label."""
    health = classify_engine(row)
    cooldown_remaining = None
    cooldown_failures = 0
    if cooldown is not None:
        cooldown_remaining = cooldown["remaining"]
        cooldown_failures = cooldown["failures"]
        # The engine is benched right now; that supersedes a stale log-based
        # label (often "idle" — no logs are produced while skipped).
        health = "cooldown"
    return EngineMetrics(
        engine=row["engine"],
        health=health,
        cooldown_seconds_remaining=cooldown_remaining,
        cooldown_failures=cooldown_failures,
        scrapes=row["scrapes"],
        successes=row["successes"],
        success_rate=_rate(row["successes"], row["scrapes"]),
        entries_parsed=row["entries_parsed"],
        zero_parse=row["zero_parse"],
        failures=row["failures"],
        blocked=row["blocked"],
        avg_latency_ms=row["avg_latency_ms"],
        p50_latency_ms=row["p50_latency_ms"],
        p95_latency_ms=row["p95_latency_ms"],
        last_scrape_at=row["last_scrape_at"],
        last_success=row["last_success"],
        last_http_status=row["last_http_status"],
        http_status_breakdown=row["http_status_breakdown"],
    )


@router.get("")
async def get_metrics(
    window: int = Query(
        _DEFAULT_WINDOW_SECONDS,
        ge=60,
        le=7 * 24 * 3600,
        description="Aggregation window in seconds (1 minute..7 days)",
    ),
) -> MetricsResponse:
    topics = await run_in_threadpool(db.get_topics)
    total_news = await run_in_threadpool(db.get_active_feed_count)
    freshness = await run_in_threadpool(db.get_feed_freshness_seconds)
    metrics = await run_in_threadpool(db.get_scrape_metrics, window)
    cycles = await run_in_threadpool(db.get_recent_cycles, _RECENT_CYCLES)
    failures = await run_in_threadpool(db.get_recent_scrape_failures, _RECENT_FAILURES)
    cooldowns = await run_in_threadpool(db.get_engine_cooldowns)

    overall = _build_overall(metrics["overall"])

    # Resolve which engines are *currently* benched (fresh snapshot, probe still
    # in the future), then overlay that onto the per-engine rows.
    now = _naive_utc_now()
    live = {
        engine: {"remaining": remaining, "failures": cd["failures"]}
        for engine, cd in cooldowns.items()
        if (remaining := _live_cooldown_seconds(cd, now)) is not None
    }
    rows = {row["engine"]: row for row in metrics["engines"]}
    # A long cooldown means no scrapes in the window, so a benched engine may be
    # absent from the aggregates entirely — synthesize a row so it still shows.
    for engine in live:
        rows.setdefault(engine, _empty_engine_row(engine))
    engines = [_build_engine(rows[engine], live.get(engine)) for engine in sorted(rows)]

    return MetricsResponse(
        active_topics=len(topics),
        total_news=total_news,
        scrape_success_rate=overall.success_rate,
        feed_freshness_seconds=freshness,
        generated_at=_naive_utc_now(),
        window_seconds=window,
        overall=overall,
        engines=engines,
        recent_cycles=[CycleMetrics(**c) for c in cycles],
        recent_failures=[FailureRow(**f) for f in failures],
    )
