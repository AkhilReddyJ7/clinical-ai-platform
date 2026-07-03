# 0013: Run extraction (and storage reads) off the event loop

- **Status:** Accepted
- **Date:** 2026-07-03

## Context

`TesseractExtractionPipeline.extract()` is a synchronous method — real OCR
work (subprocess calls to `tesseract`, PDF rasterization) takes real
wall-clock time, not just CPU time. `process_document`
(`apps/api/routers/documents.py`) called it directly, unwrapped, inside an
`async def` endpoint.

Verified empirically, not assumed: generated a real 25-page PDF with text
on every page, uploaded it, started `/process`, and fired concurrent
`/health` requests while it ran. The first `/health` call took **19.6
seconds** — it did not return until the entire OCR job finished. A single
large document being processed made the whole service unresponsive to
every other client, including the health-check probe that Docker's own
container healthcheck ([0007](0007-ci-validates-docker-build-and-boot.md))
depends on to decide whether the container is alive.

## Decision

Wrapped the two blocking calls in `process_document` —
`storage.read(...)` and `extraction_pipeline.extract(...)` — in
`starlette.concurrency.run_in_threadpool`, already a transitive dependency
via FastAPI/Starlette (no new dependency added). This moves the blocking
work to a worker thread and awaits it, freeing the event loop to keep
serving other requests while it runs.

`storage.read()` was included even though `LocalFileStorage`'s disk reads
are fast today — the `StorageBackend` interface is explicitly designed to
be swappable to a network backend (S3/GCS) later
([0001](0001-modular-monolith-over-microservices.md)/
[0002](0002-interface-first-pipeline-stages.md)), where a synchronous
network read would have the exact same blocking problem. Cheap to fix now,
consistent with the interface's stated purpose.

`validation_pipeline.validate()` was deliberately left as a direct call —
today's validators (regex matching, dict lookups) are genuinely
microsecond-fast, and wrapping them would add threadpool overhead for no
benefit. Noted at the point of first Sprint-2 discussion that this would
need the same treatment if a future validator (e.g. an LLM-based check)
becomes slow.

## Consequences

- Re-ran the exact same 25-page-PDF-plus-concurrent-`/health` test after
  the fix: the `/health` call dropped from 19.6s to 0.026s — about 750x
  faster. The `/process` call itself still takes the same ~19.6s (this
  doesn't make OCR faster, it stops OCR from blocking everything else).
  Also verified two documents processed concurrently complete correctly
  with no cross-contamination between them.
- **A dead end worth recording:** attempted an automated regression test
  using a deterministic fake pipeline (`time.sleep(0.5)`) and two threads
  hitting the FastAPI `TestClient` concurrently, asserting `/health`
  returns well before the slow call finishes. The test passed — with the
  fix reverted too. `TestClient`'s execution model doesn't faithfully
  reproduce single-event-loop blocking the way a real running
  uvicorn/Starlette server does, so the test didn't actually discriminate
  between fixed and broken. A test that passes regardless of the bug is
  worse than no test — it looks like coverage without providing any.
  Removed it rather than ship false confidence. The manual, live-server
  verification above (concrete before/after timing numbers, reproduced
  twice) is the actual proof for this fix; there is currently no
  automated regression guard against this specific class of bug
  recurring — a known, named gap, not a silent one.
