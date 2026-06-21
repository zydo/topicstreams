"""Data models for TopicStreams application.

This module defines Pydantic models for database entities:
- Topic: Represents a news topic to track
- NewsEntry: Represents a scraped news article
- ScraperLog: Represents a scraper execution log entry
"""

from datetime import datetime
from enum import Enum
from urllib.parse import urlparse

from pydantic import BaseModel, Field


class Topic(BaseModel):
    id: int | None = Field(None, description="Primary key (auto-generated)")
    name: str = Field(..., description="Topic name")
    created_at: datetime | None = Field(
        None, description="Timestamp when topic was created"
    )
    is_active: bool = Field(True, description="Whether the topic is active")

    @classmethod
    def from_db_row(cls, row: dict) -> "Topic":
        return cls(**row)


class NewsEntry(BaseModel):
    id: int | None = Field(None, description="Primary key (auto-generated)")
    topic: str = Field(..., description="Topic of the news entry")
    title: str = Field(..., description="Title of the news article")
    url: str = Field(..., description="URL of the news article")
    domain: str = Field(
        ..., description="Domain of the news article (extracted from URL)"
    )
    source: str | None = Field(None, description="Source of the news article")
    snippet: str | None = Field(
        None,
        description="Short excerpt/blurb shown under the headline (descriptive "
        "only — never part of the article identity)",
    )
    scraped_at: datetime | None = Field(
        None, description="Timestamp when entry was scraped"
    )
    # Scrape-side: which engine produced this parsed entry (stamped by the
    # runner). Persisted into topic_news_engines on insert.
    engine: str | None = Field(
        None, description="Search engine that scraped this entry (insert side)"
    )
    # Feed-side: every engine that has surfaced this feed event, populated when
    # reading the feed (empty on freshly scraped entries).
    engines: list[str] = Field(
        default_factory=list,
        description="Engines that surfaced this feed event (read side)",
    )

    @classmethod
    def create_new(
        cls,
        topic: str,
        title: str,
        url: str,
        source: str | None = None,
        engine: str | None = None,
        snippet: str | None = None,
    ) -> "NewsEntry":
        """Create a new NewsEntry for insertion (without id and scraped_at)"""

        return cls(
            id=None,
            topic=topic,
            title=title,
            url=url,
            domain=cls._extract_domain(url),
            source=source,
            scraped_at=None,
            engine=engine,
            snippet=snippet,
        )

    @classmethod
    def from_db_row(cls, row: dict) -> "NewsEntry":
        return cls(**row)

    @classmethod
    def _extract_domain(cls, url: str) -> str:
        """Extract domain from URL (e.g., 'https://example.com/path' -> 'example.com')"""
        parsed = urlparse(url)
        domain = parsed.netloc
        # Remove 'www.' prefix if present for better matching
        if domain.startswith("www."):
            domain = domain[4:]
        return domain


class WebResultKind(str, Enum):
    """The SERP component a WebResult came from. A general web-search page is
    heterogeneous, so each result carries the kind of block it was parsed from."""

    ANSWER = "answer"  # featured snippet / direct answer box (text + source)
    KNOWLEDGE_PANEL = "knowledge_panel"  # entity summary card
    WIDGET = "widget"  # structured direct-answer widget (weather, etc.)
    ORGANIC = "organic"  # a standard organic web result
    TOP_STORY = "top_story"  # a card in the Top stories / Also in the news pack
    DISCUSSION = "discussion"  # a forum/social post (Reddit, X, Medium, …)
    VIDEO = "video"  # a video-carousel result


class WebResult(BaseModel):
    """One result parsed from a general web-search (WEB vertical) page.

    Uniform across every SERP component (organic, top stories, videos, PAA) — the
    ``kind`` discriminates which block it came from. Distinct from ``NewsEntry``:
    web results are served on demand (a thin cache, not persisted to the feed),
    can lack a URL (People-also-ask), and span more component types.
    """

    kind: WebResultKind = Field(..., description="Which SERP component this is")
    title: str = Field(..., description="Result title / headline / question")
    url: str | None = Field(None, description="Destination URL (None for PAA)")
    domain: str | None = Field(None, description="Domain derived from the URL")
    source: str | None = Field(None, description="Publisher or channel name")
    snippet: str | None = Field(None, description="Description blurb, if any")
    # Stamped by the runner: which engine produced this result.
    engine: str | None = Field(None, description="Search engine that produced it")

    @classmethod
    def create(
        cls,
        kind: "WebResultKind",
        title: str,
        url: str | None = None,
        source: str | None = None,
        snippet: str | None = None,
    ) -> "WebResult":
        return cls(
            kind=kind,
            title=title,
            url=url,
            domain=NewsEntry._extract_domain(url) if url else None,
            source=source,
            snippet=snippet,
        )


class ScraperLog(BaseModel):
    id: int | None = Field(None, description="Primary key (auto-generated)")
    topic: str = Field(..., description="Topic that was scraped")
    scraped_at: datetime | None = Field(
        None, description="Timestamp when scrape was attempted"
    )
    success: bool = Field(True, description="Whether the scrape succeeded")
    http_status_code: int | None = Field(
        None, description="HTTP status code if available (e.g., 200, 429, 403)"
    )
    error_message: str | None = Field(
        None, description="Error message from exception if scrape failed"
    )
    entry_count: int = Field(
        0, description="Number of news entries parsed from this scrape"
    )
    engine: str = Field(
        "google", description="Search engine this scrape used (e.g. 'google')"
    )
    # Wall-clock to fetch+load this results page (ms), excluding the
    # anti-detection settle/scroll waits. None when unmeasured (legacy rows,
    # or a failure before navigation completed).
    duration_ms: int | None = Field(
        None, description="Results-page fetch+load latency in milliseconds"
    )

    @classmethod
    def create_new(
        cls,
        topic: str,
        success: bool,
        scraped_at: datetime | None = None,
        http_status_code: int | None = None,
        error_message: str | None = None,
        entry_count: int = 0,
        engine: str = "google",
        duration_ms: int | None = None,
    ) -> "ScraperLog":
        """Create a new ScraperLog for insertion (without id)"""
        return cls(
            id=None,
            topic=topic,
            scraped_at=scraped_at or datetime.now(),
            success=success,
            http_status_code=http_status_code,
            error_message=error_message,
            entry_count=entry_count,
            engine=engine,
            duration_ms=duration_ms,
        )

    @classmethod
    def from_db_row(cls, row: dict) -> "ScraperLog":
        return cls(**row)
