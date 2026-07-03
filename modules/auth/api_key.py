import hmac

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from shared.config.settings import get_settings

API_KEY_HEADER_NAME = "X-API-Key"

_api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)


def _parse_api_keys(raw: str) -> frozenset[str]:
    return frozenset(key.strip() for key in raw.split(",") if key.strip())


def _matches_any(candidate: str, valid_keys: frozenset[str]) -> bool:
    return any(hmac.compare_digest(candidate, key) for key in valid_keys)


def get_valid_api_keys() -> frozenset[str]:
    return _parse_api_keys(get_settings().api_keys)


async def require_api_key(
    api_key: str | None = Security(_api_key_header),
    valid_keys: frozenset[str] = Depends(get_valid_api_keys),
) -> None:
    if not valid_keys:
        # Fail closed: no configured keys means nothing can authenticate,
        # rather than silently allowing all requests through.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API key auth is not configured",
        )
    if not api_key or not _matches_any(api_key, valid_keys):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing API key",
        )
