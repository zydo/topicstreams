"""Browser-context construction for the scraper, parametrized per engine.

Each search engine runs in its own worker with its own *persistent profile
directory* — Chromium holds a ``SingletonLock`` per profile, so workers cannot
share one, and separate profiles also keep each engine's cookies/identity
isolated (which is what we want: one identity per engine on the shared exit IP).

The fingerprint (UA matched to the installed Chromium) and proxy are resolved
*once* in the main thread and handed to every worker, so all engines present the
same real browser version from the same exit IP.
"""

import logging
import random
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from playwright.sync_api import BrowserContext

from common.config import FingerprintProfile, anti_detection_config

logger = logging.getLogger(__name__)

# Per-engine persistent profiles live under this root (a Docker volume in
# production). One subdirectory per engine, e.g. .browser_profiles/google.
BASE_PROFILE_DIR = Path("/app/.browser_profiles")


def profile_dir_for(engine: str) -> Path:
    """Persistent Chromium profile directory for one engine's worker."""
    return BASE_PROFILE_DIR / engine


def detect_fingerprint(p) -> FingerprintProfile:
    """Build a fingerprint matching the installed browser version.

    Google blocks /search when the claimed UA version diverges from the real
    browser (verified 2026-06-11: a hardcoded Chrome/131 UA on Chrome 149 is
    CAPTCHA'd every time, while a version-matched UA passes). Headless Chromium
    also brands itself "HeadlessChrome", an instant block. So: read the real
    version at startup, claim the matching "Chrome/<major>.0.0.0" (real Chrome
    zeroes minor versions via UA reduction) on the actual OS platform.
    """
    browser = p.chromium.launch(headless=True)
    version = browser.version
    browser.close()
    major = version.split(".")[0]
    if sys.platform == "darwin":
        os_part, platform_brand = "Macintosh; Intel Mac OS X 10_15_7", '"macOS"'
    else:
        os_part, platform_brand = "X11; Linux x86_64", '"Linux"'
    logger.info(f"Detected Chromium {version}; claiming Chrome/{major} UA")
    return FingerprintProfile(
        user_agent=(
            f"Mozilla/5.0 ({os_part}) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
        ),
        sec_ch_ua=(
            f'"Chromium";v="{major}", "Google Chrome";v="{major}", "Not;A=Brand";v="24"'
        ),
        sec_ch_ua_platform=platform_brand,
    )


def _build_headers(profile: FingerprintProfile) -> dict:
    base_headers = dict(anti_detection_config.http_headers)
    base_headers["Sec-Ch-Ua"] = profile.sec_ch_ua
    base_headers["Sec-Ch-Ua-Mobile"] = "?0"
    base_headers["Sec-Ch-Ua-Platform"] = profile.sec_ch_ua_platform
    return base_headers


def _add_consent_cookies(context: BrowserContext) -> None:
    context.add_cookies(
        [
            {
                "name": "CONSENT",
                "value": "PENDING+987",
                "domain": ".google.com",
                "path": "/",
            },
            {
                "name": "SOCS",
                "value": "CAESHAgBEhJnd3NfMjAyMzA5MTMtMF9SQzIaAmVuIAEaBgiAo_LmBg",
                "domain": ".google.com",
                "path": "/",
            },
        ]
    )


def build_proxy() -> dict | None:
    """Resolve the configured proxy into Playwright's ``proxy`` argument.

    Optional fallback: with a version-matched fingerprint, /search works from
    a clean residential IP without a proxy. Returns None when proxying is
    disabled or misconfigured, in which case the browser connects directly.
    """
    if not anti_detection_config.proxy_enabled:
        return None

    servers = anti_detection_config.proxy_servers
    if not servers:
        logger.warning(
            "Proxy enabled but no proxy servers configured; connecting directly"
        )
        return None

    # One endpoint per browser launch. Residential gateways rotate exit IPs
    # server-side, so a single sticky endpoint is the common case; a longer
    # list lets the identity vary across container restarts.
    parsed = urlparse(random.choice(servers))
    if not parsed.hostname:
        logger.warning("Invalid proxy URL (no host); connecting directly")
        return None

    server = f"{parsed.scheme or 'http'}://{parsed.hostname}"
    if parsed.port:
        server += f":{parsed.port}"

    proxy: dict = {"server": server}
    if parsed.username:
        proxy["username"] = unquote(parsed.username)
    if parsed.password:
        proxy["password"] = unquote(parsed.password)
    return proxy


def _launch_context(
    p, profile_dir: Path, profile: FingerprintProfile, proxy: dict | None
) -> BrowserContext:
    profile_dir.mkdir(parents=True, exist_ok=True)
    # Remove stale lockfile left by a previous Chrome instance (e.g. after
    # container restart). Chrome refuses to start if it finds this lock.
    lockfile = profile_dir / "SingletonLock"
    if lockfile.exists():
        lockfile.unlink()
    # Playwright's bundled Chromium (no channel="chrome"): Google Chrome has
    # no Linux arm64 build, and running an amd64 Chrome under Rosetta 2
    # emulation gets CAPTCHA'd by Google (verified 2026-06-11).
    return p.chromium.launch_persistent_context(
        str(profile_dir),
        headless=True,
        proxy=proxy,
        args=anti_detection_config.browser_args,
        ignore_default_args=["--enable-automation"],
        user_agent=profile.user_agent,
        viewport={
            "width": anti_detection_config.viewport_width,
            "height": anti_detection_config.viewport_height,
        },
        locale=anti_detection_config.locale,
        timezone_id=anti_detection_config.timezone_id,
        permissions=anti_detection_config.permissions,
        geolocation={
            "latitude": anti_detection_config.geolocation_latitude,
            "longitude": anti_detection_config.geolocation_longitude,
        },
        color_scheme=anti_detection_config.color_scheme,
        extra_http_headers=_build_headers(profile),
    )


def new_context(
    p, profile_dir: Path, profile: FingerprintProfile, proxy: dict | None
) -> BrowserContext:
    """Launch a persistent context for one engine and apply the soft anti-bot
    touches (optional stealth, Google consent cookies)."""
    context = _launch_context(p, profile_dir, profile, proxy)
    if anti_detection_config.playwright_stealth_enabled:
        # Deferred import: stealth is permanently disabled (Google detects
        # its JS patches), so don't require the package unless enabled.
        from playwright_stealth import Stealth

        Stealth().apply_stealth_sync(context)
    _add_consent_cookies(context)
    return context
