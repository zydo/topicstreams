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
import time
import traceback
from pathlib import Path
from random import shuffle
from typing import List, Optional, Set, Tuple
from urllib.parse import unquote, urlparse

from playwright.sync_api import BrowserContext, Page, sync_playwright
from playwright_stealth import Stealth

from common import database as db
from common.config import FingerprintProfile, anti_detection_config, scraper_config
from common.settings import settings
from common.model import NewsEntry
from .scraper import scrape_news

atexit.register(db.close_pool)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Persistent browser profile directory (mounted as a Docker volume in production).
USER_DATA_DIR = Path("/app/.browser_profiles/default")

# TODO: Use Redis with TTL to replace this.
# Memorize inserted NewsEntry tuple (topic, title, domain) to match the DB
# UNIQUE(topic, title, domain) constraint. This in-memory dedup is just an optimization
# to reduce DB round-trips; removing it won't affect correctness.
_seen_entries: Set[Tuple[str, str, str]] = set()
_MAX_SEEN_ENTRIES = 25000

# Single consistent identity for the persistent context.
_DEFAULT_PROFILE = FingerprintProfile(
    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    sec_ch_ua='"Chromium";v="131", "Not_A Brand";v="24"',
    sec_ch_ua_platform='"macOS"',
)


def _dedup_entries(entries: List[NewsEntry]) -> List[NewsEntry]:
    global _seen_entries

    res, seen = [], set()
    for entry in entries:
        signature = (entry.topic, entry.title, entry.domain)
        if signature in seen or signature in _seen_entries:
            continue
        res.append(entry)
        seen.add(signature)
    return res


def _add_to_seen_entries(entries: List[NewsEntry]) -> None:
    global _seen_entries

    if len(_seen_entries) > _MAX_SEEN_ENTRIES:
        logger.info(f"Clearing seen entries cache ({len(_seen_entries)} entries)")
        _seen_entries.clear()

    for entry in entries:
        _seen_entries.add((entry.topic, entry.title, entry.domain))


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


def _build_proxy() -> Optional[dict]:
    """Resolve the configured proxy into Playwright's ``proxy`` argument.

    Google now blocks automated browsers from /search outright (residential IP
    or not), so routing through a residential/mobile proxy is the only way to
    keep the News-tab scrape working. Returns None when proxying is disabled or
    misconfigured, in which case the browser connects directly.
    """
    if not anti_detection_config.proxy_enabled:
        return None

    servers = anti_detection_config.proxy_servers
    if not servers:
        logger.warning("Proxy enabled but no proxy servers configured; "
                       "connecting directly")
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
    profile = _DEFAULT_PROFILE
    proxy = _build_proxy()
    if proxy:
        # Never log credentials, only the server endpoint.
        logger.info(f"Routing browser traffic through proxy: {proxy['server']}")
    return p.chromium.launch_persistent_context(
        str(USER_DATA_DIR),
        headless=True,
        channel="chrome",
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


def main():
    with sync_playwright() as p:
        context = _launch_context(p)
        if anti_detection_config.playwright_stealth_enabled:
            Stealth().apply_stealth_sync(context)
        _add_consent_cookies(context)

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

                        page: Page = context.new_page()
                        try:
                            entries, scraper_logs = scrape_news(
                                page, topic, scraper_config.max_pages
                            )
                            all_entries.extend(entries)
                            all_logs.extend(scraper_logs)
                        finally:
                            page.close()

                    new_entries = _dedup_entries(all_entries)
                    logger.info(f"Found {len(new_entries)} new news entries")

                    db.insert_news_entries(new_entries)
                    _add_to_seen_entries(new_entries)
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

        except KeyboardInterrupt:
            logger.info("Scraper interrupted by user")

        finally:
            context.close()


if __name__ == "__main__":
    main()
