"""Configuration management for TopicStreams.

Loads and provides access to YAML configuration files for both scraper
and anti-detection settings.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal

import yaml

logger = logging.getLogger(__name__)

# Base config directory
CONFIG_DIR = Path(__file__).parent.parent / "config"


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
        self._config: Dict[str, Any] = {}
        self._load_config()

    def _load_config(self) -> None:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

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

    def __init__(self, config_path: Path = CONFIG_DIR / "scraper.yml"):
        super().__init__(config_path)

    @property
    def scrape_interval(self) -> int:
        """Get scrape interval in seconds."""
        return self._get("scraper", "scrape_interval", default=60)

    @property
    def max_pages(self) -> int:
        """Get maximum pages to scrape per topic."""
        return self._get("scraper", "max_pages", default=1)


class AntiDetectionConfig(_BaseConfig):
    """Anti-detection configuration loader and accessor."""

    _log_name = "anti-detection"

    def __init__(self, config_path: Path = CONFIG_DIR / "anti_detection.yml"):
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
    def randomized_order_enabled(self) -> bool:
        """Check if randomized topic order is enabled."""
        return self._get("anti_detection", "randomized_order", "enabled", default=True)

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
    def browser_args(self) -> List[str]:
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
    def permissions(self) -> List[str]:
        """Get browser permissions."""
        return self._get(
            "anti_detection",
            "browser_fingerprint",
            "permissions",
            default=["geolocation"],
        )

    @property
    def captcha_keywords(self) -> List[str]:
        """Get CAPTCHA detection keywords."""
        return self._get(
            "anti_detection",
            "captcha_detection",
            "keywords",
            default=["captcha", "unusual traffic"],
        )

    @property
    def monitored_http_codes(self) -> List[int]:
        """Get monitored HTTP error codes."""
        return self._get(
            "anti_detection",
            "http_error_handling",
            "monitored_codes",
            default=[429, 403, 503],
        )

    @property
    def http_headers(self) -> Dict[str, str]:
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
        # (config/ is baked into the scraper image at build time).
        if os.environ.get("SCRAPER_PROXY", "").strip():
            return True
        return self._get("anti_detection", "proxy", "enabled", default=False)

    @property
    def proxy_servers(self) -> List[str]:
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
