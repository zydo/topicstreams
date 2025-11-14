"""Data models for TopicStreams application.

This module defines Pydantic models for database entities:
- Topic: Represents a news topic to track
- NewsEntry: Represents a scraped news article
- ScraperLog: Represents a scraper execution log entry
"""

from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field


class Topic(BaseModel):
    id: Optional[int] = Field(None, description="Primary key (auto-generated)")
    name: str = Field(..., description="Topic name")
    created_at: Optional[datetime] = Field(
        None, description="Timestamp when topic was created"
    )
    is_active: bool = Field(True, description="Whether the topic is active")

    @classmethod
    def from_db_row(cls, row: dict) -> "Topic":
        return cls(**row)


class NewsEntry(BaseModel):
    id: Optional[int] = Field(None, description="Primary key (auto-generated)")
    topic: str = Field(..., description="Topic of the news entry")
    title: str = Field(..., description="Title of the news article")
    url: str = Field(..., description="URL of the news article")
    domain: str = Field(
        ..., description="Domain of the news article (extracted from URL)"
    )
    source: Optional[str] = Field(None, description="Source of the news article")
    scraped_at: Optional[datetime] = Field(
        None, description="Timestamp when entry was scraped"
    )

    @classmethod
    def create_new(
        cls,
        topic: str,
        title: str,
        url: str,
        source: Optional[str] = None,
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


class ScraperLog(BaseModel):
    id: Optional[int] = Field(None, description="Primary key (auto-generated)")
    topic: str = Field(..., description="Topic that was scraped")
    scraped_at: Optional[datetime] = Field(
        None, description="Timestamp when scrape was attempted"
    )
    success: bool = Field(True, description="Whether the scrape succeeded")
    http_status_code: Optional[int] = Field(
        None, description="HTTP status code if available (e.g., 200, 429, 403)"
    )
    error_message: Optional[str] = Field(
        None, description="Error message from exception if scrape failed"
    )

    @classmethod
    def create_new(
        cls,
        topic: str,
        success: bool,
        scraped_at: datetime,
        http_status_code: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> "ScraperLog":
        """Create a new ScraperLog for insertion (without id)"""
        return cls(
            id=None,
            topic=topic,
            scraped_at=scraped_at,
            success=success,
            http_status_code=http_status_code,
            error_message=error_message,
        )

    @classmethod
    def from_db_row(cls, row: dict) -> "ScraperLog":
        return cls(**row)
