"""Configuration management for the TopicStreams API."""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ========== Database ========== #

    postgres_host: str = Field(
        default="postgres",
        description="PostgreSQL host (default: 'postgres' - the Docker service name)",
    )
    postgres_port: int = Field(
        default=5432, ge=1, le=65535, description="PostgreSQL port"
    )
    postgres_db: str = Field(default="newsdb", description="PostgreSQL database name")
    postgres_user: str = Field(default="newsuser", description="PostgreSQL username")
    postgres_password: str = Field(
        default="newspass", description="PostgreSQL password"
    )

    # ========== API ========== #

    api_port: int = Field(default=5000, ge=1, le=65535, description="API server port")

    # ========== Scraper ========== #

    scrape_interval: int = Field(
        default=60,
        description=(
            "Scraping interval in seconds, zero or negative means no intervals"
            " between loops (start next loop immediately)"
        ),
    )

    max_pages: int = Field(
        default=1,
        ge=1,
        description=(
            "Maximum number of pages to scrape in each cycles (default: 1). "
            "Increase if scrape_interval is long and high-volume topics exceed 10 new "
            "articles per cycle."
        ),
    )

    # Browser fingerprinting configuration
    browser_timezone: str = Field(
        default="America/Los_Angeles",
        description="Browser timezone (default: Los Angeles)",
    )

    browser_geolocation_latitude: float = Field(
        default=34.0522,
        description="Browser geolocation latitude (default: Los Angeles)",
    )

    browser_geolocation_longitude: float = Field(
        default=-118.2437,
        description="Browser geolocation longitude (default: Los Angeles)",
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False

    @field_validator(
        "postgres_db", "postgres_user", "postgres_host", "postgres_password"
    )
    @classmethod
    def validate_non_empty(cls, v, info):
        if v is not None and len(str(v).strip()) == 0:
            raise ValueError(f"{info.field_name} cannot be empty when provided")
        return str(v).strip() if v else v

    @field_validator("scrape_interval", "api_port", "postgres_port", "max_pages")
    @classmethod
    def convert_int(cls, v):
        if isinstance(v, str):
            v = int(v)
        return v


settings = Settings()
