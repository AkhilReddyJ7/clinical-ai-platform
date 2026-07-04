import hmac

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from shared.config.settings import get_settings

API_KEY_HEADER_NAME = "X-API-Key"

_api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)


def _parse_api_keys(raw: str) -> dict[str, str]:
    """Parses `label:key,label:key` into {key: label} (ADR-0026).

    Each comma-separated entry is split on its *first* colon; both label
    and key are required -- an entry with no colon is a configuration
    error and is silently dropped (contributing to `get_valid_api_keys()`
    returning fewer keys than configured, not to a value-less/anonymous
    entry), consistent with this project's fail-closed posture: better an
    operator notices a caller can't authenticate than have that caller
    silently attributed to nothing.
    """
    keys: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        label, _, key = entry.partition(":")
        label, key = label.strip(), key.strip()
        if label and key:
            keys[key] = label
    return keys


def _resolve_caller(candidate: str, valid_keys: dict[str, str]) -> str | None:
    """Constant-time per-candidate comparison (ADR-0026): matching must
    never short-circuit via a dict/hash lookup on the presented key
    itself, which would leak timing information about which keys are
    valid. Every configured key is compared against `candidate` with
    `hmac.compare_digest` regardless of whether an earlier one already
    matched.
    """
    matched_label: str | None = None
    for key, label in valid_keys.items():
        if hmac.compare_digest(candidate, key):
            matched_label = label
    return matched_label


def get_valid_api_keys() -> dict[str, str]:
    return _parse_api_keys(get_settings().api_keys)


async def require_api_key(
    api_key: str | None = Security(_api_key_header),
    valid_keys: dict[str, str] = Depends(get_valid_api_keys),
) -> str:
    if not valid_keys:
        # Fail closed: no configured keys means nothing can authenticate,
        # rather than silently allowing all requests through.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API key auth is not configured",
        )
    caller = _resolve_caller(api_key, valid_keys) if api_key else None
    if caller is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing API key",
        )
    return caller
