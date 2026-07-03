# 0002: Interface-first pipeline stages

- **Status:** Accepted
- **Date:** 2026-07-03

## Context

Sprint 1's constraints explicitly required the architecture to stay
"extendable for future OCR, RAG, PHI detection, and LLM extraction" without
restructuring. At the same time, Sprint 1 could only ship a mock
OCR/extraction pipeline and a baseline validator — the real implementations
don't exist yet.

## Decision

Every pipeline stage that will plausibly get a real implementation later is
defined as an abstract base class with exactly one concrete implementation
today:

- `modules.ingestion.storage.StorageBackend` → `LocalFileStorage`
- `modules.ocr.base.ExtractionPipeline` → `MockExtractionPipeline`
- `modules.validation.base.ValidationPipeline` → `RequiredFieldsValidator`

`apps/api/dependencies.py` is the single place that wires interface to
implementation, injected into routes via FastAPI `Depends()`. Route handlers
in `apps/api/routers/documents.py` depend only on the interface type, never
the concrete class.

## Consequences

- Swapping mock OCR for a real OCR/LLM backend, or local disk for S3/GCS, is
  a new class implementing the existing interface plus a one-line change in
  `dependencies.py` — not a router rewrite.
- `ExtractionOutput` (raw_text, fields, confidence) was deliberately shaped
  to be what a real OCR call *or* an LLM extraction call would both
  naturally produce, so Sprint 2 doesn't need to redesign the data contract.
- Cost: an extra layer of indirection (ABC + single implementation) for
  something that today has no second implementation to justify it in
  isolation — accepted because the interface is what the "no rewrite"
  requirement is actually buying.
