"""Exit-IP saturation signal for the per-engine worker pool.

Each engine runs in its own worker on a *shared* exit IP (see scraper/main.py).
When an engine starts throttling it benches itself via the cooldown tracker
(scraper/cooldown.py). The question this module answers is the operational one:
*does the cooling mean the IP is out of capacity, or is it just one strict
engine?*

Strict engines (Brave is the canonical one) trip far sooner than the rest, so
their cooling is a canary about that engine, not the IP. We therefore weight the
signal: the exit IP is "saturated" only when enough *robust* (non-canary)
engines are cooling at the same time. That is the cue to divide traffic across
machines with different IPs, rather than to slow everything down.

``SharedEngineState`` is the thread-safe hand-off between the engine workers
(writers, one engine each) and the main thread (reader: DB publish + this
signal).
"""

import logging
import threading
from dataclasses import dataclass

from .cooldown import CooldownSnapshot
from .tasks import SchedulerHealth

logger = logging.getLogger(__name__)

# An engine is "behind" when its oldest overdue topic is later than this many
# per-topic intervals — i.e. it's cycling topics materially slower than
# configured. A lagging capacity/throttle signal that complements the cooldown
# one (which fires on a hard block); see docs/TASK_SCHEDULER.md.
_BACKLOG_LATENESS_FACTOR = 3.0


class SharedEngineState:
    """Latest per-engine cooldown snapshot *and* scheduler-health read, written
    by each worker.

    One writer per engine (the worker owning it) and one reader (the main
    thread), so a single lock around two dicts is ample.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshots: dict[str, CooldownSnapshot] = {}
        self._health: dict[str, SchedulerHealth] = {}

    def update(self, snapshot: CooldownSnapshot) -> None:
        with self._lock:
            self._snapshots[snapshot.engine] = snapshot

    def all(self) -> list[CooldownSnapshot]:
        with self._lock:
            return list(self._snapshots.values())

    def update_health(self, engine: str, health: SchedulerHealth) -> None:
        with self._lock:
            self._health[engine] = health

    def health_all(self) -> dict[str, SchedulerHealth]:
        with self._lock:
            return dict(self._health)


@dataclass(frozen=True)
class SaturationVerdict:
    """Outcome of one saturation evaluation."""

    saturated: bool
    cooling_robust: list[str]  # robust engines currently cooling
    cooling_canary: list[str]  # canary engines currently cooling (informational)
    robust_threshold: int


def evaluate_saturation(
    snapshots: list[CooldownSnapshot],
    *,
    canary_engines: list[str],
    robust_threshold: int,
) -> SaturationVerdict:
    """Decide whether the exit IP looks saturated from the cooldown snapshots.

    An engine is "cooling" when it has at least one consecutive block on record
    (``failures > 0``). Canary engines are excluded from the count; saturation
    fires when the number of cooling *robust* engines reaches ``robust_threshold``.
    """
    canary = set(canary_engines)
    cooling = [s.engine for s in snapshots if s.failures > 0]
    cooling_robust = sorted(e for e in cooling if e not in canary)
    cooling_canary = sorted(e for e in cooling if e in canary)
    saturated = len(cooling_robust) >= robust_threshold
    return SaturationVerdict(
        saturated=saturated,
        cooling_robust=cooling_robust,
        cooling_canary=cooling_canary,
        robust_threshold=robust_threshold,
    )


def log_saturation(verdict: SaturationVerdict) -> None:
    """Emit a loud, actionable log line when the IP looks saturated.

    Kept separate from ``evaluate_saturation`` so the decision stays pure and
    testable; callers log at most once per evaluation tick.
    """
    if not verdict.saturated:
        return
    logger.warning(
        "EXIT IP SATURATION SUSPECTED — %d robust engines throttled (%s); "
        "threshold is %d. The shared exit IP is at capacity for this topic "
        "load; scale out to another machine/IP rather than slowing down.%s",
        len(verdict.cooling_robust),
        ", ".join(verdict.cooling_robust),
        verdict.robust_threshold,
        (
            f" (canaries also cooling: {', '.join(verdict.cooling_canary)})"
            if verdict.cooling_canary
            else ""
        ),
    )


@dataclass(frozen=True)
class BacklogVerdict:
    """One engine's queue-backlog health read."""

    engine: str
    overdue_count: int
    max_lateness_seconds: float
    behind: bool  # cycling topics materially slower than the configured interval


def evaluate_backlog(
    engine: str,
    health: SchedulerHealth,
    *,
    interval: float,
    lateness_factor: float = _BACKLOG_LATENESS_FACTOR,
) -> BacklogVerdict:
    """Read an engine's scheduler health as a backlog signal.

    ``behind`` is set when the oldest overdue topic is later than
    ``lateness_factor`` × the per-topic ``interval`` — the engine is falling
    behind its freshness cadence (capacity/throttle pressure), a *lagging*
    complement to the direct cooldown signal. Pure for testability.
    """
    behind = health.max_lateness_seconds > lateness_factor * interval
    return BacklogVerdict(
        engine=engine,
        overdue_count=health.overdue_count,
        max_lateness_seconds=health.max_lateness_seconds,
        behind=behind,
    )


def log_backlog(verdict: BacklogVerdict) -> None:
    """Warn when an engine is materially behind its scrape cadence."""
    if not verdict.behind:
        return
    logger.warning(
        "[%s] falling behind — %d topics overdue, oldest %.0fs late. The engine "
        "is cycling topics slower than its interval (pace floor/cooldown pressure "
        "or too many topics for one engine on this IP).",
        verdict.engine,
        verdict.overdue_count,
        verdict.max_lateness_seconds,
    )
