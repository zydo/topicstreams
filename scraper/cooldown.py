"""Per-engine adaptive cooldown for the scraper orchestrator.

When a search engine starts throttling (HTTP 429/403/503 or a detected block
page), continuing to hit it every cycle wastes requests and tends to *re-arm*
the rate-limit window (observed 2026-06-17: Brave 429'd every request for ~3h
while the other engines stayed fresh). ``EngineCooldownTracker`` watches each
engine's scrape outcomes and, after a block, benches that engine for an
exponentially growing window (capped). When the window expires it permits a
single *probe* request: a clean probe lifts the cooldown, another block deepens
it.

State is intentionally in-process and lives for the lifetime of the scraper
loop. It is consulted and updated synchronously inside ``scrape_topic`` (see
scraper.py), so a probe sent for one topic immediately governs the next topic in
the same cycle — that synchrony is what keeps a probe to a *single* request per
window even across many topics. The per-engine *health label* shown in the UI is
derived separately from persisted ``scraper_logs``
(``api/v1/metrics.classify_engine``); this tracker only decides whether to send
the next request.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Literal

from common.block_signals import is_network_block
from common.model import ScraperLog

logger = logging.getLogger(__name__)

# HTTP statuses treated as throttle/block signals. Mirrors
# api/v1/metrics._BLOCK_STATUSES and anti_detection.monitored_http_codes.
BLOCK_STATUSES = frozenset({429, 403, 503})

Outcome = Literal["block", "ok", "other"]
Decision = Literal["run", "probe", "skip"]


@dataclass(frozen=True)
class CooldownSnapshot:
    """One engine's cooldown state, serialized for cross-process consumption.

    ``remaining_seconds`` is the wall-clock-equivalent of the tracker's internal
    monotonic ``next_probe`` at snapshot time, so a reader in another process
    (the API) can turn it into an absolute timestamp.
    """

    engine: str
    failures: int
    remaining_seconds: float


def _is_block_message(log: ScraperLog) -> bool:
    # detect_block / redirected_off_results record "<engine> blocked: <reason>";
    # a connection-level teardown (e.g. Yahoo's ERR_CONNECTION_CLOSED) carries no
    # HTTP status but is equally a block, so fold it in here too.
    message = log.error_message or ""
    return "block" in message.lower() or is_network_block(message)


def classify_logs(logs: list[ScraperLog]) -> Outcome:
    """Reduce one engine's per-page logs to a single cooldown-relevant outcome.

    'block' wins over everything (a throttle/block signal anywhere in the run),
    then 'ok' if any page succeeded, else 'other' (a transient, non-block
    failure such as a navigation timeout, which we neither punish nor reward).
    """
    blocked = any(
        log.http_status_code in BLOCK_STATUSES or _is_block_message(log) for log in logs
    )
    if blocked:
        return "block"
    if any(log.success for log in logs):
        return "ok"
    return "other"


@dataclass
class _EngineState:
    failures: int = 0  # consecutive block count (0 == not cooling)
    next_probe: float = 0.0  # monotonic time the next request is allowed


@dataclass
class EngineCooldownTracker:
    """Adaptive, per-engine request gate. See the module docstring.

    ``base_seconds`` is the window after the first block; each further block
    doubles it up to ``max_seconds``. ``_clock`` is injectable for tests.
    """

    base_seconds: float
    max_seconds: float
    _clock: Callable[[], float] = time.monotonic
    _state: dict[str, _EngineState] = field(default_factory=dict)

    def _window_for(self, failures: int) -> float:
        """Exponential backoff from base, capped: base * 2**(failures-1)."""
        window = self.base_seconds * (2 ** max(0, failures - 1))
        return min(self.max_seconds, window)

    def decide(self, engine: str) -> Decision:
        """Whether to send this engine's next request: run / probe / skip."""
        state = self._state.get(engine)
        if state is None or state.failures == 0:
            return "run"
        if self._clock() >= state.next_probe:
            return "probe"
        return "skip"

    def remaining(self, engine: str) -> float:
        """Seconds until the engine's cooldown window allows a probe (0 if not
        cooling)."""
        state = self._state.get(engine)
        if state is None or state.failures == 0:
            return 0.0
        return max(0.0, state.next_probe - self._clock())

    def record(self, engine: str, logs: list[ScraperLog]) -> None:
        """Fold an engine's scrape outcome into its cooldown state."""
        outcome = classify_logs(logs)
        state = self._state.setdefault(engine, _EngineState())

        if outcome == "block":
            state.failures += 1
            window = self._window_for(state.failures)
            state.next_probe = self._clock() + window
            logger.warning(
                f"{engine}: block signal (#{state.failures}) — "
                f"cooling down {window:.0f}s before the next probe"
            )
        elif outcome == "ok":
            if state.failures:
                logger.info(f"{engine}: probe succeeded — cooldown cleared")
            state.failures = 0
            state.next_probe = 0.0
        elif state.failures:
            # A non-block failure during a probe (the only way we run while
            # cooling). Don't deepen — it isn't a throttle signal — but re-arm
            # the same window so a flaky probe doesn't become a per-topic retry
            # storm across the rest of the cycle.
            state.next_probe = self._clock() + self._window_for(state.failures)

    def snapshot(self) -> list[CooldownSnapshot]:
        """Current state of every tracked engine, for persistence (see
        db.upsert_engine_cooldowns). Engines never seen are simply absent."""
        return [
            CooldownSnapshot(
                engine=engine,
                failures=state.failures,
                remaining_seconds=self.remaining(engine),
            )
            for engine, state in self._state.items()
        ]
