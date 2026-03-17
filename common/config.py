"""Configuration management for TopicStreams.

Loads and provides access to YAML configuration files for both scraper
and anti-detection settings.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List

import yaml

logger = logging.getLogger(__name__)

# Base config directory
CONFIG_DIR = Path(__file__).parent.parent / "config"


class ScraperConfig:
    """Scraper configuration loader and accessor."""

    def __init__(self, config_path: Path = CONFIG_DIR / "scraper.yml"):
        """Load scraper configuration from YAML file.

        Args:
            config_path: Path to the scraper.yml file
        """
        self.config_path = config_path
        self._config: dict = {}
        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from YAML file."""
        try:
            with open(self.config_path, "r") as f:
                self._config = yaml.safe_load(f) or {}
            logger.info(f"Loaded scraper config from {self.config_path}")
        except FileNotFoundError:
            logger.warning(f"Config file not found: {self.config_path}, using defaults")
            self._config = self._default_config()
        except yaml.YAMLError as e:
            logger.error(f"Error parsing config file: {e}, using defaults")
            self._config = self._default_config()

    def _default_config(self) -> dict:
        """Return default configuration."""
        return {
            "scraper": {
                "scrape_interval": 60,
                "max_pages": 1,
            }
        }

    def _get(self, *keys: str, default: Any = None) -> Any:
        """Get nested config value by keys.

        Args:
            *keys: Nested keys to traverse
            default: Default value if key not found

        Returns:
            Config value or default
        """
        value = self._config
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
                if value is None:
                    return default
            else:
                return default
        return value if value is not None else default

    @property
    def scrape_interval(self) -> int:
        """Get scrape interval in seconds."""
        return self._get("scraper", "scrape_interval", default=60)

    @property
    def max_pages(self) -> int:
        """Get maximum pages to scrape per topic."""
        return self._get("scraper", "max_pages", default=1)


class AntiDetectionConfig:
    """Anti-detection configuration loader and accessor."""

    def __init__(self, config_path: Path = CONFIG_DIR / "anti_detection.yml"):
        """Load anti-detection configuration from YAML file.

        Args:
            config_path: Path to the anti_detection.yml file
        """
        self.config_path = config_path
        self._config: Dict[str, Any] = {}
        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from YAML file."""
        try:
            with open(self.config_path, "r") as f:
                self._config = yaml.safe_load(f) or {}
            logger.info(f"Loaded anti-detection config from {self.config_path}")
        except FileNotFoundError:
            logger.warning(f"Config file not found: {self.config_path}, using defaults")
            self._config = self._default_config()
        except yaml.YAMLError as e:
            logger.error(f"Error parsing config file: {e}, using defaults")
            self._config = self._default_config()

    def _default_config(self) -> Dict[str, Any]:
        """Return default configuration."""
        return {
            "anti_detection": {
                "playwright_stealth": {"enabled": True},
                "browser_args": {
                    "enabled": True,
                    "args": [
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-blink-features=AutomationControlled",
                    ],
                },
                "page_isolation": {"enabled": True},
                "random_delays": {"enabled": True, "min_seconds": 2, "max_seconds": 5},
                "randomized_order": {"enabled": True},
                "browser_fingerprint": {
                    "enabled": True,
                    "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "user_agent_rotation": {
                        "enabled": False,
                        "strategy": "per_topic",
                        "user_agents": [
                            # Chrome on Windows
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                            # Chrome on macOS
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                            # Chrome on Linux
                            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                            # Firefox on Windows
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
                            # Firefox on macOS
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0",
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:132.0) Gecko/20100101 Firefox/132.0",
                            # Firefox on Linux
                            "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
                            "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0",
                            # Safari on macOS
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
                        ],
                    },
                    "viewport_width": 1920,
                    "viewport_height": 1080,
                    "locale": "en-US",
                    "timezone_id": "America/Los_Angeles",
                    "geolocation_latitude": 37.3273,
                    "geolocation_longitude": -121.954,
                    "color_scheme": "light",
                    "permissions": ["geolocation"],
                },
                "captcha_detection": {
                    "enabled": True,
                    "keywords": ["captcha", "unusual traffic"],
                },
                "http_error_handling": {
                    "enabled": True,
                    "monitored_codes": [429, 403, 503],
                },
                "http_headers": {
                    "enabled": True,
                    "headers": {
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                },
            },
        }

    def _get(self, *keys: str, default: Any = None) -> Any:
        """Get nested config value by keys.

        Args:
            *keys: Nested keys to traverse
            default: Default value if key not found

        Returns:
            Config value or default
        """
        value = self._config
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
                if value is None:
                    return default
            else:
                return default
        return value if value is not None else default

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
    def page_isolation_enabled(self) -> bool:
        """Check if page isolation is enabled."""
        return self._get("anti_detection", "page_isolation", "enabled", default=True)

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
    def user_agent(self) -> str:
        """Get browser user agent (static fallback)."""
        return self._get(
            "anti_detection",
            "browser_fingerprint",
            "user_agent",
            default="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )

    @property
    def user_agent_rotation_enabled(self) -> bool:
        """Check if user agent rotation is enabled."""
        return self._get(
            "anti_detection", "browser_fingerprint", "user_agent_rotation", "enabled", default=False
        )

    @property
    def user_agent_rotation_strategy(self) -> str:
        """Get user agent rotation strategy."""
        return self._get(
            "anti_detection", "browser_fingerprint", "user_agent_rotation", "strategy", default="per_topic"
        )

    @property
    def user_agent_list(self) -> List[str]:
        """Get list of user agents for rotation."""
        return self._get(
            "anti_detection",
            "browser_fingerprint",
            "user_agent_rotation",
            "user_agents",
            default=[],
        )

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
    def color_scheme(self) -> str:
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


# Global instances
scraper_config = ScraperConfig()
anti_detection_config = AntiDetectionConfig()
