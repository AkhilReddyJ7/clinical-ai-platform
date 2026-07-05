# 0031: Extraction/validation versioning and lineage schema

- **Status:** Accepted
- **Date:** 2026-07-05
- **Follows up on:** `docs/architecture/idp-platform-pivot-baseline.md`
  (Phase B), [0020](0020-document-and-job-state-machines.md) (the Job
  model this ADR extends), [0025](0025-confidence-and-quality-semantics-model.md)
  (confidence semantics, unaffected by this ADR).

## Context

`ExtractionResult` and `ValidationResult` are pure insert-only logs:
every processing attempt writes a fresh row, and reprocessing a document
produces N rows sharing `document_id`, distinguished only by `job_id`
and `created_at`. There is no explicit version number, no recorded
reason a given attempt exists, and no way to identify which
field-extraction backend/model produced a given result. This ADR adds
that lineage without redesigning the append-only model itself, which
already works and needs no correction.

## Decision

### 1. Lineage lives on `Job`, not on the result tables

A `Job` already represents "one series of attempts" (ADR-0020) — a new
`Job` row is only ever created on resubmit, never on an internal
transient retry (which reuses the same row via `running`/`retrying`).
`Job` gains three columns:

- `attempt_number: int` — 1-indexed, `COUNT(existing Job rows for this
  document) + 1`, computed under the same `with_for_update()` document
  lock `enqueue_job`/`force_reprocess_job` already take. Race-free
  without a separate counter column anywhere.
- `trigger: JobTrigger` — `initial_submission` / `resubmit_after_failure`
  / `forced_reprocess`. Deliberately not named "retry": `JobStatus.RETRYING`
  already means something unrelated (an internal transient retry of the
  *same* job row), and reusing that word here would conflate the two.
- `trigger_note: str | None` — operator-supplied free text, always
  `None` for the first two trigger values. See ADR-0032 for how this
  carries the "why" (pipeline upgrade / manual reprocess / backfill).

### 2. `ExtractionResult` gains exactly one column: `pipeline_version`

`pipeline_version: str | None` identifies which field-extraction
backend/model produced a result (e.g. `"anthropic:claude-haiku-4-5"`,
`"mock"`). Not added to `ValidationResult`: validation is rule-based,
not model-based, and has no meaningful version axis today — adding a
column with no real values to report would be scope for its own sake.

Implemented as a **concrete property with a default**
(`FieldExtractionPipeline.pipeline_version`, returning
`type(self).__name__`), not an `@abstractmethod`. An abstract method
would make four existing test-double subclasses
(`_NoFieldsExtraction` ×2, `_NeverCallMeFieldExtractionPipeline`,
`_PartialFieldExtractionPipeline`, `_FailingFieldExtractionPipeline`)
unconstructible, since none of them implement anything beyond
`extract_fields`. `AnthropicFieldExtractionPipeline` overrides with
`f"anthropic:{self._model}"`; `MockFieldExtractionPipeline` with
`"mock"`.

### 3. No `supersedes_id`/`superseded_by_id` pointer column

Lineage is fully reconstructable via `ExtractionResult.job_id ->
Job.attempt_number`, ordered — an explicit pointer would be a second,
independently-writable source of the same fact, with no correctness
benefit over the join and a real risk of drifting from it.

### 4. `GET /documents/{id}/result` is unaffected — this is the critical constraint

**"Current result" stays defined exactly as it is today: the
`ExtractionResult`/`ValidationResult` row with the latest `created_at`
for the document.** It is deliberately *not* redefined as "the result
belonging to the Job with the highest `attempt_number`."

This matters concretely: worker-level failure paths (retry-budget
exhaustion, stale-job reclaim — `modules/processing/worker.py`) mark a
`Job` `failed` without ever calling `pipeline.py`'s `_persist_failure` —
so a document's latest `Job` can legitimately have zero result rows.
Redefining "current" by attempt number would make `/result` report
nothing for a document whose last attempt died at the worker/queue
level, a real regression from today's behavior. `Job.attempt_number` is
exposed only as the lineage ordinal on the new `GET
/documents/{id}/history` endpoint (ADR-0032) — never used to pick
"current" anywhere. This is precisely the "a new version existing is
not the same as a new job attempt happening" distinction the pivot
baseline itself warns about, confirmed here to be a real case, not a
hypothetical one.

## Consequences

- Migration `81a785918b94` adds `jobs.attempt_number` (`NOT NULL DEFAULT
  1`), `jobs.trigger` (`NOT NULL DEFAULT 'INITIAL_SUBMISSION'`),
  `jobs.trigger_note` (nullable), and
  `extraction_results.pipeline_version` (nullable). **Every pre-existing
  `Job` row backfills to `attempt_number=1, trigger=INITIAL_SUBMISSION`
  regardless of true history** — an accepted limitation of this
  dev/pre-production project, not a real backfill-integrity concern for
  any data that has actually existed here.
- `enqueue_job` now also computes and sets `attempt_number`/`trigger`
  for its two existing legal paths (`uploaded -> processing` =
  `initial_submission`, `failed -> processing` = `resubmit_after_failure`).
- No change to `DOCUMENT_TRANSITIONS`, `validate_document_transition`,
  or `GET /documents/{id}/result`'s query — see ADR-0032 for the new,
  separate reprocess path this ADR's schema supports.
