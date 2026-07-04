from collections.abc import Awaitable, Callable

import pytest
from starlette.types import Message, Receive, Scope, Send

from modules.auth.middleware import ApiKeyGateMiddleware
from shared.config.settings import get_settings


async def _noop_app(scope: Scope, receive: Receive, send: Send) -> None:
    # Stands in for "the rest of the app" — if this runs, the middleware
    # let the request through.
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _scope(path: str, headers: list[tuple[bytes, bytes]]) -> Scope:
    return {"type": "http", "path": path, "headers": headers}


def _tracking_receive() -> tuple[Receive, list[bool]]:
    called: list[bool] = []

    async def receive() -> Message:
        called.append(True)
        return {"type": "http.request", "body": b"", "more_body": False}

    return receive, called


def _collecting_send() -> tuple[Send, list[Message]]:
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    return send, sent


@pytest.mark.asyncio
async def test_rejects_missing_key_without_ever_reading_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The actual regression this guards: verified live (Docker) that an
    # unauthenticated 100MB upload took ~260ms to reject — consistent with
    # the full body having already been transferred, not an early
    # rejection. This test proves the fix at the ASGI level directly:
    # receive() (which is what would pull body bytes off the wire) must
    # never be called when auth fails.
    monkeypatch.setattr(get_settings(), "api_keys", "alice:valid-key")
    middleware: Callable[[Scope, Receive, Send], Awaitable[None]] = ApiKeyGateMiddleware(
        _noop_app, protected_prefix="/documents"
    )
    receive, receive_calls = _tracking_receive()
    send, sent = _collecting_send()

    await middleware(_scope("/documents", headers=[]), receive, send)

    assert receive_calls == []
    assert sent[0]["status"] == 401


@pytest.mark.asyncio
async def test_rejects_wrong_key_without_ever_reading_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "api_keys", "alice:valid-key")
    middleware: Callable[[Scope, Receive, Send], Awaitable[None]] = ApiKeyGateMiddleware(
        _noop_app, protected_prefix="/documents"
    )
    receive, receive_calls = _tracking_receive()
    send, sent = _collecting_send()

    await middleware(_scope("/documents", headers=[(b"x-api-key", b"wrong-key")]), receive, send)

    assert receive_calls == []
    assert sent[0]["status"] == 401


@pytest.mark.asyncio
async def test_fails_closed_without_reading_body_when_no_keys_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "api_keys", "")
    middleware: Callable[[Scope, Receive, Send], Awaitable[None]] = ApiKeyGateMiddleware(
        _noop_app, protected_prefix="/documents"
    )
    receive, receive_calls = _tracking_receive()
    send, sent = _collecting_send()

    await middleware(_scope("/documents", headers=[]), receive, send)

    assert receive_calls == []
    assert sent[0]["status"] == 503


@pytest.mark.asyncio
async def test_fails_closed_when_configured_keys_have_no_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ADR-0026: a bare key with no `label:` prefix is a configuration
    # error and is dropped during parsing, not silently accepted -- with
    # nothing left, this behaves exactly like an empty API_KEYS.
    monkeypatch.setattr(get_settings(), "api_keys", "valid-key")
    middleware: Callable[[Scope, Receive, Send], Awaitable[None]] = ApiKeyGateMiddleware(
        _noop_app, protected_prefix="/documents"
    )
    receive, receive_calls = _tracking_receive()
    send, sent = _collecting_send()

    await middleware(_scope("/documents", headers=[(b"x-api-key", b"valid-key")]), receive, send)

    assert receive_calls == []
    assert sent[0]["status"] == 503


@pytest.mark.asyncio
async def test_allows_correct_key_through_to_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "api_keys", "alice:valid-key")
    middleware: Callable[[Scope, Receive, Send], Awaitable[None]] = ApiKeyGateMiddleware(
        _noop_app, protected_prefix="/documents"
    )
    receive, _receive_calls = _tracking_receive()
    send, sent = _collecting_send()

    await middleware(_scope("/documents", headers=[(b"x-api-key", b"valid-key")]), receive, send)

    assert sent[0]["status"] == 200


@pytest.mark.asyncio
async def test_stashes_the_resolved_caller_label_on_scope_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "api_keys", "alice:valid-key,bob:other-key")
    middleware: Callable[[Scope, Receive, Send], Awaitable[None]] = ApiKeyGateMiddleware(
        _noop_app, protected_prefix="/documents"
    )
    receive, _receive_calls = _tracking_receive()
    send, sent = _collecting_send()
    scope = _scope("/documents", headers=[(b"x-api-key", b"other-key")])

    await middleware(scope, receive, send)

    assert sent[0]["status"] == 200
    assert scope["state"]["caller"] == "bob"


@pytest.mark.asyncio
async def test_does_not_gate_paths_outside_the_protected_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "api_keys", "alice:valid-key")
    middleware: Callable[[Scope, Receive, Send], Awaitable[None]] = ApiKeyGateMiddleware(
        _noop_app, protected_prefix="/documents"
    )
    receive, _receive_calls = _tracking_receive()
    send, sent = _collecting_send()

    # No X-API-Key header at all — still lets it through, since /health
    # isn't under the protected prefix.
    await middleware(_scope("/health", headers=[]), receive, send)

    assert sent[0]["status"] == 200
