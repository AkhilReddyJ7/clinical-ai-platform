# 0010: Real local OCR via Tesseract; fields stay synthetic pending real extraction

- **Status:** Accepted
- **Date:** 2026-07-03

## Context

Sprint 2 needed its next epic. Two options were on the table: real OCR
(bytes → text) or LLM-based field extraction (text → structured fields).
Both were flagged as requiring an external vendor decision. On review, that
turned out to only be strictly true for LLM extraction — OCR has a genuinely
vendor-free option (local Tesseract) that cloud OCR (Textract, Document AI,
Azure Document Intelligence) does not.

A second finding shaped the scope: OCR alone doesn't complete the pipeline.
Structured fields (`patient_name`, `mrn`, `date_of_birth`) still require
something to interpret free text — an LLM, an NER model, or brittle regex.
Shipping "real OCR" with empty `fields` would make `RequiredFieldsValidator`
fail on every real upload, regressing the demo's happy path from
`status: validated` to `status: failed` for anything that isn't the mock.

## Decision

`TesseractExtractionPipeline` (`modules/ocr/tesseract.py`) replaces
`MockExtractionPipeline` as the pipeline wired into
`apps/api/dependencies.py::get_extraction_pipeline()`:

- `raw_text` becomes **real**: direct UTF-8 decode for `text/plain`
  (confidence `1.0`, no OCR involved), local Tesseract OCR for
  `image/png`/`image/jpeg`, and Tesseract-over-rasterized-pages for
  `application/pdf` (via `pypdfium2`, which ships a prebuilt PDFium binary
  in its wheel — no `poppler-utils` system package needed, only
  `tesseract-ocr` itself).
- `fields` stay **synthetic** — literally the same
  `modules.ocr.mock.synthesize_fields()` function `MockExtractionPipeline`
  already used, imported directly rather than duplicated, so the synthetic
  nature is visible at the call site.
- `MockExtractionPipeline` is unchanged and stays wired into the test
  suite (`tests/conftest.py`) — fast, deterministic, no system dependency
  on the CI `test` job or local dev without Docker.
- Real Tesseract-specific code paths (image/PDF OCR, confidence averaging)
  are unit-tested by mocking `pytesseract.image_to_data` — no tesseract
  binary needed to run those tests. One integration test exercises the
  real pipeline end-to-end for `text/plain` specifically (no OCR binary
  needed there either, pure passthrough), proving PHI detection now
  catches a real pattern in real content — closing the gap
  [0008](0008-lightweight-regex-phi-detection-not-presidio.md) flagged as
  previously untestable. Image/PDF OCR itself is verified via a CI step
  that runs a real image through the real pipeline inside the built
  container (see [0009](0009-preseed-upload-directory-ownership-in-image.md)
  for why a Python-only test job isn't enough), asserting OCR *functions*
  (non-empty text, confidence above a floor) rather than asserting exact
  text — tiny bitmap-font renders proved genuinely noisy in practice
  during manual verification, so exact-match assertions would have been a
  source of flaky CI failures unrelated to real regressions.

## Consequences

- **This changes the risk profile of the "no real PHI" project constraint.**
  Before this change, `raw_text` was always synthetic regardless of upload
  content — real PHI could not reach the system even if someone
  accidentally uploaded a real document, because the mock ignored it
  entirely. Now `raw_text` reflects whatever was actually uploaded. The "no
  real PHI" constraint goes from *structurally impossible to violate* to
  *a policy relying on uploader discipline plus a detection guardrail*
  ([0008](0008-lightweight-regex-phi-detection-not-presidio.md)) that, at
  the time this ADR was written, ran **after** the extraction result was
  already written to Postgres, not before. A document that tripped PHI
  detection was marked `status: failed`, but its real-content-derived
  `raw_text` was already at rest in `extraction_results` by that point.
  Verified this precisely: uploaded a real image containing a
  fake-but-pattern-shaped SSN and confirmed it reached the database and
  got flagged, not blocked pre-storage.

  **Resolved same-day in [0011](0011-phi-detection-gates-persistence.md):**
  validation now runs before persistence, and a PHI finding gets a
  redacted placeholder written instead of the real text. Left the original
  wording above as the record of what was true when this ADR was written.
- Verified end-to-end against the live compose stack with genuinely
  rendered images and a real PDF (not just blank pages): Tesseract
  correctly reads real text from both, with real (not hardcoded) per-image
  confidence scores, and PHI detection correctly fires against real OCR'd
  content from an image, not just from `text/plain`.
- Local (non-Docker) development now requires `tesseract-ocr` installed on
  the host to exercise image/PDF uploads for real — `text/plain` still
  works via pure passthrough either way. Docker Compose remains the fully
  supported path (`tesseract-ocr` + `tesseract-ocr-eng` installed in the
  image); this wasn't judged worth a fallback/graceful-degradation
  mechanism.
- OCR quality is a known, accepted limitation, not a claim of production
  readiness: Tesseract is free and vendor-free but genuinely weaker than
  cloud OCR or vision-capable LLMs on handwriting, poor scans, and complex
  forms — plausibly common in real clinical documents. Revisiting toward a
  cloud OCR vendor or a vision-LLM approach is real future work once
  quality against real-world documents actually matters, not before.
- Sets up LLM-based field extraction (deferred, still needs a vendor
  decision) to be more valuable when it lands: it will run against real
  text from every supported content type, not just a narrow `text/plain`
  slice, which was the shape of the alternative epic this one was chosen
  over.
