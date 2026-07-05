# 0035: Retrieval API shape and PHI safety at the retrieval boundary

- **Status:** Accepted
- **Date:** 2026-07-05
- **Follows up on:** `docs/architecture/idp-platform-pivot-baseline.md`
  (Phase C), [0033](0033-vector-store-selection-for-retrieval.md),
  [0034](0034-chunking-and-embeddings-strategy-for-clinical-documents.md).

## Context

With chunking, embeddings, and a vector store decided, this ADR covers
where indexing hooks into the existing pipeline, what the read API looks
like, and the one genuinely new question this phase raises: does
retrieval open a new PHI-exposure surface.

## Decision

### 1. Indexing hook: only when a document reaches `VALIDATED`

`run_processing_pipeline` (`modules/processing/pipeline.py`) gains a
required `retrieval_service: RetrievalService` parameter. The indexing
call happens immediately after the document's `final_status` transition,
gated on `final_status == DocumentStatus.VALIDATED`. **This is the PHI
safety boundary**: a document only reaches `validated` after passing
both PHI gates (the pre-LLM-call precheck, ADR-0011/0019, and the final
`CompositeValidationPipeline` including `PHIDetectionValidator` again) —
so nothing is ever indexed that hasn't already cleared the same bar
`raw_text` persistence itself clears. A `failed` document (PHI-flagged
*or* missing a required field) is never indexed, verified directly by
test.

### 2. Indexing is non-fatal — mirrors `record_action`'s precedent

Wrapped in `run_in_threadpool` (ADR-0013) and a broad `try/except` that
logs and swallows any exception. A Chroma outage, a `fastembed` error, or
anything else unexpected here must never fail the surrounding document/
job — the same principle `modules/audit/service.py`'s `record_action`
already established for the audit trail ("the action being observed must
not fail because a secondary concern did"). Verified directly by test: a
`VectorStore` whose every method raises still results in the document
reaching `validated`.

### 3. `POST /retrieval/query` — one route, matching this project's own precedent

New router `apps/api/routers/retrieval.py`. Body: `query: str`, optional
`top_k` (defaults to `settings.retrieval_default_top_k`, rejected with
`422` above `settings.retrieval_max_top_k`). Router-level
`Depends(require_api_key)`, matching `audit.py`/`metrics.py` — not
`ApiKeyGateMiddleware`, whose `protected_prefix="/documents"` doesn't
cover this route and has no reason to: that middleware exists
specifically to reject an unauthenticated request *before its body is
read* (ADR-0017), which matters for a multipart upload, not a small JSON
query body.

### 4. No PHI check on the query text itself

PHI safety in this project has always governed what gets *ingested/
indexed* (ADR-0011/0019), not what an already-authenticated caller
types. The response can only ever surface `chunk_text` sourced from
documents that already passed both PHI gates before indexing (decision
1) — nothing new can leak through a retrieval response that isn't
already exposed, at the same access level, by the existing
`GET /documents/{id}/result` under ADR-0026's flat, per-key-equal access
model. Retrieval is a different read path over already-readable data,
not a new exposure class.

### 5. Reindex is a script, not a bulk API — and bypasses the job queue entirely

`scripts/run_reindex.py` indexes every currently-`validated` document's
latest `ExtractionResult` (same "current result" definition
`GET /documents/{id}/result` uses, ADR-0031 section 4) directly and
synchronously. Unlike `scripts/run_backfill.py` (which only enqueues,
relying on the worker to execute the actual pipeline), indexing has no
LLM call and isn't part of the `Job`/queue state machine at all — running
it synchronously is the right shape, not a shortcut. Delete-then-upsert
(ADR-0034) makes every run idempotent, so no "already indexed" check is
needed regardless of how many times it's re-run.

### 6. Reranking is deferred, not built

The pivot baseline names reranking a stretch goal for this phase, not a
requirement. Nothing in this ADR or its implementation adds a
cross-encoder or second-pass scoring step beyond Chroma's own cosine
similarity ranking — stated explicitly so a future contributor doesn't
assume it's missing by oversight.

## Consequences

- New `modules/retrieval/schemas.py`
  (`RetrievalQueryIn`/`RetrievedChunkOut`/`RetrievalQueryOut`), new
  router mounted in `apps/api/main.py` alongside the others.
- `modules/processing/pipeline.py`, `apps/api/dependencies.py`, and
  `modules/processing/worker.py` (the API's and worker's independent
  composition roots, ADR-0001) all thread the new `RetrievalService`
  through.
- New Makefile target `reindex`.
- Settings gain `retrieval_default_top_k`/`retrieval_max_top_k`.
