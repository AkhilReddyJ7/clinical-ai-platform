# 0019: Anthropic-based field extraction; PHI check now gates the LLM call

- **Status:** Accepted
- **Date:** 2026-07-04
- **Follows up on:** [0010](0010-real-local-ocr-via-tesseract-fields-stay-synthetic.md)
  (named this as the next gap), [0011](0011-phi-detection-gates-persistence.md)
  (extends the same "gate before the risky action" principle one step
  earlier), [0018](0018-evaluated-presidio-not-adopting-yet.md) (redirected
  Sprint 2 effort here as the larger remaining gap).

## Context

Real OCR ([0010](0010-real-local-ocr-via-tesseract-fields-stay-synthetic.md))
produces real `raw_text`, but structured `fields` were still the
deterministic synthetic placeholders from `modules.ocr.mock.synthesize_fields`
— a real gap, since `RequiredFieldsValidator` could never actually fail
(synthetic fields always populate all three required fields) and downstream
consumers of `fields` (search, indexing, analytics — all reserved-for-later
modules) would eventually need real data.

Two design questions were discussed with the project owner before writing
code:

1. **Architecture shape** — split OCR (`bytes -> raw_text`) from field
   extraction (`raw_text -> fields`) as two pipeline stages, rather than
   growing `ExtractionPipeline` to do both. Decided yes: they're genuinely
   different concerns (image/PDF decoding vs. LLM prompting) with different
   failure modes, and the seam was reserved since Sprint 1 scaffolding
   (`modules/extraction/` existed as an empty directory).
2. **Provider scope** — build a provider-agnostic tree
   (`FieldExtractionPipeline` -> `AnthropicFieldExtractor` /
   `OpenAIFieldExtractor` / `LocalFieldExtractor`, configured via `Settings`)
   from day one, or build only an Anthropic-backed implementation and
   generalize later if a second provider is ever actually needed. Decided
   the latter, for the same reason every other stage in this project has
   exactly one concrete implementation behind its ABC
   (`LocalFileStorage`, `TesseractExtractionPipeline`,
   `RequiredFieldsValidator` + `PHIDetectionValidator`): the ABC is already
   the extensibility point. A second implementation can be added later
   without touching callers, same as any other stage — building the tree
   now would be speculative generality with no second provider to validate
   it against.

A third question came up implementing the control flow: the existing
`process_document` validated *after* extraction completed (OCR fields +
real text together), then redacted if PHI was found. With a real external
LLM call now in the loop, running the LLM *before* checking PHI means
potentially sending PHI-shaped content to a third party specifically to
extract fields from it — a bigger trust-boundary crossing than the
persistence-only gate [0011](0011-phi-detection-gates-persistence.md)
already established.

## Decision

**New `modules/extraction/` package**, mirroring `modules/ocr/`'s shape:
- `base.py` — `FieldExtractionOutput` (`fields: dict[str, str]`,
  `confidence: float`), `FieldExtractionError`, and the
  `FieldExtractionPipeline` ABC (`extract_fields(raw_text: str) ->
  FieldExtractionOutput`). `FieldExtractionError` mirrors
  `ExtractionError` — a dedicated exception for "the call itself failed,"
  distinct from a valid-but-empty result.
- `mock.py` — `MockFieldExtractionPipeline`, deterministic hash-of-text
  synthetic fields, independent of `modules.ocr.mock`'s bytes-keyed
  version (different pipeline stage, different input shape — not worth
  cross-importing for three constants).
- `anthropic_extractor.py` — `AnthropicFieldExtractionPipeline`, the sole
  real implementation. Uses a **forced tool call**
  (`tool_choice={"type": "tool", "name": ...}`) against a single tool
  (`record_clinical_fields`, schema: `patient_name`/`date_of_birth`/`mrn`,
  none marked `required` — the model may omit fields genuinely absent from
  the text) rather than free-text parsing, so the response shape is
  reliable without a JSON-parsing step of its own.

**Settings**: `anthropic_api_key` (empty default), `anthropic_model`
(`claude-haiku-4-5` — cheap/fast, appropriate for a bounded structured-
extraction task, not a reasoning-heavy one), `anthropic_timeout_seconds`
(30.0), `anthropic_max_input_chars` (12,000 — bounds per-document LLM cost
the same way `max_pdf_pages` ([0016](0016-cap-per-document-ocr-resource-cost.md))
bounds per-document OCR cost).

**Fail-closed, but per-request, not per-construction.** The pipeline is
built once via `get_field_extraction_pipeline()`
(`apps/api/dependencies.py`), a FastAPI dependency. The first version of
this raised `ValueError` in `__init__` when `api_key` was empty — correct
in isolation, but wrong here: it would crash dependency resolution (an
unhandled 500) on *every* `/process` call in a deployment without a key
configured, including ones that would never have reached the LLM anyway
(e.g. PHI-flagged documents, which now skip the call entirely — see
below). Corrected before merging: construction never raises;
`extract_fields()` raises `FieldExtractionError` immediately if no key is
configured, before attempting any request — same graceful
`status: failed` path as any other extraction failure
([0012](0012-graceful-extraction-failure-handling.md)), not a crash.
Caught live against the real Docker image (see Consequences).

**Control-flow change in `process_document`** (`apps/api/routers/documents.py`):
PHI-check `raw_text` alone, immediately after OCR, via a bare
`PHIDetectionValidator` (new `get_phi_validator()` dependency — not the
full `CompositeValidationPipeline`, since `fields` don't exist yet at this
point and `RequiredFieldsValidator` would have nothing meaningful to
check). If PHI-shaped content is found: redact and fail, exactly as
before, but the field-extraction LLM is **never called** — no cost, no
external send of PHI-shaped content. If clean: call
`field_extraction_pipeline.extract_fields()` (via `run_in_threadpool`,
same reasoning as [0013](0013-run-extraction-off-the-event-loop.md) — a
network call is exactly the kind of blocking-the-event-loop operation that
decision covers), then run the **full** `CompositeValidationPipeline`
against the combined real `raw_text` + real `fields` (the PHI re-check
here is redundant but harmless; `RequiredFieldsValidator` is now
meaningful for the first time, since the LLM can genuinely fail to find a
field). A `FieldExtractionError` (bad/missing key, rate limit, timeout,
malformed response) fails the document cleanly, same shape as an OCR
`ExtractionError` — but persists the real (already-PHI-checked) `raw_text`
rather than a placeholder, since only the field-extraction step failed,
not text extraction itself.

**Confidence**: `ExtractionResult.confidence` is a single column; the
final value persisted is the average of the OCR stage's confidence and
the field-extraction stage's confidence (`len(fields found) /
len(fields requested)` when at least one field is found, else `0.0`) — no
schema change for two separate confidence numbers, and both really are
0.0–1.0 measures of how much to trust the result.

**Error handling**: `anthropic.RateLimitError`, `anthropic.APIStatusError`
(covers auth/permission/bad-request/5xx), and `anthropic.APIConnectionError`
(covers timeouts) are each caught and re-raised as `FieldExtractionError`
with the original exception chained (`raise ... from exc`) — a
most-specific-first chain, since `RateLimitError` is a subclass of
`APIStatusError`.

**CI**: no `ANTHROPIC_API_KEY` provisioned — deliberately, to avoid a live
LLM call (and cost) on every push. The Docker smoke test in
`.github/workflows/ci.yml` was updated to expect `status: failed` with an
`"Anthropic API key is not configured"` issue, rather than `validated` —
this still proves the full container/dependency wiring works end-to-end
(the fail-closed-per-request design above means this is a clean assertion,
not a crash to work around).

## Consequences

- Full local verification cycle (`ruff`, `black`, `mypy --strict`,
  `pytest`) passes; 18 new tests added (mock field extraction, Anthropic
  extractor unit tests with the SDK call mocked directly — success,
  partial fields, disallowed/blank field filtering, non-`tool_use`
  stop reasons, missing tool-use block, each of the three exception types,
  input truncation — plus integration tests proving the PHI-gates-LLM-call
  behavior and the field-extraction-failure path).
- **The PHI-gate test was verified to actually discriminate**, following
  the project's established practice
  ([0017](0017-reject-unauthenticated-requests-before-body-read.md)):
  deliberately broke the gate (`if not phi_precheck.is_valid` ->
  `if False`) and confirmed the new test failed with exactly the expected
  assertion error, then reverted.
- **Verified against the real Anthropic API, not just mocks** — a
  meaningfully different kind of check from typical local dev, since this
  is the first real external network dependency in the project. Ran the
  live Docker Compose stack three ways: (1) no key configured — confirmed
  `status: failed` with `"Anthropic API key is not configured"`, matching
  the new CI assertion exactly; (2) a syntactically-valid but fake key,
  via a real `.env` file picked up through the bind-mounted `/app`
  directory (no `docker-compose.yml` change needed — `Settings`' own
  `env_file` loading reads it directly, the same mechanism already used
  for every other setting) — confirmed a **real** round trip to
  `api.anthropic.com` (a genuine `request_id` came back in the 401 body),
  and the `APIStatusError` branch correctly turned it into a clean
  `FieldExtractionError` / `status: failed`, at zero inference cost since
  auth fails before any model call; (3) confirmed the container builds
  cleanly with the new `anthropic` SDK dependency added to
  `pyproject.toml`/`uv.lock`.
- **Genuine end-to-end verification against a real, valid API key is still
  pending** — the project owner has not supplied one. Everything short of
  actual inference (dependency wiring, auth-failure handling, the
  fail-closed-with-no-key path, the full container build) has been
  verified live; the only remaining gap is confirming a real model
  response parses as expected, which needs a real key placed in a
  gitignored `.env` (never committed).
- `modules/ocr/`'s own `synthesize_fields` (bytes-keyed) is now fully
  vestigial for the real pipeline — `TesseractExtractionPipeline` still
  computes it (unchanged, per the split-not-touch-OCR design above), but
  `process_document` never reads `extraction_output.fields` anymore,
  always overwriting it with the field-extraction stage's real output.
  Left as-is rather than stripped: `modules/ocr/` was explicitly scoped as
  "unchanged" in the proposal this ADR implements, and
  `MockExtractionPipeline` still needs *some* fields output for its own
  test coverage.
- README and `.env.example` updated throughout to reflect that `fields`
  are real, not synthetic — this was one of the most-repeated caveats in
  the project's documentation, so every instance was found and updated
  rather than leaving stale claims alongside the new ones.
