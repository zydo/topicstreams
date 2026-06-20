"""Per-engine idle keep-alive heartbeat.

On-demand web search reuses each engine's existing warm news Chromium session.
That session goes *cold* when it makes no recent successful requests — no
tracked topics, sparse topics, a fresh deploy, or just after a cooldown — and a
web search arriving on a cold session is the worst case for getting CAPTCHA'd
(see ``docs/WEB_SEARCH_WARMUP.md``).

This component is the timer + query rotation for the warm-up the worker fires
when it would otherwise be idle: at startup, then every ``interval`` seconds
(with jitter) of inactivity. It deliberately knows nothing about scraping,
pacing, or cooldown — the worker owns those — so it's a pure, clock-injectable
unit. The actual request it triggers must use the **NEWS** vertical: news passes
cold but the default web endpoint does not, so a web-vertical heartbeat on a
cold session would itself CAPTCHA (chicken-and-egg).

Idle-only: any real news scrape or web search calls ``record_activity`` to push
the next heartbeat out, so in the common case (topics present) it rarely fires —
a near-zero-overhead safety net, not a constant tax on the shared pace budget.
"""

import random
import time
from typing import Callable, Sequence

# Generic, always-has-results queries — nothing topical or sensitive that would
# pollute intent signals on the shared profile.
DEFAULT_QUERIES: tuple[str, ...] = (
    "weather tomorrow",
    "local news",
    "breaking news",
    "sports scores",
    "movie times",
)


class KeepAliveHeartbeat:
    """Idle timer that decides *when* to warm a session and *what* benign query
    to use, with both the clock and RNG injectable for tests.

    The heartbeat and any real request are equivalent for warmth, so a single
    ``record_activity`` resets the timer for both: the worker calls it after
    every real scrape *and* after firing a heartbeat. The timer starts already
    due, so the first ``due()`` after construction fires the startup warm-up.
    """

    def __init__(
        self,
        interval: float,
        jitter_ratio: float = 0.5,
        queries: Sequence[str] = DEFAULT_QUERIES,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ):
        if not queries:
            raise ValueError("keep-alive needs at least one query")
        self._interval = max(0.0, interval)
        self._jitter_ratio = max(0.0, jitter_ratio)
        self._clock = clock
        self._rng = rng or random
        # Shuffle once so a restart doesn't open with the same query every time;
        # the cursor then rotates through the whole set with no repeats.
        self._queries = list(queries)
        self._rng.shuffle(self._queries)
        self._cursor = 0
        # Start due: the first check fires the startup warm-up.
        self._next_fire = self._clock()

    def due(self) -> bool:
        """True once the session has been idle past the (jittered) interval."""
        return self._clock() >= self._next_fire

    def seconds_until_due(self) -> float:
        """How long until the next heartbeat is due (0.0 if already due). Lets
        the worker bound its idle sleep so a heartbeat isn't slept through."""
        return max(0.0, self._next_fire - self._clock())

    def next_query(self) -> str:
        """The next benign query, rotating through the shuffled set."""
        query = self._queries[self._cursor % len(self._queries)]
        self._cursor += 1
        return query

    def record_activity(self) -> None:
        """Reset the idle timer one (jittered) interval into the future.

        Called after a real scrape *and* after a fired heartbeat — both keep the
        session warm, so either defers the next heartbeat."""
        delay = self._interval * (1.0 + self._rng.uniform(0.0, self._jitter_ratio))
        self._next_fire = self._clock() + delay
