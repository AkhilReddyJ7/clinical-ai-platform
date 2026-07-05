# 0032: Reprocessing and backfill triggering

- **Status:** Accepted
- **Date:** 2026-07-05
- **Follows up on:** `docs/architecture/idp-platform-pivot-baseline.md`
  (Phase B), [0020](0020-document-and-job-state-machines.md) (which
  disallowed `validated -> processing` by default and named this the
  seam for "its own explicit, audited action" later),
  [0031](0031-extraction-validation-versioning-and-lineage-schema.md)
  (the `Job` lineage columns this ADR's new code paths populate).

## Context

ADR-0020 disallowed `validated -> processing` by default: "A validated
document is a trusted, completed result. Silently allowing arbitrary
re-processing risks overwriting a good result with a worse one... if a
genuine need for 'force re-process a validated document' emerges, it
should be introduced later as its own explicit, audited action." That
need has now emerged: the pivot baseline requires a backfill/reprocess
entry point that can re-run the pipeline against already-ingested
documents, e.g. after a pipeline/model upgrade.

## Decision

### 1. One new, narrow repository function — not a change to the frozen state graph

`force_reprocess_job(db, document_id, *, trigger_note=None)` in
`modules/processing/repository.py` requires `document.status ==
VALIDATED` and performs `VALIDATED -> PROCESSING` directly, without
touching `DOCUMENT_TRANSITIONS` or `validate_document_transition`'s
general graph. This function *is* the "own explicit, audited action"
ADR-0020 anticipated — deliberately scoped to exactly this one bypass so
every other caller of the general validator still correctly rejects
`validated -> processing` by default. Raises `IllegalTransitionError`
for any non-`validated` status (covering both "never validated" and
"already has an active job," since a document is only ever `validated`
with zero active jobs, by construction). Takes the same
`with_for_update()` document-row lock `enqueue_job` uses, for the same
reason: two concurrent reprocess calls for the same document must not
both succeed.

### 2. Two new routes, `apps/api/routers/documents.py`

- **`POST /documents/{id}/reprocess`** — body `{trigger_note}`
  (`ReprocessIn`), `202` with the same `ProcessEnqueuedOut` shape
  `/process` returns (now including `attempt_number`/`trigger`/
  `trigger_note` — see ADR-0031), `409` if the document isn't currently
  `validated`, `404` if it doesn't exist. Audited via a new
  `AuditAction.FORCED_REPROCESS` through the existing `record_action`
  (no migration needed: `AuditLogEntry.action` is a plain `VARCHAR`
  with no DB-level `CHECK` constraint). Distinct from `/process`, which
  remains the entry point for the `uploaded`/`failed` cases.
- **`GET /documents/{id}/history`** — every `Job` attempt for a
  document, ordered by `attempt_number`, with `pipeline_version`/
  `confidence`/`is_valid` from its result rows where they exist (`None`
  where a job never produced one — see ADR-0031 section 4). Not
  paginated: a document's attempt count is naturally small, unlike the
  global `/audit`/`/metrics` collections.

### 3. Bulk backfill is a script, not a bulk API

`scripts/run_backfill.py` targets every currently-`validated` document
(the only legal `force_reprocess_job` precondition) or a single
`--document-id`, with `--before`/`--limit` filters, a required `--note`
(forcing a real justification, consistent with ADR-0020's "operational
override" framing), `--dry-run`, and a confirmation prompt unless
`--yes`. It calls `force_reprocess_job` directly (not over HTTP) per
candidate document, treating a per-document `IllegalTransitionError` as
"skipped" rather than aborting the whole run (a document can legitimately
race out from under the filter between the candidate query and the
call). **It only enqueues — it does not run the pipeline itself.** The
existing worker service (`docker-compose.yml`'s `worker`) must already
be running to actually pick up and execute the resulting `queued` jobs;
this is stated explicitly in the script's own docstring since it's an
easy assumption to get wrong.

### 4. The three baseline-named reasons collapse into one trigger value plus free text

The pivot baseline names three reasons a reprocess might happen
(pipeline upgrade, manual reprocess, backfill). These overlap heavily
in practice (a backfill is usually "manual reprocess for a pipeline
upgrade," run in bulk) and aren't cleanly disjoint. Rather than
encoding all three as separate enum values — which would push
ambiguity into "which one do I pick" without buying real query value at
this project's scale — `JobTrigger` stays at exactly the mechanism
(`forced_reprocess`), and the free-text `trigger_note` carries the
"why." `scripts/run_backfill.py` adopts the convention of prefixing
notes for grouping (e.g. `"backfill: upgrade to claude-haiku-4-6,
batch=2026-07-05"`), so a `LIKE 'backfill:%'` query can still group a
run if that's ever needed. Accepted as a proportionate limitation for
now; a dedicated `batch_id` column is the named extension path if
aggregate backfill-run reporting becomes a real need later — not built
today.

## Consequences

- `apps/api/schemas.py` gains `ReprocessIn`, `DocumentHistoryEntryOut`,
  `DocumentHistoryOut`; `ProcessEnqueuedOut` is extended and shared by
  both `/process` and `/reprocess`.
- `modules/processing/schemas.py` is a new file (`JobOut`) — the one
  module that lacked its own schemas module, per every other module's
  convention.
- `modules/audit/models.py` gains `AuditAction.FORCED_REPROCESS`.
- New `Makefile` target `backfill` (`uv run python -m
  scripts.run_backfill $(ARGS)`).
- No change to `DOCUMENT_TRANSITIONS`, `validate_document_transition`,
  or any other caller of the general state-transition validator.
