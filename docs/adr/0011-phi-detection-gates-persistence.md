# 0011: PHI detection gates persistence, not just document status

- **Status:** Accepted
- **Date:** 2026-07-03
- **Resolves:** the unresolved risk named in [0010](0010-real-local-ocr-via-tesseract-fields-stay-synthetic.md)

## Context

[0010](0010-real-local-ocr-via-tesseract-fields-stay-synthetic.md) shipped
real OCR and, with it, a real risk: PHI detection ran, but only *after* the
real extracted text was already committed to Postgres. Tracing the exact
code path confirmed why: `process_document` called
`ingestion_service.update_status(db, document, DocumentStatus.EXTRACTED)`
immediately after `db.add(extraction)` — and `update_status` calls
`db.commit()` internally, flushing the whole session, including the staged
extraction — a full two statements *before* `validation_pipeline.validate()`
even ran. A document that tripped PHI detection ended up `status: failed`,
but its real, PHI-shaped `raw_text` was already at rest in
`extraction_results`.

## Decision

Reordered `process_document` (`apps/api/routers/documents.py`) so
`validation_pipeline.validate(extraction_output)` runs entirely in-memory,
before anything derived from the real text is persisted. The result decides
what gets written:

- If any issue in the validation result starts with `"phi:"` (the prefix
  `PHIDetectionValidator` already used, see
  [0008](0008-lightweight-regex-phi-detection-not-presidio.md)), the
  persisted `ExtractionResult` gets a redaction placeholder
  (`"[REDACTED: PHI detected in N characters of extracted text; not
  persisted]"`, `fields={}`) instead of the real text.
- Otherwise, behavior is unchanged — real `raw_text`/`fields` persisted as
  before.

The document/extraction/validation status-transition sequence
(`processing → extracted → validated|failed`) and the two-commit structure
are otherwise untouched — this was a data-flow reorder plus a conditional,
not a bigger redesign. No new `DocumentStatus` value, no new endpoint, no
schema change.

The `"phi:"` string-prefix check is a deliberate, acknowledged shortcut:
`ValidationOutput` doesn't carry which validator produced which issue, so
this is the cheapest way to identify PHI findings specifically without
threading provenance through the interface. It's also why this gates on
PHI specifically rather than "any validation failure" — `RequiredFields
Validator` can currently only fail on empty input (already rejected at
upload), so gating on `is_valid` broadly would have worked by coincidence
today, but would incorrectly block storage for a future non-PHI validator
failure that has no business redacting anything. Flagged in a code comment
at the call site for whoever adds the next validator.

## Consequences

- Verified at the database level, not just the API response: uploaded a
  real image containing a fake-but-pattern-shaped SSN, confirmed via direct
  `psql` query against the live container that `extraction_results.raw_text`
  contains only the redaction placeholder — the SSN pattern is not present
  anywhere in Postgres. Also verified the clean path is unaffected: a
  PHI-free upload still persists real text and real confidence exactly as
  before.
- **Still partial, and this is worth being explicit about.** The original
  uploaded file bytes still land in the storage backend at upload time,
  before any scanning is possible — that's unavoidable without a bigger
  redesign (scanning before accepting the upload at all) and is out of
  scope here. This decision closes the *database* exposure (the
  searchable/queryable copy of extracted text), not 100% of the risk
  surface.
- The `"phi:"` prefix coupling between `modules/validation/phi.py` and
  `apps/api/routers/documents.py` is real, string-based, implicit coupling.
  Acceptable now (small codebase, one call site, commented at both ends),
  but a structured `Issue(category, message)` type would be the correct
  fix if a second call site or a second security-relevant validator
  category shows up.
