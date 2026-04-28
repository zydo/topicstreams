"""Entry point for the TopicStreams news scraper.

This module initializes the Playwright browser and runs the scraper loop
to continuously collect news articles for tracked topics.

Scraping Strategy:
    - Scrapes configurable number of pages (MAX_PAGES, default: 1) for efficiency
    - Since results are sorted by recency and filtered to the past hour, new articles
      always appear on the first few pages

IMPORTANT Assumption:
    This strategy assumes the scrape interval is short enough that new entries between
    cycles do not exceed number of results from the first MAX_PAGES pages (10 results
    per page). If more new articles are published for a topic between scrapes than fit
    on the configured pages, older articles will be missed.

    If you set a longer scrape interval (e.g., >5 minutes for high-volume topics),
    set the MAX_PAGES environment variable to a larger number (e.g., 2-3) to avoid
    missing news articles.
"""

import atexit
import logging
import random
import time
import traceback
from random import shuffle
from typing import List, Optional, Set, Tuple

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
from playwright_stealth import Stealth

from common import database as db
from common.config import FingerprintProfile, anti_detection_config, scraper_config
from common.model import NewsEntry
from .scraper import scrape_news

atexit.register(db.close_pool)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# TODO: Use Redis with TTL to replace this.
# Memorize inserted NewsEntry tuple (topic, title, source) to avoid unnecessary insert.
# Use 'source' because 'domain' field is not calculated yet.
# At schema level there is also UNIQUE(topic, title, domain) constraint. This in-memory
# dedup is just an optimization to reduce DB operations, removing it won't affect
# behavior.
# Using set for O(1) lookups with periodic cleanup to prevent unbounded memory growth.
# Max 25,000 entries (~6 hours of data at 10 topics * 5 entries/min * 60 min * 6 hours).
_seen_entries: Set[Tuple[str, str, str | None]] = set()
_MAX_SEEN_ENTRIES = 25000

# Default profile matching the static user_agent in config
_DEFAULT_PROFILE = FingerprintProfile(
    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    sec_ch_ua='"Chromium";v="131", "Not_A Brand";v="24"',
    sec_ch_ua_platform='"macOS"',
)


class ProfileRotation:
    """Manages fingerprint profile rotation for anti-detection."""

    def __init__(self):
        self._current_index = 0
        self._cycle_count = 0

    def get_profile(self) -> FingerprintProfile:
        profiles = anti_detection_config.fingerprint_profiles
        if not anti_detection_config.user_agent_rotation_enabled or not profiles:
            return _DEFAULT_PROFILE

        strategy = anti_detection_config.user_agent_rotation_strategy

        if strategy == "per_cycle":
            profile = profiles[self._cycle_count % len(profiles)]
            return profile
        elif strategy == "per_topic":
            profile = profiles[self._current_index % len(profiles)]
            self._current_index += 1
            return profile
        else:
            logger.warning(f"Unknown rotation strategy: {strategy}")
            return _DEFAULT_PROFILE

    def advance_cycle(self) -> None:
        self._cycle_count += 1
        self._current_index = 0


def _dedup_entries(entries: List[NewsEntry]) -> List[NewsEntry]:
    global _seen_entries

    # Dedup both in-batch and with seen_entries
    res, seen = [], set()
    for entry in entries:
        signature = (entry.topic, entry.title, entry.source)
        if signature in seen or signature in _seen_entries:
            continue
        res.append(entry)
        seen.add(signature)
    return res


def _add_to_seen_entries(entries: List[NewsEntry]) -> None:
    global _seen_entries

    # Prevent unbounded memory growth: clear when exceeding limit
    if len(_seen_entries) > _MAX_SEEN_ENTRIES:
        logger.info(f"Clearing seen entries cache ({len(_seen_entries)} entries)")
        _seen_entries.clear()

    for entry in entries:
        _seen_entries.add((entry.topic, entry.title, entry.source))


def _build_headers(profile: FingerprintProfile) -> dict:
    base_headers = dict(anti_detection_config.http_headers)
    base_headers["Sec-Ch-Ua"] = profile.sec_ch_ua
    base_headers["Sec-Ch-Ua-Mobile"] = "?0"
    base_headers["Sec-Ch-Ua-Platform"] = profile.sec_ch_ua_platform
    return base_headers


def _create_context(browser: Browser, profile: FingerprintProfile) -> BrowserContext:
    return browser.new_context(
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


def _add_consent_cookies(context: BrowserContext) -> None:
    context.add_cookies([
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
    ])


def main():
    """Main scraper entry point with fingerprint profile rotation support."""
    profile_rotation = ProfileRotation()

    with sync_playwright() as p:
        browser: Browser = p.chromium.launch(
            headless=True,
            channel="chrome",
            args=anti_detection_config.browser_args,
            ignore_default_args=["--enable-automation"],
        )

        need_context_per_topic = (
            anti_detection_config.user_agent_rotation_enabled
            and anti_detection_config.user_agent_rotation_strategy == "per_topic"
        )

        stealth = (
            Stealth() if anti_detection_config.playwright_stealth_enabled else None
        )

        try:
            # Create default context (used for per_cycle rotation or no rotation)
            default_context: Optional[BrowserContext] = None
            if not need_context_per_topic:
                default_profile = profile_rotation.get_profile()
                default_context = _create_context(browser, default_profile)
                if stealth:
                    stealth.apply_stealth_sync(default_context)

            # Log rotation status
            if anti_detection_config.user_agent_rotation_enabled:
                strategy = anti_detection_config.user_agent_rotation_strategy
                profiles = anti_detection_config.fingerprint_profiles
                logger.info(
                    f"Profile rotation enabled: strategy={strategy}, "
                    f"{len(profiles)} profiles in pool"
                )

            try:
                while True:
                    cycle_start, cycle_success = time.time(), False
                    current_context: Optional[BrowserContext] = None

                    try:
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
                                delay = random.uniform(
                                    anti_detection_config.random_delay_min,
                                    anti_detection_config.random_delay_max,
                                )
                                time.sleep(delay)

                            if need_context_per_topic:
                                profile = profile_rotation.get_profile()
                                current_context = _create_context(browser, profile)
                                if stealth:
                                    stealth.apply_stealth_sync(current_context)
                                _add_consent_cookies(current_context)
                                logger.debug(
                                    f"Topic '{topic}' using profile: {profile.user_agent[:60]}..."
                                )
                            else:
                                current_context = default_context

                            assert current_context is not None
                            page: Page = current_context.new_page()

                            try:
                                entries, scraper_logs = scrape_news(
                                    page, topic, scraper_config.max_pages
                                )
                                all_entries.extend(entries)
                                all_logs.extend(scraper_logs)
                            finally:
                                page.close()
                                if need_context_per_topic and current_context:
                                    current_context.close()
                                    current_context = None

                        new_entries = _dedup_entries(all_entries)
                        logger.info(f"Found {len(new_entries)} new news entries")

                        db.insert_news_entries(new_entries)
                        _add_to_seen_entries(new_entries)
                        db.insert_scraper_logs(all_logs)
                        cycle_success = True

                    except Exception as e:
                        logger.error(f"Error in scraping loop: {e}")
                        logger.error(f"Full traceback:\n{traceback.format_exc()}")

                    finally:
                        if need_context_per_topic and current_context:
                            current_context.close()

                    elapsed = time.time() - cycle_start
                    if cycle_success:
                        logger.info(f"{len(topics)} topics took {elapsed:.1f}s")
                    else:
                        logger.error(f"Scrape failed in {elapsed:.1f}s")

                    profile_rotation.advance_cycle()

                    # Update context for next cycle if using per_cycle rotation
                    if (
                        anti_detection_config.user_agent_rotation_enabled
                        and anti_detection_config.user_agent_rotation_strategy == "per_cycle"
                    ):
                        next_profile = profile_rotation.get_profile()
                        logger.info(f"Next cycle profile: {next_profile.user_agent[:60]}...")
                        if default_context:
                            default_context.close()
                        default_context = _create_context(browser, next_profile)
                        if stealth:
                            stealth.apply_stealth_sync(default_context)

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
            if default_context:
                default_context.close()
            browser.close()


if __name__ == "__main__":
    main()
