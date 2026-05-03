"""API key authentication dependency."""

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from common.settings import settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(api_key: str = Security(_api_key_header)) -> None:
    """Require a valid X-API-Key header when API_KEY is configured.

    When API_KEY env var is not set, this dependency is a no-op (dev mode).
    """
    if settings.api_key is None:
        return
    if api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "UNAUTHORIZED", "message": "Invalid or missing API key", "status": "error"},
        )
