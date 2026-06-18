"""Generic scraper runner.

Drives a ``SearchSource`` (one per search engine, see ``scraper/sources/``)
with Playwright: builds the results URL, navigates, simulates human behaviour,
detects blocking, and parses results. The engine-specific parts — URL params,
selectors, block signals — live in the sources, so this runner is the same for
Google, Bing, etc.
"""

import logging
import random
import time
import traceback
from typing import Callable

from bs4 import BeautifulSoup
from playwright.sync_api import Page, Response

from common.config import anti_detection_config
from common.model import NewsEntry, ScraperLog

from .cooldown import EngineCooldownTracker
from .sources import Ordering, Recency, SearchSource

logger = logging.getLogger(__name__)


def _engines_for_cycle(
    sources: list[SearchSource], strategy: str, cycle: int
) -> list[SearchSource]:
    """Pick which engines run this cycle for the given strategy.

    'rotate' uses a single engine per cycle, advancing through the list;
    'all' and 'fallback' both consider every enabled engine (fallback stops
    early at scrape time once one yields items).
    """
    if strategy == "rotate" and sources:
        return [sources[cycle % len(sources)]]
    return sources


def _yielded_results(logs: list[ScraperLog]) -> bool:
    return any(log.success and log.entry_count > 0 for log in logs)


def scrape_topic(
    make_page: Callable[[], Page],
    sources: list[SearchSource],
    topic: str,
    *,
    strategy: str = "fallback",
    cycle: int = 0,
    ordering: Ordering = Ordering.DATE,
    recency: Recency = Recency.HOUR,
    max_result_pages: int | None = None,
    cooldown: EngineCooldownTracker | None = None,
) -> tuple[list[NewsEntry], list[ScraperLog]]:
    """Scrape one topic across the configured engines per ``strategy``.

    A fresh page is created (via ``make_page``) and closed per engine so each
    engine navigates independently. Returns the combined entries and the
    per-engine, per-page ScraperLogs. Cross-engine duplicates are resolved
    downstream by the URL-derived news id, so engines can safely overlap.

    When a ``cooldown`` tracker is supplied, an engine that recently threw a
    throttle/block signal is skipped until its backoff window expires, then
    probed once; see ``scraper/cooldown.py``. Skipped engines produce no logs
    this cycle, so under ``fallback`` the chain naturally falls through to the
    next healthy engine.
    """
    all_entries: list[NewsEntry] = []
    all_logs: list[ScraperLog] = []

    for source in _engines_for_cycle(sources, strategy, cycle):
        if cooldown is not None:
            decision = cooldown.decide(source.name)
            if decision == "skip":
                logger.info(
                    f"Skipping {source.name} for '{topic}' — cooling down "
                    f"(~{cooldown.remaining(source.name):.0f}s until next probe)"
                )
                continue
            if decision == "probe":
                logger.info(f"Probing {source.name} for '{topic}' after cooldown")

        page = make_page()
        try:
            entries, logs = scrape_news(
                page,
                source,
                topic,
                ordering=ordering,
                recency=recency,
                max_result_pages=max_result_pages,
            )
        finally:
            page.close()

        if cooldown is not None:
            cooldown.record(source.name, logs)

        all_entries.extend(entries)
        all_logs.extend(logs)

        if strategy == "fallback" and _yielded_results(logs):
            break

    return all_entries, all_logs


def scrape_news(
    page: Page,
    source: SearchSource,
    topic: str,
    *,
    ordering: Ordering = Ordering.DATE,
    recency: Recency = Recency.HOUR,
    max_result_pages: int | None = None,
) -> tuple[list[NewsEntry], list[ScraperLog]]:
    """Scrape news entries for a topic from one engine, across result pages.

    Iterates pages until no entries remain, an error occurs, or
    ``max_result_pages`` is reached. Defaults reproduce the original behaviour
    (newest-first, past hour).

    Returns (entries oldest-to-newest, one ScraperLog per page attempt).
    """
    result_page_number = 1
    all_entries: list[NewsEntry] = []
    scraper_logs: list[ScraperLog] = []

    while True:
        if max_result_pages is not None and result_page_number > max_result_pages:
            break

        entries, scraper_log = _scrape_one_page(
            page, source, topic, result_page_number, ordering, recency
        )
        scraper_logs.append(scraper_log)
        if len(entries) == 0 or not scraper_log.success:
            break

        all_entries.extend(entries)
        result_page_number += 1

    # Reverse to chronological order (oldest to newest).
    all_entries.reverse()
    scraper_logs.reverse()
    return all_entries, scraper_logs


def _scrape_one_page(
    page: Page,
    source: SearchSource,
    topic: str,
    result_page_number: int,
    ordering: Ordering,
    recency: Recency,
) -> tuple[list[NewsEntry], ScraperLog]:
    url = source.build_url(
        topic, ordering=ordering, recency=recency, page=result_page_number
    )
    logger.info(f"Scraping {source.name} for topic: {topic}")

    # Fetch latency: wall-clock of the navigation itself (page.goto through
    # domcontentloaded). Deliberately excludes the anti-detection settle/scroll
    # waits below, so it reflects real results-page load time, not the
    # intentional human-simulation delay. Read by _log at call time; stays None
    # if navigation never started.
    fetch_ms: int | None = None

    def _log(**kwargs):
        # Every log row carries the measured fetch latency unless the caller
        # explicitly overrides it (e.g. a pre-navigation failure path).
        kwargs.setdefault("duration_ms", fetch_ms)
        return ScraperLog.create_new(topic=topic, engine=source.name, **kwargs)

    try:
        fetch_start = time.perf_counter()
        try:
            response: Response | None = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=anti_detection_config.nav_timeout_ms,
            )
        finally:
            fetch_ms = round((time.perf_counter() - fetch_start) * 1000)

        if response is None:
            logger.error(
                f"No response received for topic '{topic}' - Navigation failed"
            )
            logger.error(f"URL: {url}")
            return (
                [],
                _log(
                    success=False,
                    error_message="No response received - Navigation failed",
                ),
            )

        response_status: int = response.status

        if anti_detection_config.http_error_handling_enabled:
            if response_status in anti_detection_config.monitored_http_codes:
                logger.error(
                    f"HTTP ERROR {response_status} for topic '{topic}' - Request failed"
                )
                logger.error(f"URL: {page.url}")
                if response_status == 429:
                    logger.error("Rate limiting detected - Too many requests")
                elif response_status in (403, 503):
                    logger.error(
                        "Access blocked - May need to adjust scraping strategy"
                    )
                return (
                    [],
                    _log(success=False, http_status_code=response_status),
                )
        elif _is_http_error(response_status):
            logger.error(
                f"HTTP ERROR {response_status} for topic '{topic}' - Request failed"
            )
            return (
                [],
                _log(success=False, http_status_code=response_status),
            )

        # Let dynamic content load.
        page.wait_for_timeout(
            random.randint(
                anti_detection_config.page_settle_min_ms,
                anti_detection_config.page_settle_max_ms,
            )
        )

        # Simulate human-like reading behaviour. The scroll/mouse ranges come
        # from anti_detection.page_interaction.human_simulation so block-risk vs.
        # speed is tunable without a code change.
        hs = anti_detection_config
        try:
            for _ in range(random.randint(hs.scroll_steps_min, hs.scroll_steps_max)):
                page.evaluate(
                    f"window.scrollBy(0, {random.randint(hs.scroll_distance_min, hs.scroll_distance_max)})"
                )
                page.wait_for_timeout(
                    random.randint(hs.scroll_wait_min, hs.scroll_wait_max)
                )
            page.mouse.move(
                random.randint(hs.mouse_x_min, hs.mouse_x_max),
                random.randint(hs.mouse_y_min, hs.mouse_y_max),
                steps=random.randint(hs.mouse_steps_min, hs.mouse_steps_max),
            )
            if random.random() < hs.scroll_back_chance:
                page.evaluate(
                    f"window.scrollBy(0, -{random.randint(hs.scroll_back_distance_min, hs.scroll_back_distance_max)})"
                )
                page.wait_for_timeout(
                    random.randint(hs.scroll_back_wait_min, hs.scroll_back_wait_max)
                )
        except Exception:
            pass

        # Wait for the engine's results container, but don't fail if missing.
        try:
            page.wait_for_selector(
                source.ready_selector,
                timeout=anti_detection_config.selector_timeout_ms,
            )
        except Exception as e:
            logger.warning(f"Selector wait timeout, proceeding anyway: {e}")

        content: str = page.content()

        # Engine-specific signal first, then the generic "redirected off the
        # results page" backup (catches /sorry/-style block redirects for any
        # engine that declares its results location).
        blocked_reason = source.detect_block(
            page.url, content
        ) or source.redirected_off_results(page.url)
        if blocked_reason:
            logger.error(f"{source.name} blocked the request - {blocked_reason}")
            logger.error(f"Response preview (first 500 chars): {content[:500]}")
            return (
                [],
                _log(
                    success=False,
                    http_status_code=response_status,
                    error_message=f"{source.name} blocked: {blocked_reason}",
                ),
            )

        soup = BeautifulSoup(content, "lxml")
        items = source.find_items(soup)
        logger.info(f"Found {len(items)} potential news items")

        entries: list[NewsEntry] = []
        for item in items:
            try:
                entry = source.parse_item(item, topic)
                if entry:
                    # Stamp the producing engine so the insert can attribute
                    # this (topic, article) match to it in topic_news_engines.
                    entry.engine = source.name
                    entries.append(entry)
            except Exception as e:
                logger.debug(f"Error parsing news item: {e}")
                continue

        if entries:
            logger.info(f"Successfully parsed {len(entries)} news entries")
        else:
            # HTTP 200 and detect_block clear, yet nothing parsed: a possible
            # silent block or selector rot. Log enough (final URL + title) to
            # characterize it later without a deliberate flood — this
            # self-documenting log is what surfaced Brave's/Google's block
            # pages during the 2026-06-17 run (docs/BLOCK_SIGNAL_FINDINGS.md). A
            # sustained run of these flips server-side health to "parsing".
            title = soup.title.get_text(strip=True) if soup.title else ""
            logger.warning(
                f"{source.name}/{topic}: HTTP {response_status} but 0 items parsed "
                f"({len(items)} candidates); final_url={page.url} title={title!r}"
            )
        return (
            entries,
            _log(
                success=True,
                http_status_code=response_status,
                entry_count=len(entries),
            ),
        )

    except Exception as e:
        logger.error(f"Error scraping news for topic '{topic}'")
        logger.error(f"Exception type: {type(e).__name__}")
        logger.error(f"Full traceback:\n{traceback.format_exc()}")
        return (
            [],
            _log(
                success=False,
                error_message=f"{type(e).__name__}: {str(e)}",
            ),
        )


def _is_http_error(response_status: int) -> bool:
    """True for any status code >= 400 (client/server errors)."""
    return response_status >= 400
