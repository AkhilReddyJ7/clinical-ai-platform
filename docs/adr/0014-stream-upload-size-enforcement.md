# 0014: Stream-enforce upload size limits instead of buffer-then-check

- **Status:** Accepted
- **Date:** 2026-07-03

## Context

`upload_document` (`apps/api/routers/documents.py`) called `await
file.read()` — materializing the entire upload into one `bytes` object in
application memory — and only checked it against `max_upload_size_bytes`
afterward. Flagged as a known gap as far back as the original Sprint 1
review, and picked up now specifically because it compounds with
[0013](0013-run-extraction-off-the-event-loop.md): a large-enough upload
could tie up real memory and a threadpool worker before ever being
rejected for size.

Also found while implementing this: there was no test at all covering the
`413` path. Nothing had ever exercised it.

## Decision

Added `_read_upload_within_limit(file, max_bytes)` — reads the
`UploadFile` in 1 MiB chunks via `await file.read(size)`, tracking a
running total and raising `413` as soon as that total exceeds the limit,
without ever accumulating more than roughly one chunk past the limit in
memory. `upload_document` now calls this instead of `file.read()`.

Also fixed a `StarletteDeprecationWarning` surfaced by finally exercising
this code path for the first time: `HTTP_413_REQUEST_ENTITY_TOO_LARGE` →
`HTTP_413_CONTENT_TOO_LARGE` (same status code, current constant name).

Added the two tests that should have existed already: a file over the
limit is rejected (`413`), and a file at exactly the limit succeeds
(`201`) — using `monkeypatch` to lower `max_upload_size_bytes` for the
test rather than constructing a genuinely huge file, so the test stays
fast while still exercising the real chunked-read/reject code path, not
just a size-arithmetic assertion.

## Consequences

- Verified against the live compose stack with real file sizes, not just
  the fast unit tests: a real 30MB upload against the default 25MB limit
  is rejected in ~0.2s (mostly network transfer time over localhost, not
  buffering), and a file at exactly the configured limit succeeds
  end-to-end.
- Starlette's own multipart parser already spools large form parts to a
  temp file past its own internal threshold, before this code even runs —
  this fix addresses the separate, additional materialization that
  `await file.read()` was doing in application code on top of that.
- Chunk size (1 MiB) is a hardcoded implementation constant, not a
  `Settings` field — an internal streaming detail, not something a
  deployment should need to tune.
