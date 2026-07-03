# 0012: Graceful extraction failure handling

- **Status:** Accepted
- **Date:** 2026-07-03

## Context

While looking for the next Sprint 2 task, tested how real OCR
(`docs/adr/0010`) handles a file whose bytes don't match its declared
content type — e.g. uploaded as `image/png` but not actually a valid PNG.
Confirmed directly: `PIL.UnidentifiedImageError` (images) and
`pypdfium2.PdfiumError` (PDFs) both propagate all the way up as unhandled
exceptions, producing a raw `500 Internal Server Error` with no useful
detail. Worse, since the exception is raised before the document's status
is ever updated past `PROCESSING`, the document is left permanently stuck
in that state — there's no clean way to retry or even see that it failed,
short of reading server logs.

The upload endpoint doesn't validate that uploaded bytes actually match
their declared `content_type` (it only checks the content type is in the
allowed set), so this is a real, reachable failure mode, not a
hypothetical one — verified by uploading plain text bytes with
`content_type: image/png` and `content_type: application/pdf` through the
live stack.

## Decision

- Added `ExtractionError` to `modules/ocr/base.py`, alongside
  `ExtractionOutput`/`ExtractionPipeline` — the interface itself now
  declares that a pipeline may signal "couldn't process this at all,"
  distinct from a normal-but-empty extraction result.
- `TesseractExtractionPipeline` catches `PIL.UnidentifiedImageError` and
  `pypdfium2.PdfiumError` at the exact point they're raised (inside
  `_ocr_image`/`_ocr_pdf`) and re-raises as `ExtractionError` — callers
  don't need to know which OCR library raised what.
- `apps/api/routers/documents.py::process_document` catches
  `ExtractionError` specifically (not a bare `except Exception`, which
  would mask real bugs) and responds the same way a PHI-triggered failure
  already does: persists a clear failure marker instead of the real
  content, marks the document `status: failed`, logs a `WARNING` (not an
  unhandled traceback), and returns `200` with a `ProcessingResultOut` body
  explaining what happened — consistent with how every other
  "processing ran, here's the outcome" case is modeled, rather than a
  special HTTP error status for this one failure mode.

## Consequences

- Verified by reproducing the exact original crash: uploading
  mismatched-content PNG and PDF files through the live compose stack. Both
  now return `200` with `status: failed` and a clear `issues` message,
  instead of a `500` and a document stuck in `PROCESSING` forever.
  Confirmed logs show one clean `WARNING` line, not a raw traceback.
- Confirmed no regression on the happy path: a valid upload still persists
  real text/fields and reaches `status: validated` exactly as before.
- This pattern (catch pipeline-specific exceptions, re-raise as a shared
  domain error, handle once at the router) is the template a future
  LLM-based extraction pipeline should follow too — API errors, timeouts,
  and rate limits from an LLM provider are a similar "couldn't process
  this at all" failure mode, not a new category to invent.
