"""Google Search News tab scraping module.

This module scrapes news articles from Google Search's News tab
(https://google.com/search?tbm=nws), NOT the Google News site (https://news.google.com).

It retrieves multiple pages of results sorted by recency (newest first)
for specified topics using Playwright and BeautifulSoup. Results are
filtered to the past hour and paginated (approximately 10 results per page).

Main Features:
    - Multi-page scraping with configurable page limit
    - Automatic pagination handling (start parameter)
    - Robust error handling and logging
    - Multiple fallback CSS selectors for resilience
    - URL normalization (handles Google redirect wrappers)
    - Results naturally limited by 1-hour time filter
"""

import logging
import re
import traceback
from datetime import datetime
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup
from bs4.element import Tag, ResultSet
from playwright.sync_api import Page, Response

from common.model import NewsEntry, ScraperLog

logger = logging.getLogger(__name__)


def scrape_news(
    page: Page, topic: str, max_result_pages: Optional[int] = None
) -> Tuple[List[NewsEntry], List[ScraperLog]]:
    """Scrape news entries from Google Search News tab across multiple result pages.

    This function scrapes news articles from the recent 1 hour for a given topic
    by iterating through multiple pages of Google Search results (News tab).
    It continues scraping until one of the following conditions is met:
    - No more entries are found (end of results)
    - An error occurs during scraping
    - Maximum page limit is reached (if specified)

    Args:
        page: Playwright Page object for browser automation
        topic: News topic to search for (e.g., "artificial intelligence")
        max_result_pages: Maximum number of result pages to scrape (default: None)
                          None means scrape all available pages

    Returns:
        Tuple containing:
        - List[NewsEntry]: All scraped news entries across all pages, in chronological
                           order (oldest to newest)
        - List[ScraperLog]: Log entries for each page scrape attempt

    Note:
        - Results are filtered to the recent 1 hour only (qdr:h parameter)
        - The function stops immediately if any page scrape fails
        - Due to the 1-hour time filter, total results are naturally limited
        - Each page except for the last contains exactly 10 results (set by Google)
    """
    result_page_number = 1
    all_entries, scraper_logs = [], []

    while True:
        if max_result_pages is not None and result_page_number > max_result_pages:
            break

        entries, scraper_log = _scrape_one_page(page, topic, result_page_number)

        scraper_logs.append(scraper_log)
        # Stop if no entries found (end of results) or error occurred
        if len(entries) == 0 or not scraper_log.success:
            break

        all_entries.extend(entries)
        result_page_number += 1

    # Reverse to get chronological order (oldest to newest)
    all_entries.reverse()
    scraper_logs.reverse()
    return all_entries, scraper_logs


def _scrape_one_page(
    page: Page, topic: str, result_page_number: int
) -> Tuple[List[NewsEntry], ScraperLog]:
    """Scrape news entries from a single Google Search News results page.

    Constructs a Google Search URL with News tab filters and pagination,
    navigates to the page, waits for content to load, parses the HTML,
    and extracts news entries from the recent 1 hour.

    Args:
        page: Playwright Page object for browser automation
        topic: News topic to search for (will be URL-encoded)
        result_page_number: 1-based page number for pagination
                           (page 1 = start 0, page 2 = start 10, etc.)

    Returns:
        Tuple containing:
        - List[NewsEntry]: Parsed news entries from this page (may be empty)
        - ScraperLog: Log entry for this scrape attempt (success or failure)
    """

    # Replace one or more consecutive spaces with a single '+'
    formatted_topic = re.sub(r"\s+", "+", topic.strip())

    # 1-based page offset, 10 results per Google result page
    start = (result_page_number - 1) * 10

    # - tbm=nws: "To Be Matched" = news (search in Google Search News tab)
    # - tbs=sbd:1,qdr:h: "To Be Sorted"
    #       sbd:1 - sort by date : 1 (newest first)
    #       qdr:h - query date range : h (past hour)
    #       nsd:1 - news show duplicate : 1 (show same news from different sources)
    url = (
        "https://www.google.com/search?tbm=nws"
        f"&tbs=sbd:1,qdr:h,nsd:1&start={start}&q={formatted_topic}"
    )

    logger.info(f"Scraping news for topic: {topic}")

    try:
        response: Optional[Response] = page.goto(
            url, wait_until="domcontentloaded", timeout=5000
        )

        if response is None:
            logger.error(
                f"No response received for topic '{topic}' - Navigation failed"
            )
            logger.error(f"URL: {url}")
            return (
                [],
                ScraperLog.create_new(
                    topic=topic,
                    success=False,
                    scraped_at=datetime.now(),
                    error_message="No response received - Navigation failed",
                ),
            )

        response_status: int = response.status

        if _is_http_error(response_status):
            logger.error(
                f"HTTP ERROR {response_status} for topic '{topic}' - Request failed"
            )
            logger.error(f"URL: {page.url}")
            if response_status == 429:
                logger.error("Rate limiting detected - Too many requests")
            elif response_status in (403, 503):
                logger.error("Access blocked - May need to adjust scraping strategy")
            return (
                [],
                ScraperLog.create_new(
                    topic=topic,
                    success=False,
                    scraped_at=datetime.now(),
                    http_status_code=response_status,
                ),
            )

        # Add a small delay to let dynamic content load
        page.wait_for_timeout(1000)

        # Try to wait for common selectors, but don't fail if not found
        try:
            page.wait_for_selector(
                "#search, #rso, div[data-sokoban-container]", timeout=5000
            )
        except Exception as e:
            logger.warning(f"Selector wait timeout, proceeding anyway: {e}")

        content: str = page.content()

        soup: BeautifulSoup = BeautifulSoup(content, "lxml")

        news_items: ResultSet = _find_news_items(soup)
        logger.info(f"Found {len(news_items)} potential news items")

        entries: List[NewsEntry] = []
        for item in news_items:
            try:
                entry: Optional[NewsEntry] = _parse_item(item, topic)
                if entry:
                    entries.append(entry)
            except Exception as e:
                logger.debug(f"Error parsing news item: {e}")
                continue

        logger.info(f"Successfully parsed {len(entries)} news entries")
        return (
            entries,
            ScraperLog.create_new(
                topic=topic,
                success=True,
                scraped_at=datetime.now(),
                http_status_code=response_status,
            ),
        )

    except Exception as e:
        logger.error(f"Error scraping news for topic '{topic}'")
        logger.error(f"Exception type: {type(e).__name__}")
        logger.error(f"Full traceback:\n{traceback.format_exc()}")
        return (
            [],
            ScraperLog.create_new(
                topic=topic,
                success=False,
                scraped_at=datetime.now(),
                error_message=f"{type(e).__name__}: {str(e)}",
            ),
        )


def _find_news_items(soup: BeautifulSoup) -> ResultSet:
    news_items: ResultSet = soup.select("div.SoaBEf")
    if not news_items:
        news_items = soup.select("div.Gx5Zad")
    if not news_items:
        news_items = soup.select("div[data-sokoban-container] > div")
    if not news_items:
        news_items = soup.select("#rso div.g, #search div.g")
    return news_items


def _parse_item(item: Tag, topic: str) -> Optional[NewsEntry]:

    def _get_title(item: Tag) -> Optional[str]:
        title_elem: Optional[Tag] = item.select_one(
            'div[role="heading"], a[role="heading"]'
        )
        if not title_elem:
            title_elem = item.select_one("h3, h4")
        return title_elem.get_text(strip=True) if title_elem else None

    title: Optional[str] = _get_title(item)
    if not title:
        return None

    def _get_url(item: Tag) -> Optional[str]:
        link_elem: Optional[Tag] = item.select_one("a[href]")
        if not link_elem:
            return None

        href = link_elem.get("href")
        if not href:
            return None

        url: str = str(href).strip()
        if url.startswith("/url?q="):
            url = (url.split("/url?q=")[1]).split("&")[0]
        elif url.startswith("/"):
            url = "https://www.google.com" + url

        return url

    url: Optional[str] = _get_url(item)
    if not url:
        return None

    def _get_source(item: Tag) -> Optional[str]:
        source_elem: Optional[Tag] = item.select_one("div.MgUUmf, span.MgUUmf")
        if not source_elem:
            source_elem = item.select_one("div[data-n-tid], div.CEMjEf span")
        return source_elem.get_text(strip=True) if source_elem else None

    source: Optional[str] = _get_source(item)

    return NewsEntry.create_new(
        topic=topic,
        title=title,
        url=url,
        source=source,
    )


def _is_http_error(response_status: int) -> bool:
    """Check if HTTP response indicates an error (non-2xx status code).

    Returns True for any status code >= 400 (client/server errors).
    Returns False for 2xx (success) and 3xx (redirects).
    """
    return response_status >= 400
