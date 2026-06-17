"""Entry point for the TopicStreams news scraper.

This module initializes a persistent Playwright browser context and runs the
scraper loop to continuously collect news articles for tracked topics.

Anti-detection strategy:
    - One persistent browser context (cookies/cache survive container restarts),
      run with a single consistent identity (rotation disabled by default).
    - Long random delays between topics, with occasional "long break" pauses
      to mimic a human stepping away.

Scraping Strategy:
    - Scrapes configurable number of pages (MAX_PAGES, default: 1) for efficiency
    - Since results are sorted by recency and filtered to the past hour, new articles
      always appear on the first few pages

IMPORTANT Assumption:
    This strategy assumes the scrape interval is short enough that new entries between
    cycles do not exceed number of results from the first MAX_PAGES pages (10 results
    per page). If more new articles are published for a topic between scrapes than fit
    on the configured pages, older articles will be missed.
"""

import atexit
import logging
import random
import sys
import time
import traceback
from pathlib import Path
from random import shuffle
from urllib.parse import unquote, urlparse

from playwright.sync_api import BrowserContext, sync_playwright

from common import database as db
from common.config import FingerprintProfile, anti_detection_config, scraper_config
from common.logging_config import configure_logging
from common.settings import settings
from .scraper import scrape_topic
from .sources import get_source

atexit.register(db.close_pool)

configure_logging(settings.log_format)
logger = logging.getLogger(__name__)

# Persistent browser profile directory (mounted as a Docker volume in production).
USER_DATA_DIR = Path("/app/.browser_profiles/default")

# Recycle the Chromium context every N cycles to release accumulated memory.
# A single long-lived context grows unbounded over thousands of cycles; on the
# swap-less production host this exhausted RAM and livelocked the box (postmortem
# 2026-06-13). The on-disk persistent profile survives the recycle.
BROWSER_RECYCLE_CYCLES = 50


def _detect_fingerprint(p) -> FingerprintProfile:
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
            f'"Chromium";v="{major}", "Google Chrome";v="{major}", '
            f'"Not;A=Brand";v="24"'
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


def _sleep_between_topics() -> None:
    delay = random.uniform(
        anti_detection_config.random_delay_min,
        anti_detection_config.random_delay_max,
    )
    logger.info(f"Sleeping {delay:.1f}s before next topic")
    time.sleep(delay)


def _build_proxy() -> dict | None:
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
            "Proxy enabled but no proxy servers configured; " "connecting directly"
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


def _launch_context(p) -> BrowserContext:
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Remove stale lockfile left by a previous Chrome instance (e.g. after
    # container restart). Chrome refuses to start if it finds this lock.
    lockfile = USER_DATA_DIR / "SingletonLock"
    if lockfile.exists():
        lockfile.unlink()
    profile = _detect_fingerprint(p)
    proxy = _build_proxy()
    if proxy:
        # Never log credentials, only the server endpoint.
        logger.info(f"Routing browser traffic through proxy: {proxy['server']}")
    # Playwright's bundled Chromium (no channel="chrome"): Google Chrome has
    # no Linux arm64 build, and running an amd64 Chrome under Rosetta 2
    # emulation gets CAPTCHA'd by Google (verified 2026-06-11).
    return p.chromium.launch_persistent_context(
        str(USER_DATA_DIR),
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


def _new_context(p) -> BrowserContext:
    context = _launch_context(p)
    if anti_detection_config.playwright_stealth_enabled:
        # Deferred import: stealth is permanently disabled (Google detects
        # its JS patches), so don't require the package unless enabled.
        from playwright_stealth import Stealth

        Stealth().apply_stealth_sync(context)
    _add_consent_cookies(context)
    return context


def main():
    with sync_playwright() as p:
        context = _new_context(p)
        cycle_count = 0

        # Resolve configured engines once (fails fast on an unknown name).
        sources = [get_source(name) for name in scraper_config.engines]
        strategy = scraper_config.engine_strategy
        logger.info(
            f"Scraping with engines {[s.name for s in sources]} "
            f"(strategy: {strategy})"
        )

        try:
            while True:
                cycle_start, cycle_success = time.time(), False

                try:
                    deleted = db.purge_old_news_entries(settings.news_retention_days)
                    if deleted:
                        logger.info(
                            f"Purged {deleted} news entries older than "
                            f"{settings.news_retention_days} days"
                        )

                    deleted_logs = db.purge_old_scraper_logs(
                        settings.news_retention_days
                    )
                    if deleted_logs:
                        logger.info(
                            f"Purged {deleted_logs} scraper logs older than "
                            f"{settings.news_retention_days} days"
                        )

                    topics = [topic.name for topic in db.get_topics()]

                    if anti_detection_config.randomized_order_enabled:
                        shuffle(topics)
                        logger.info(
                            f"Scraping for {len(topics)} topics (randomized order)"
                        )
                    else:
                        logger.info(f"Scraping for {len(topics)} topics")

                    all_entries, all_logs = [], []
                    for i, topic in enumerate(topics):
                        if i > 0 and anti_detection_config.random_delays_enabled:
                            _sleep_between_topics()

                        entries, scraper_logs = scrape_topic(
                            context.new_page,
                            sources,
                            topic,
                            strategy=strategy,
                            cycle=cycle_count,
                            max_result_pages=scraper_config.max_pages,
                        )
                        all_entries.extend(entries)
                        all_logs.extend(scraper_logs)

                    # Dedup is handled by the DB (news upsert on a URL-derived
                    # id + UNIQUE(topic, news_id) on matches), so insert the raw
                    # parsed entries and let ON CONFLICT skip what already exists.
                    new_events = db.insert_news_entries(all_entries)
                    logger.info(
                        f"Parsed {len(all_entries)} entries, {new_events} new feed events"
                    )
                    db.insert_scraper_logs(all_logs)
                    cycle_success = True

                except Exception as e:
                    logger.error(f"Error in scraping loop: {e}")
                    logger.error(f"Full traceback:\n{traceback.format_exc()}")

                elapsed = time.time() - cycle_start
                if cycle_success:
                    logger.info(f"{len(topics)} topics took {elapsed:.1f}s")
                else:
                    logger.error(f"Scrape failed in {elapsed:.1f}s")

                sleep_time = max(0, scraper_config.scrape_interval - elapsed)
                if sleep_time > 0:
                    logger.info(f"Waiting {sleep_time:.1f}s until next scrape...")
                    time.sleep(sleep_time)
                else:
                    logger.info(
                        f"Cycle elapsed time (exceeds {scraper_config.scrape_interval}s "
                        "interval), starting next cycle immediately"
                    )

                cycle_count += 1
                if cycle_count % BROWSER_RECYCLE_CYCLES == 0:
                    logger.info(
                        f"Recycling browser context after {cycle_count} cycles "
                        "to release accumulated memory"
                    )
                    context.close()
                    context = _new_context(p)

        except KeyboardInterrupt:
            logger.info("Scraper interrupted by user")

        finally:
            context.close()


if __name__ == "__main__":
    main()
