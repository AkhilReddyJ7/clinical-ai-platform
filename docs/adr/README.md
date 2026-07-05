# Architecture Decision Records

Decisions made during Sprint 1 (MVP slice), Sprint 1.5 (production-readiness
hardening), Sprint 2 (auth, PHI detection, real OCR, LLM-based field
extraction), and Sprint 3 (production processing pipeline: state machine,
async worker, retry handling, identity, audit — see
`../architecture/sprint-3-design-baseline.md` for the approved design
baseline). Each record follows: Status, Context, Decision, Consequences.

| # | Title | Status |
|---|---|---|
| [0001](0001-modular-monolith-over-microservices.md) | Modular monolith over microservices | Accepted |
| [0002](0002-interface-first-pipeline-stages.md) | Interface-first pipeline stages | Accepted |
| [0003](0003-alembic-migrations-over-implicit-create-all.md) | Alembic migrations over implicit `create_all` | Accepted |
| [0004](0004-sqlite-for-tests-postgres-for-runtime.md) | SQLite for tests, Postgres for runtime | Accepted |
| [0005](0005-paginated-response-envelope-breaking-change-accepted-early.md) | Paginated response envelope, breaking change accepted early | Accepted |
| [0006](0006-non-root-container-fixed-uid-venv-outside-bind-mount.md) | Non-root container, fixed UID 1000, venv outside the bind mount | Accepted (corrected by 0009) |
| [0007](0007-ci-validates-docker-build-and-boot.md) | CI validates the Docker build and boot, not just the Python package | Accepted |
| [0008](0008-lightweight-regex-phi-detection-not-presidio.md) | Lightweight regex-based PHI detection, not Presidio | Accepted (revisited in 0015, resolved by 0018) |
| [0009](0009-preseed-upload-directory-ownership-in-image.md) | Pre-seed the upload directory in the image so named-volume ownership is correct | Accepted |
| [0010](0010-real-local-ocr-via-tesseract-fields-stay-synthetic.md) | Real local OCR via Tesseract; fields stay synthetic pending real extraction | Accepted (resolved by 0011 and 0019) |
| [0011](0011-phi-detection-gates-persistence.md) | PHI detection gates persistence, not just document status | Accepted |
| [0012](0012-graceful-extraction-failure-handling.md) | Graceful extraction failure handling | Accepted |
| [0013](0013-run-extraction-off-the-event-loop.md) | Run extraction (and storage reads) off the event loop | Accepted |
| [0014](0014-stream-upload-size-enforcement.md) | Stream-enforce upload size limits instead of buffer-then-check | Accepted (corrected by 0017) |
| [0015](0015-phi-detection-re-evaluation-and-pattern-expansion.md) | PHI detection re-evaluation and pattern expansion | Accepted (resolved by 0018) |
| [0016](0016-cap-per-document-ocr-resource-cost.md) | Cap per-document OCR resource cost | Accepted |
| [0017](0017-reject-unauthenticated-requests-before-body-read.md) | Reject unauthenticated requests before the body is ever read | Accepted |
| [0018](0018-evaluated-presidio-not-adopting-yet.md) | Evaluated Microsoft Presidio for PHI detection; not adopting yet | Accepted |
| [0019](0019-anthropic-field-extraction-and-phi-gates-llm-call.md) | Anthropic-based field extraction; PHI check now gates the LLM call | Accepted |
| [0020](0020-document-and-job-state-machines.md) | Document and job state machines: legal transitions | Accepted |
| [0021](0021-postgres-backed-job-queue.md) | Postgres-backed job queue, not Redis/Celery | Accepted |
| [0022](0022-process-api-contract-enqueue-and-return.md) | `/process` becomes enqueue-and-return; `/result` becomes the canonical status/result endpoint | Accepted |
| [0023](0023-retry-and-backoff-policy-for-processing-jobs.md) | Retry and backoff policy for processing jobs | Accepted |
| [0024](0024-stale-job-recovery-worker-crash.md) | Stale RUNNING job recovery: reclaiming jobs orphaned by a worker crash | Accepted |
| [0025](0025-confidence-and-quality-semantics-model.md) | Confidence and quality semantics model for document processing | Accepted |
| [0026](0026-named-api-keys-replace-the-shared-static-secret.md) | Named API keys replace the shared static secret | Accepted |
| [0027](0027-audit-log-schema-and-redaction-policy.md) | Audit log schema and redaction policy | Accepted |
| [0028](0028-audit-trail-read-api.md) | Audit trail read API | Accepted |
| [0029](0029-operational-metrics-api.md) | Operational metrics API | Accepted |
| [0030](0030-evaluation-harness.md) | Evaluation harness | Accepted |
| [0031](0031-extraction-validation-versioning-and-lineage-schema.md) | Extraction/validation versioning and lineage schema | Accepted |
| [0032](0032-reprocessing-and-backfill-triggering.md) | Reprocessing and backfill triggering | Accepted |
| [0033](0033-vector-store-selection-for-retrieval.md) | Vector store selection for retrieval | Accepted |
| [0034](0034-chunking-and-embeddings-strategy-for-clinical-documents.md) | Chunking and embeddings strategy for clinical documents | Accepted |
| [0035](0035-retrieval-api-shape-and-phi-safety-at-the-retrieval-boundary.md) | Retrieval API shape and PHI safety at the retrieval boundary | Accepted |
