"""Configuration management for the TopicStreams API."""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # The .env / container env is shared with Docker Compose, which carries
        # vars the app doesn't model (e.g. HOST_PORT); ignore rather than reject.
        extra="ignore",
    )

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
        description="PostgreSQL password (required — no default)"
    )

    # ========== API ========== #

    api_port: int = Field(default=5000, ge=1, le=65535, description="API server port")

    # When set, POST/DELETE topic endpoints require this key in the X-API-Key header.
    # Leave unset to disable auth (useful for local development).
    api_key: str | None = Field(
        default=None, description="API key for write operations"
    )

    # Comma-separated list of allowed CORS origins, or '*' to allow all.
    cors_origins: str = Field(
        default="*", description="Comma-separated allowed CORS origins"
    )

    # ========== Database Pool ========== #

    db_pool_min_conn: int = Field(
        default=2, ge=1, description="Minimum DB connections in pool"
    )
    db_pool_max_conn: int = Field(
        default=10, ge=1, description="Maximum DB connections in pool"
    )

    # ========== Data Retention ========== #

    news_retention_days: int = Field(
        default=30, ge=1, description="Days to retain news entries before purging"
    )

    @property
    def cors_origins_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @field_validator(
        "postgres_db", "postgres_user", "postgres_host", "postgres_password"
    )
    @classmethod
    def validate_non_empty(cls, v, info):
        if v is not None and len(str(v).strip()) == 0:
            raise ValueError(f"{info.field_name} cannot be empty when provided")
        return str(v).strip() if v else v


# Fields are populated from the environment / .env, not constructor args, which
# the type checker can't model (it sees the required postgres_password as unset).
settings = Settings()  # type: ignore[call-arg]
