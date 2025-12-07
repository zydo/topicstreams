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

import logging
import time
import traceback
from random import shuffle
from typing import List, Set, Tuple

from playwright.sync_api import Browser, BrowserContext, sync_playwright
from playwright_stealth import Stealth

from common import database as db
from common.model import NewsEntry
from common.settings import settings
from .scraper import scrape_news

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


def main():

    with sync_playwright() as p:
        browser: Browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        try:
            context: BrowserContext = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                timezone_id=settings.browser_timezone,
                permissions=["geolocation"],
                geolocation={
                    "latitude": settings.browser_geolocation_latitude,
                    "longitude": settings.browser_geolocation_longitude,
                },
                color_scheme="light",
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                },
            )

            stealth = Stealth()

            while True:
                cycle_start, cycle_success = time.time(), False
                try:
                    topics = [topic.name for topic in db.get_topics()]
                    shuffle(topics)
                    logger.info(f"Scraping for {len(topics)} topics (randomized order)")

                    all_entries, all_logs = [], []
                    for topic in topics:  # Topics from database are already normalized
                        # Create new page per topic to prevent memory accumulation in
                        # long-running scraper
                        page = context.new_page()
                        stealth.apply_stealth_sync(page)
                        try:
                            entries, scraper_logs = scrape_news(
                                page, topic, settings.max_pages
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

                except KeyboardInterrupt:
                    logger.info("Scraper interrupted by user")
                    break

                except Exception as e:
                    logger.error(f"Error in scraping loop: {e}")
                    logger.error(f"Full traceback:\n{traceback.format_exc()}")

                if cycle_success:
                    logger.info(f"{len(topics)} topics took {elapsed:.1f}s")
                else:
                    logger.error(f"Scrape failed in {elapsed:.1f}s")

                elapsed = time.time() - cycle_start
                sleep_time = max(0, settings.scrape_interval - elapsed)
                if sleep_time > 0:
                    logger.info(f"Waiting {sleep_time:.1f}s until next scrape...")
                    time.sleep(sleep_time)
                else:
                    logger.info(
                        f"Cycle elapsed time (exceeds {settings.scrape_interval}s "
                        "interval), starting next cycle immediately"
                    )

        finally:
            browser.close()


if __name__ == "__main__":
    main()
