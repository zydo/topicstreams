"""Configuration management for the TopicStreams API."""

from pathlib import Path

import yaml
from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# Unified config file at the repo/container root (same path common.config uses).
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yml"


class YamlSettingsSource(PydanticBaseSettingsSource):
    """Optional ``api:`` section of ``config.yml`` for API tuning knobs.

    Precedence (highest → lowest): init args > environment > .env file > this
    YAML > field defaults. So secrets and Docker-startup vars stay in .env (they
    win), while this file is the preferred surface for tunable defaults — edit
    it without rebuilding, or override a single value from the environment.

    The file (and its ``api:`` section) is optional; a missing or malformed file
    simply falls through to the field defaults.
    """

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self._yaml_data: dict = self._load_yaml()

    @staticmethod
    def _load_yaml() -> dict:
        path = CONFIG_PATH
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except OSError, yaml.YAMLError:
            return {}
        if not isinstance(data, dict):
            return {}
        api = data.get("api")
        return api if isinstance(api, dict) else {}

    def get_field_value(self, field, field_name: str):
        return self._yaml_data.get(field_name), field_name, False

    def __call__(self) -> dict:
        # Keep only keys that map to a declared field (lower-snake, matching the
        # pydantic field names); unknown YAML keys are ignored, like extra="ignore".
        return {
            name: self._yaml_data[name]
            for name in self.settings_cls.model_fields
            if name in self._yaml_data
        }


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # The .env / container env is shared with Docker Compose, which carries
        # vars the app doesn't model (e.g. HOST_PORT); ignore rather than reject.
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        # Order is precedence (first wins); field defaults apply last, after all
        # sources. env / .env override the YAML so secrets and Docker-startup
        # vars win, while config/settings.yml is the preferred tuning surface.
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlSettingsSource(settings_cls),
            file_secret_settings,
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

    # Comma-separated bearer tokens that authenticate REST requests. When set,
    # every v1 REST endpoint requires `Authorization: Bearer <token>` matching
    # one of these. Leave unset to disable auth (useful for local development).
    # Env var: TOPICSTREAMS_API_KEY (e.g. "alice-tok,bob-tok,ci-tok").
    topicstreams_api_key: str | None = Field(
        default=None, description="Comma-separated bearer tokens for the REST API"
    )

    # How long the API caches the DB-backed api_keys set before re-reading it.
    # Adds/disables via the api_keys table go live within this window (no
    # restart). 0 disables caching (re-reads every request — handy in tests).
    api_key_cache_ttl_seconds: int = Field(
        default=30, ge=0, description="TTL for the cached DB-backed API key set"
    )

    # Comma-separated list of allowed CORS origins, or '*' to allow all.
    cors_origins: str = Field(
        default="*", description="Comma-separated allowed CORS origins"
    )

    # Number of trusted reverse proxies in front of the app. When > 0, the rate
    # limiter reads the client IP from X-Forwarded-For (the Nth entry from the
    # right); when 0, X-Forwarded-For is ignored and the direct peer IP is used.
    trusted_proxy_count: int = Field(
        default=0, ge=0, description="Trusted reverse proxies in front of the app"
    )

    # 'text' (human-readable) or 'json' (structured, one object per line).
    log_format: str = Field(default="text", description="Log format: text or json")

    # ========== API rate limiting ========== #

    rate_limit_calls: int = Field(
        default=120, ge=1, description="Max requests per client IP per period"
    )
    rate_limit_period: int = Field(
        default=60, ge=1, description="Rate-limit window in seconds"
    )
    rate_limit_max_tracked: int = Field(
        default=10000,
        ge=1,
        description="Max client IPs tracked before the stale-IP eviction sweep",
    )

    # ========== Database Pool ========== #

    db_pool_min_conn: int = Field(
        default=2, ge=1, description="Minimum DB connections in pool"
    )
    db_pool_max_conn: int = Field(
        default=10, ge=1, description="Maximum DB connections in pool"
    )

    # ========== Database connection tuning ========== #

    db_connect_timeout: int = Field(
        default=10, ge=1, description="Postgres connect timeout (seconds)"
    )
    db_keepalives_idle: int = Field(
        default=30, ge=1, description="Postgres TCP keepalive idle time (seconds)"
    )
    db_keepalives_interval: int = Field(
        default=10, ge=1, description="Postgres TCP keepalive interval (seconds)"
    )
    db_keepalives_count: int = Field(
        default=5, ge=1, description="Postgres TCP keepalive probes before giving up"
    )

    # ========== Data Retention ========== #

    news_retention_days: int = Field(
        default=30, ge=1, description="Days to retain news entries before purging"
    )

    # ========== Feed ========== #

    feed_engines_window_days: int = Field(
        default=7,
        ge=1,
        description="Engine filter lists engines seen within this many days",
    )
    feed_page_size: int = Field(
        default=20, ge=1, le=100, description="Default feed page size (UI)"
    )

    # ========== Scrape-health signal (api/v1/status) ========== #

    health_log_window: int = Field(
        default=30, ge=1, description="Recent scraper logs to read for health"
    )
    health_stale_min_seconds: int = Field(
        default=5 * 60, ge=1, description="Floor for the 'stalled' threshold"
    )
    health_stale_max_seconds: int = Field(
        default=30 * 60, ge=1, description="Ceiling for the 'stalled' threshold"
    )
    health_stale_default_seconds: int = Field(
        default=15 * 60,
        ge=1,
        description="'stalled' threshold when cadence can't be inferred (one log)",
    )

    # ========== DB retry ========== #

    db_retry_max_attempts: int = Field(
        default=3, ge=1, description="Attempts for transient DB errors"
    )
    db_retry_delay_seconds: float = Field(
        default=0.1, gt=0, description="Initial backoff between DB retries (s)"
    )

    # ========== Frontend (served via /api/v1/config) ========== #

    status_poll_interval_ms: int = Field(
        default=30_000, ge=1000, description="UI status-strip refresh interval"
    )
    ws_reconnect_base_ms: int = Field(
        default=5_000, ge=100, description="WebSocket reconnect backoff base"
    )
    ws_reconnect_max_ms: int = Field(
        default=30_000, ge=1000, description="WebSocket reconnect backoff cap"
    )

    @property
    def api_keys(self) -> frozenset[str]:
        """Parsed set of valid bearer tokens. Empty means auth is disabled.

        Tokens are comma-separated in ``topicstreams_api_key``; surrounding
        whitespace is trimmed and empty entries dropped, so a trailing comma or
        spacing around values is harmless.
        """
        raw = self.topicstreams_api_key
        if not raw:
            return frozenset()
        return frozenset(tok.strip() for tok in raw.split(",") if tok.strip())

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
