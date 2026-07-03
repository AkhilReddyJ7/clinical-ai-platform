# 0017: Reject unauthenticated requests before the body is ever read

- **Status:** Accepted
- **Date:** 2026-07-03
- **Corrects a claim in:** [0014](0014-stream-upload-size-enforcement.md)

## Context

Testing resource-exhaustion angles (following [0016](0016-cap-per-document-ocr-resource-cost.md)),
tried an unauthenticated 100MB upload against the live stack expecting a
near-instant `401`. It took **~260ms** — the same order of magnitude as an
*authenticated* oversized upload's `413`. That's not consistent with an
early rejection; it's consistent with the full 100MB having already been
received.

Root cause: `file: UploadFile = File(...)` is a *declared parameter* on
`upload_document`. FastAPI resolves declared parameters — including ones
that require reading the body, like `File(...)` — as part of building the
full dependency tree for the request, and it does this regardless of
whether other dependencies (like the router-level `require_api_key`) are
going to fail. By the time `require_api_key`'s `HTTPException` is raised,
Starlette has already read and multipart-parsed the entire request body.
An unauthenticated caller could force the server to receive and spool an
arbitrarily large body on every request — undermining the API key's
stated purpose ("the natural gate before adding anything that costs money
per call" per the auth commit) since the gate didn't actually prevent
resource consumption, just downstream processing.

This also means **[0014](0014-stream-upload-size-enforcement.md)'s
"never buffers more than roughly one chunk past the limit" claim is less
complete than stated.** Verified directly: an *authenticated* 100MB
upload against the 25MB limit also took ~290ms — Starlette's own
multipart parser (via `SpooledTemporaryFile`, not application code) fully
receives the body before `_read_upload_within_limit` (which only runs
inside the endpoint body, after `UploadFile` is already resolved) ever
gets a chance to reject early. 0014's streaming fix is still real and
still valuable — it avoids a second full in-memory `bytes` copy on top of
what Starlette already buffered/spooled — but it does not prevent the
network transfer and Starlette-level buffering from completing first,
for either authenticated or unauthenticated oversized uploads.

## Decision

Added `modules/auth/middleware.py::ApiKeyGateMiddleware`, a raw ASGI
middleware (not `BaseHTTPMiddleware`, to avoid its own known body-buffering
quirks) registered globally but scoped internally to a `protected_prefix`
(`/documents`). It inspects only `scope["headers"]` — available before
`receive()` is ever called — and short-circuits with `401`/`503` without
ever invoking `receive()`, meaning it never pulls body bytes off the ASGI
channel at all. This runs *before* FastAPI's routing/dependency-resolution
layer, closing the gap no amount of `Depends()` ordering could fix.

`require_api_key` (`modules/auth/api_key.py`) stays in place — it's what
makes the requirement appear in the OpenAPI schema (verified:
`security: [{"APIKeyHeader": []}]` still present on `POST /documents`) and
is a harmless redundant check for any request that does reach it.

Reusable auth logic (`get_valid_api_keys`, `_matches_any`) is shared
between the middleware and the FastAPI dependency, not duplicated — one
source of truth for what counts as a valid key.

**Test-infrastructure consequence:** the middleware calls
`get_valid_api_keys()` as a plain function call, not via `Depends()` — it
never participates in FastAPI's `app.dependency_overrides`, by design
(that's specific to the DI layer this middleware runs ahead of). Tests
that previously overrode `get_valid_api_keys` via `dependency_overrides`
switched to `monkeypatch.setattr(get_settings(), "api_keys", ...)`
instead, mutating the one thing both the middleware and the route
dependency actually read.

## Consequences

- Verified by reproducing the exact original measurement: an
  unauthenticated 100MB upload now rejects in ~8-12ms (down from ~260ms) —
  the body is never transferred. A wrong-key request behaves identically.
- Added a direct ASGI-level unit test suite
  (`tests/unit/test_auth_middleware.py`) asserting `receive()` is never
  called on the reject paths, using hand-built mock scope/receive/send
  rather than going through `TestClient` — more precise for this specific
  claim than an HTTP-level test would be, and avoids the exact kind of
  test-methodology trap found in
  [0013](0013-run-extraction-off-the-event-loop.md) (`TestClient` not
  faithfully reproducing real ASGI-level behavior). Verified this test
  actually discriminates: deliberately broke the middleware (added a
  premature `receive()` call), confirmed 3 of the tests failed, then
  reverted.
- Confirmed no regressions: `/health` and `/` still work without a key,
  legitimate authenticated uploads still succeed end-to-end, OpenAPI still
  documents the security requirement.
- **Still not solved, named rather than silently accepted:** an
  *authenticated* client sending an oversized file still causes the full
  body to be received and spooled by Starlette before
  [0014](0014-stream-upload-size-enforcement.md)'s size check can reject
  it — fully closing that would need either a custom ASGI-level body
  reader with an early cutoff for all requests (not just unauthenticated
  ones) or a lower-level integration with Starlette's multipart parsing
  than this codebase currently has. A real, valid API key already implies
  some level of trust the fully-anonymous case doesn't, so this was judged
  a smaller, acceptable residual risk — but it is residual risk, not
  resolved risk.
