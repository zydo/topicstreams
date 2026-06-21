"""Configuration management for TopicStreams.

Loads the unified ``config.yml`` (at the repo root, alongside ``.env``) and
exposes its ``scraper:`` and ``anti_detection:`` sections. The API process reads
the same file's ``api:`` section via common.settings's YAML source.
"""

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

logger = logging.getLogger(__name__)

# Unified config file at the repo/container root (WORKDIR is /app in the image,
# so this resolves to /app/config.yml there and <repo>/config.yml locally).
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yml"


@dataclass
class FingerprintProfile:
    user_agent: str
    sec_ch_ua: str
    sec_ch_ua_platform: str


class _BaseConfig:
    """Base class for YAML config loaders."""

    _log_name: str

    def __init__(self, config_path: Path):
        self.config_path = config_path
        self._config: dict[str, Any] = {}
        self._load_config()

    def _load_config(self) -> None:
        if not self.config_path.exists():
            example_path = self.config_path.with_name(
                self.config_path.name + ".example"
            )
            if not example_path.exists():
                raise FileNotFoundError(
                    f"Config file not found: {self.config_path} "
                    f"(and no template at {example_path})"
                )
            shutil.copy(example_path, self.config_path)
            logger.warning(
                f"Config file {self.config_path} not found; "
                f"created it from {example_path}"
            )

        try:
            with open(self.config_path, "r") as f:
                self._config = yaml.safe_load(f) or {}
            logger.info(f"Loaded {self._log_name} config from {self.config_path}")
        except yaml.YAMLError as e:
            raise ValueError(f"Error parsing config file {self.config_path}: {e}")

    def _get(self, *keys: str, default: Any = None) -> Any:
        value = self._config
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
                if value is None:
                    return default
            else:
                return default
        return value if value is not None else default


class ScraperConfig(_BaseConfig):
    """Scraper configuration loader and accessor."""

    _log_name = "scraper"

    def __init__(self, config_path: Path = CONFIG_PATH):
        super().__init__(config_path)

    @property
    def scrape_interval(self) -> int:
        """Get scrape interval in seconds."""
        return self._get("scraper", "scrape_interval", default=60)

    @property
    def max_pages(self) -> int:
        """Get maximum pages to scrape per topic."""
        return self._get("scraper", "max_pages", default=1)

    @property
    def engines(self) -> list[str]:
        """Search-engine names to scrape, as a YAML list under ``engines``.

        In priority order (used by the ``fallback`` strategy). Defaults to
        ``['google']`` if unset.
        """
        return self._get("scraper", "engines", default=["google"])

    @property
    def engine_strategy(self) -> str:
        """How enabled engines are combined: 'all', 'fallback', or 'rotate'."""
        return self._get("scraper", "engine_strategy", default="fallback")

    @property
    def browser_recycle_cycles(self) -> int:
        """Recycle the Chromium context every N cycles to release memory."""
        return self._get("scraper", "browser_recycle_cycles", default=50)

    @property
    def cooldown_enabled(self) -> bool:
        """Whether adaptive per-engine cooldown is active (scraper/cooldown.py).

        When on, an engine that returns a throttle/block signal (429/403/503 or
        a detected block page) is benched for an exponential backoff window and
        probed once before resuming, instead of being hit every cycle.
        """
        return self._get("scraper", "cooldown", "enabled", default=True)

    @property
    def cooldown_base_seconds(self) -> float:
        """Backoff window after an engine's first block; doubles per block."""
        return self._get("scraper", "cooldown", "base_seconds", default=300)

    @property
    def cooldown_max_seconds(self) -> float:
        """Cap on the exponential cooldown window."""
        return self._get("scraper", "cooldown", "max_seconds", default=3600)

    # ----- Proactive per-engine pacing -----
    # Each engine runs in its own worker (see scraper/main.py) and paces *itself*
    # to a known-safe request rate. This is the primary throttle; cooldown is
    # only the reactive backstop for when the pace is misjudged. Operating at a
    # deliberate floor (rather than hammering until a 429) avoids promoting soft
    # throttles into hard CAPTCHA/IP-level blocks on the shared exit IP.

    @property
    def pacing_default_min_interval(self) -> float:
        """Floor on seconds between consecutive requests for one engine."""
        return self._get("scraper", "pacing", "default_min_interval", default=2.0)

    @property
    def pacing_per_engine(self) -> dict[str, float]:
        """Per-engine override of the min request interval (engine -> seconds).

        Some engines (notably Brave) throttle far sooner than others, so give
        them a longer floor instead of discovering it by getting blocked.
        """
        return self._get("scraper", "pacing", "per_engine", default={}) or {}

    @property
    def pacing_jitter_ratio(self) -> float:
        """Random fraction (0..1) added on top of each pace interval, so the
        cadence isn't perfectly regular (itself a bot signal)."""
        return self._get("scraper", "pacing", "jitter_ratio", default=0.25)

    def min_interval_for(self, engine: str) -> float:
        """Resolve the proactive pace floor for one engine."""
        return float(
            self.pacing_per_engine.get(engine, self.pacing_default_min_interval)
        )

    # ----- Idle keep-alive heartbeat (warms the session for web search) -----
    # Off by default: web search isn't wired to the API/WS yet, so until it is
    # the heartbeat would only add request load. Flip ``enabled`` on once web
    # search goes live so sessions stay warm between scrapes. See
    # docs/WEB_SEARCH_WARMUP.md and scraper/keepalive.py.

    @property
    def keepalive_enabled(self) -> bool:
        """Whether the per-engine idle keep-alive heartbeat fires."""
        return self._get("scraper", "keepalive", "enabled", default=False)

    @property
    def keepalive_interval_seconds(self) -> float:
        """Idle gap after which a keep-alive warm-up request fires (~10 min)."""
        return self._get("scraper", "keepalive", "interval_seconds", default=600)

    @property
    def keepalive_jitter_ratio(self) -> float:
        """Random fraction (0..1) added on top of the keep-alive interval."""
        return self._get("scraper", "keepalive", "jitter_ratio", default=0.5)

    @property
    def keepalive_queries(self) -> list[str] | None:
        """Optional override of the benign keep-alive query set (else the
        built-in defaults in scraper/keepalive.py are used)."""
        return self._get("scraper", "keepalive", "queries", default=None)

    # ----- On-demand web search (cross-process bridge) -----
    # When enabled, each engine worker drains a per-engine DB-backed queue
    # (scraper/webqueue.py, web_search_jobs) and the API dispatches user queries
    # to a healthy engine (api/websearch.py). Off by default so a deployment that
    # only wants the news feed doesn't poll the queue table.

    @property
    def web_search_enabled(self) -> bool:
        """Whether on-demand web search is served (per-engine queues + dispatcher)."""
        return self._get("scraper", "web_search", "enabled", default=False)

    @property
    def web_search_engines(self) -> list[str]:
        """Engines the web-search dispatcher may use, in priority order.

        Defaults to Google only (single-engine web search for now); add more here
        to turn on cross-engine fan-out/fallback. Each must also be a running
        ``scraper.engines`` worker so a worker drains its web-search queue.
        """
        return self._get("scraper", "web_search", "engines", default=["google"])

    @property
    def web_search_request_timeout_seconds(self) -> float:
        """How long the API waits for one engine to serve a query before it
        gives up on that engine and falls back to the next healthy one."""
        return self._get(
            "scraper", "web_search", "request_timeout_seconds", default=25.0
        )

    @property
    def web_search_max_in_flight(self) -> int:
        """Backpressure cap: max in-flight (pending+claimed) web-search jobs per
        engine. Each engine has ONE warm session serving searches strictly
        sequentially, so a deep queue just times out; instead, reject the N+1th
        request fast (HTTP 429) so the caller retries rather than hanging. Size it
        to how many serves fit in ``request_timeout`` (~6s each → ~4)."""
        return self._get("scraper", "web_search", "max_in_flight", default=4)

    @property
    def web_search_poll_interval_seconds(self) -> float:
        """How often the API polls the job row for its result while waiting."""
        return self._get("scraper", "web_search", "poll_interval_seconds", default=0.25)

    @property
    def web_search_max_engine_attempts(self) -> int:
        """Max distinct healthy engines a single query may fan out across before
        the dispatcher gives up (bounds the worst-case latency/cost)."""
        return self._get("scraper", "web_search", "max_engine_attempts", default=3)

    @property
    def web_search_job_ttl_seconds(self) -> float:
        """Age past which an abandoned job row is purged (producer crashed/timed
        out before deleting it). Generous vs. request_timeout so a slow-but-live
        request is never swept out from under itself."""
        return self._get("scraper", "web_search", "job_ttl_seconds", default=120)

    # ----- Weighted saturation signal (when to scale to another exit IP) -----

    @property
    def saturation_canary_engines(self) -> list[str]:
        """Engines excluded from the IP-saturation signal.

        Strict engines (e.g. Brave) trip first by nature, so their cooling is a
        canary about *that engine*, not evidence the exit IP is saturated.
        """
        return (
            self._get("scraper", "saturation", "canary_engines", default=["brave"])
            or []
        )

    @property
    def saturation_robust_threshold(self) -> int:
        """How many *robust* (non-canary) engines must be cooling at once before
        the exit IP is flagged as saturated (the signal to scale horizontally)."""
        return self._get("scraper", "saturation", "robust_threshold", default=2)


class AntiDetectionConfig(_BaseConfig):
    """Anti-detection configuration loader and accessor."""

    _log_name = "anti-detection"

    def __init__(self, config_path: Path = CONFIG_PATH):
        super().__init__(config_path)

    # ============================================
    # Strategy Enabled Checkers
    # ============================================

    @property
    def playwright_stealth_enabled(self) -> bool:
        """Check if playwright-stealth is enabled."""
        return self._get(
            "anti_detection", "playwright_stealth", "enabled", default=True
        )

    @property
    def random_delays_enabled(self) -> bool:
        """Check if random delays are enabled."""
        return self._get("anti_detection", "random_delays", "enabled", default=True)

    @property
    def captcha_detection_enabled(self) -> bool:
        """Check if CAPTCHA detection is enabled."""
        return self._get("anti_detection", "captcha_detection", "enabled", default=True)

    @property
    def http_error_handling_enabled(self) -> bool:
        """Check if HTTP error handling is enabled."""
        return self._get(
            "anti_detection", "http_error_handling", "enabled", default=True
        )

    # ============================================
    # Configuration Values
    # ============================================

    @property
    def browser_args(self) -> list[str]:
        """Get browser launch arguments."""
        return self._get(
            "anti_detection",
            "browser_args",
            "args",
            default=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )

    # ----- Page interaction timing (speed vs. block-risk) -----

    @property
    def nav_timeout_ms(self) -> int:
        """Navigation (page.goto) timeout in milliseconds."""
        return self._get(
            "anti_detection", "page_interaction", "nav_timeout_ms", default=30000
        )

    @property
    def selector_timeout_ms(self) -> int:
        """Timeout waiting for the results container, in milliseconds."""
        return self._get(
            "anti_detection", "page_interaction", "selector_timeout_ms", default=5000
        )

    @property
    def page_settle_min_ms(self) -> int:
        """Minimum post-load settle wait, in milliseconds."""
        return self._get(
            "anti_detection", "page_interaction", "settle_min_ms", default=1500
        )

    @property
    def page_settle_max_ms(self) -> int:
        """Maximum post-load settle wait, in milliseconds."""
        return self._get(
            "anti_detection", "page_interaction", "settle_max_ms", default=3000
        )

    # ----- Human-simulation jitter (scroll/mouse after the page settles) -----
    # Cosmetic anti-bot motion; trades a little speed for looking less robotic.
    # Each range mirrors a random.randint(lo, hi) call in scraper.py, and the
    # defaults reproduce the prior hardcoded behaviour exactly.

    def _human_sim(self, key: str, default: Any) -> Any:
        """Read one knob under anti_detection.page_interaction.human_simulation."""
        return self._get(
            "anti_detection",
            "page_interaction",
            "human_simulation",
            key,
            default=default,
        )

    @property
    def scroll_steps_min(self) -> int:
        return self._human_sim("scroll_steps_min", 2)

    @property
    def scroll_steps_max(self) -> int:
        return self._human_sim("scroll_steps_max", 4)

    @property
    def scroll_distance_min(self) -> int:
        return self._human_sim("scroll_distance_min", 80)

    @property
    def scroll_distance_max(self) -> int:
        return self._human_sim("scroll_distance_max", 250)

    @property
    def scroll_wait_min(self) -> int:
        return self._human_sim("scroll_wait_min", 300)

    @property
    def scroll_wait_max(self) -> int:
        return self._human_sim("scroll_wait_max", 800)

    @property
    def mouse_x_min(self) -> int:
        return self._human_sim("mouse_x_min", 200)

    @property
    def mouse_x_max(self) -> int:
        return self._human_sim("mouse_x_max", 1700)

    @property
    def mouse_y_min(self) -> int:
        return self._human_sim("mouse_y_min", 200)

    @property
    def mouse_y_max(self) -> int:
        return self._human_sim("mouse_y_max", 800)

    @property
    def mouse_steps_min(self) -> int:
        return self._human_sim("mouse_steps_min", 5)

    @property
    def mouse_steps_max(self) -> int:
        return self._human_sim("mouse_steps_max", 15)

    @property
    def scroll_back_chance(self) -> float:
        """Probability of a small upward scroll after the mouse move (0..1)."""
        return self._human_sim("scroll_back_chance", 0.5)

    @property
    def scroll_back_distance_min(self) -> int:
        return self._human_sim("scroll_back_distance_min", 30)

    @property
    def scroll_back_distance_max(self) -> int:
        return self._human_sim("scroll_back_distance_max", 100)

    @property
    def scroll_back_wait_min(self) -> int:
        return self._human_sim("scroll_back_wait_min", 200)

    @property
    def scroll_back_wait_max(self) -> int:
        return self._human_sim("scroll_back_wait_max", 500)

    @property
    def random_delay_min(self) -> float:
        """Get minimum random delay in seconds."""
        return self._get("anti_detection", "random_delays", "min_seconds", default=2)

    @property
    def random_delay_max(self) -> float:
        """Get maximum random delay in seconds."""
        return self._get("anti_detection", "random_delays", "max_seconds", default=5)

    @property
    def viewport_width(self) -> int:
        """Get browser viewport width."""
        return self._get(
            "anti_detection", "browser_fingerprint", "viewport_width", default=1920
        )

    @property
    def viewport_height(self) -> int:
        """Get browser viewport height."""
        return self._get(
            "anti_detection", "browser_fingerprint", "viewport_height", default=1080
        )

    @property
    def locale(self) -> str:
        """Get browser locale."""
        return self._get(
            "anti_detection", "browser_fingerprint", "locale", default="en-US"
        )

    @property
    def timezone_id(self) -> str:
        """Get browser timezone ID."""
        return self._get(
            "anti_detection",
            "browser_fingerprint",
            "timezone_id",
            default="America/Los_Angeles",
        )

    @property
    def geolocation_latitude(self) -> float:
        """Get geolocation latitude."""
        return self._get(
            "anti_detection",
            "browser_fingerprint",
            "geolocation_latitude",
            default=37.3273,
        )

    @property
    def geolocation_longitude(self) -> float:
        """Get geolocation longitude."""
        return self._get(
            "anti_detection",
            "browser_fingerprint",
            "geolocation_longitude",
            default=-121.954,
        )

    @property
    def color_scheme(self) -> Literal["dark", "light", "no-preference", "null"]:
        """Get browser color scheme."""
        return self._get(
            "anti_detection", "browser_fingerprint", "color_scheme", default="light"
        )

    @property
    def permissions(self) -> list[str]:
        """Get browser permissions."""
        return self._get(
            "anti_detection",
            "browser_fingerprint",
            "permissions",
            default=["geolocation"],
        )

    @property
    def captcha_keywords(self) -> list[str]:
        """Get CAPTCHA detection keywords."""
        return self._get(
            "anti_detection",
            "captcha_detection",
            "keywords",
            default=["captcha", "unusual traffic"],
        )

    @property
    def monitored_http_codes(self) -> list[int]:
        """Get monitored HTTP error codes."""
        return self._get(
            "anti_detection",
            "http_error_handling",
            "monitored_codes",
            default=[429, 403, 503],
        )

    @property
    def http_headers(self) -> dict[str, str]:
        """Get HTTP headers."""
        return self._get(
            "anti_detection",
            "http_headers",
            "headers",
            default={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )

    @property
    def proxy_enabled(self) -> bool:
        # Setting SCRAPER_PROXY in the environment implicitly enables proxying,
        # so credentials can be supplied via .env without rebuilding the image
        # (config.yml is baked into the images at build time).
        if os.environ.get("SCRAPER_PROXY", "").strip():
            return True
        return self._get("anti_detection", "proxy", "enabled", default=False)

    @property
    def proxy_servers(self) -> list[str]:
        """Proxy URLs (scheme://[user:pass@]host:port).

        The SCRAPER_PROXY environment variable (a single URL) takes precedence
        over the YAML ``proxies`` list, which is the convenient path for Docker
        since the scraper reads .env at runtime.
        """
        env = os.environ.get("SCRAPER_PROXY", "").strip()
        if env:
            return [env]
        return self._get("anti_detection", "proxy", "proxies", default=[])


# Global instances
scraper_config = ScraperConfig()
anti_detection_config = AntiDetectionConfig()
