import json

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from modules.auth.api_key import API_KEY_HEADER_NAME, _resolve_caller, get_valid_api_keys


class ApiKeyGateMiddleware:
    """Rejects unauthenticated requests under `protected_prefix` before any
    request body is read from the ASGI receive channel.

    Without this, an unauthenticated POST to an endpoint with a File()/
    Form() parameter still gets its entire body received and spooled by
    Starlette during FastAPI's normal dependency resolution — the
    router-level `require_api_key` dependency (modules/auth/api_key.py)
    only raises its 401 *after* that resolution completes, by which point
    the body transfer already happened. Verified directly: an
    unauthenticated 100MB upload took ~260ms to reject (roughly the same
    order of magnitude as an authenticated-but-oversized one), not
    near-instant — consistent with the full body already having been
    transferred, not an early rejection.

    This middleware inspects only the header, available in `scope` before
    `receive()` is ever called, and short-circuits before FastAPI's
    routing/dependency layer runs at all. `require_api_key` stays in place
    too — it's what makes the requirement show up in the OpenAPI schema,
    and is a harmless redundant check for requests that do reach it.

    ADR-0026: also resolves the presented key to its configured label and
    stashes it on `scope["state"]["caller"]` (readable downstream as
    `Request.state.caller`), the same resolution rule `require_api_key`
    applies -- kept here too so a route can read the caller from either
    enforcement point without a second lookup.
    """

    def __init__(self, app: ASGIApp, protected_prefix: str) -> None:
        self.app = app
        self.protected_prefix = protected_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.protected_prefix):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        api_key = headers.get(API_KEY_HEADER_NAME)
        valid_keys = get_valid_api_keys()

        if not valid_keys:
            await _send_json(send, 503, {"detail": "API key auth is not configured"})
            return
        caller = _resolve_caller(api_key, valid_keys) if api_key else None
        if caller is None:
            await _send_json(send, 401, {"detail": "invalid or missing API key"})
            return

        scope.setdefault("state", {})["caller"] = caller
        await self.app(scope, receive, send)


async def _send_json(send: Send, status_code: int, payload: dict[str, str]) -> None:
    body = json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})
