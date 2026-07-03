# 0016: Cap per-document OCR resource cost

- **Status:** Accepted
- **Date:** 2026-07-03

## Context

Found while looking for the next Sprint 2 task, directly following on from
[0013](0013-run-extraction-off-the-event-loop.md) (which fixed OCR
blocking *other* requests, but not the unbounded cost of any *one*
request). Two concrete, reproduced gaps:

- **Decompression bomb images uncaught.** Pillow's own
  `Image.MAX_IMAGE_PIXELS` protection is active by default (never
  disabled anywhere in this codebase) and correctly raises
  `PIL.Image.DecompressionBombError` for a pixel count over its budget.
  But that exception is **not** a subclass of `UnidentifiedImageError` —
  verified directly via `__mro__` — so `_ocr_image`'s existing exception
  handling (added in [0012](0012-graceful-extraction-failure-handling.md))
  didn't catch it. Reproduced by lowering `Image.MAX_IMAGE_PIXELS`
  temporarily and opening a small image: the error propagated uncaught
  through the full pipeline, reproducing the exact "stuck in PROCESSING
  behind an unhandled crash" failure class 0012 was supposed to have
  closed — just for a different exception type.
- **No PDF page count limit.** `_ocr_pdf` iterated every page
  unconditionally. The 25-page-PDF measurement in
  [0013](0013-run-extraction-off-the-event-loop.md) showed roughly 0.8s/page
  — a 500-page PDF would tie up a threadpool worker for over 6 minutes,
  a 5,000-page one for over an hour, with no cap. Threadpool workers are
  a finite, shared resource (moving work off the event loop in 0013
  doesn't mean that work is free); enough large documents in flight
  concurrently would still exhaust them.

## Decision

- `_ocr_image` now also catches `Image.DecompressionBombError` alongside
  `UnidentifiedImageError`, re-raising both as the existing
  `ExtractionError` — same graceful-failure path as every other
  extraction failure, no new failure-handling code needed.
- Added `Settings.max_pdf_pages` (default 50 — real clinical documents are
  rarely more than a few dozen pages). `TesseractExtractionPipeline` now
  takes `max_pdf_pages` in its constructor (wired from `Settings` in
  `apps/api/dependencies.py`, matching the existing DI pattern used for
  `LocalFileStorage`'s `storage_root`) and checks the PDF's page count
  against it *before* processing any page, raising `ExtractionError`
  immediately if it's over the limit rather than processing 50 pages and
  then failing.

## Consequences

- Verified against the live compose stack: a real 51-page PDF is rejected
  in ~46ms (`status: failed`, clear message) instead of the ~40s it would
  have taken to actually OCR all 51 pages. Confirmed a normal small PDF
  still processes correctly (`status: validated`, real extracted text).
- Decompression bomb handling verified at the unit level (simulated by
  temporarily lowering `Image.MAX_IMAGE_PIXELS` rather than generating a
  genuinely huge real file, consistent with how the PDF page limit's
  boundary cases are tested without needing pathological inputs).
- `max_pdf_pages` is configurable (`Settings`/`.env`), not hardcoded —
  same pattern as `max_upload_size_bytes`, in case a deployment's real
  documents are legitimately larger than the 50-page default.
- Still not addressed, named rather than silently skipped: no wall-clock
  timeout on the OCR call itself (a single pathological page — e.g. an
  image Tesseract struggles badly with — could still take a long time
  even under the page cap), and no limit on concurrent in-flight `/process`
  requests overall (the threadpool has its own default size, but nothing
  in this codebase explicitly caps or monitors it).
