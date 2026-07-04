# Sprint 4 Design Baseline: Making Accountability Usable

- **Status:** Approved — frozen as the Sprint 4 design baseline
- **Date approved:** 2026-07-04
- **Scope:** Architecture planning only. Individual architectural decisions
  named in this baseline are formalized as their own ADRs (`docs/adr/`)
  before implementation, per the ADR-first process used throughout
  Sprints 1-3. This document is not itself an implementation plan and
  should not be treated as one — it defines the shape and constraints
  that the ADRs and code that follow must satisfy.

This document is frozen as written at approval time. Corrections or
revisions, if any become necessary, are appended as dated notes (matching
this project's ADR convention) rather than silently edited.

---

## 1. Sprint 4 Objective and Success Criteria

**Objective: turn Sprint 3's two write-only epics into something a real
operator can actually use.**

Sprint 3 built the audit trail (ADR-0027) and named identity (ADR-0026)
correctly, but stopped exactly at "durably recorded" — there is no way
today to read an audit entry back, and no way to answer "how many jobs
are queued right now" or "what's our failure rate" without a raw SQL
query against the running database. The Sprint 3 baseline's own section
9 stated the goal directly: metrics should be "derivable from the data
model that already exists... not a second pass to add observability
later." That data model exists. Nothing exposes it yet.

Sprint 4 is not a new feature surface — it's closing the loop on two
epics that are otherwise permanently half-finished, plus discharging the
one item that's been sitting deferred since Sprint 2.

**Success criteria:**
- Sprint 2's live-Anthropic-credentials verification is either
  completed or explicitly re-confirmed as still blocked, not silently
  carried forward a third sprint.
- A caller can retrieve audit history — globally, or scoped to one
  document — through the API, not just via a database client.
- A caller can retrieve operational metrics (queue depth, retry/failure
  counts, confidence distribution) through the API, computed from data
  that is already durably stored — no new instrumentation, no new
  storage, per this sprint's scope.
- No new infrastructure (no Prometheus, no OpenTelemetry, no
  message broker, no vector store) — everything in this sprint is a read
  surface over `Document`/`Job`/`ExtractionResult`/`ValidationResult`/
  `AuditLogEntry`, all of which already exist.

## 2. A Correction Found During This Sprint's Own Audit

**`modules/processing/metrics.py`'s `WorkerMetrics` cannot back an API
endpoint, and this baseline does not ask it to.** Confirmed directly by
reading the module's own docstring: it is an explicit, deliberate
**process-local, non-aggregating** singleton — "each worker instance
(process, container, or asyncio task) has its own independent
WorkerMetrics... aggregating counts across multiple worker instances
would need an external system — explicitly out of scope."

This matters concretely because of a shape change Sprint 3 itself made:
`apps/worker/main.py` runs processing in a **separate container** from
`apps/api/main.py` (ADR-0022). If the API process exposed a `/metrics`
route reading `modules.processing.metrics.metrics` directly, it would
read its *own* process's copy — which never claims a job, never runs a
stage, and would report zeros forever. This is not a hypothetical risk;
it is what would happen by construction if this sprint reused that
singleton naively.

**Resolution: Sprint 4's metrics epic is a database read, not a
metrics-singleton read.** Everything this baseline commits to exposing
(queue depth, retry/failure counts, confidence distribution) is already
durably persisted on `Job`/`Document`/`ExtractionResult` and reachable by
a plain query against any process holding a DB session — API or worker,
doesn't matter, because the data lives in Postgres, not in either
process's memory.

**One consequence of that: per-stage duration (OCR duration, extraction
duration, "how long did stage X take") is explicitly out of scope this
sprint.** It has only ever existed as an in-memory sample
(`WorkerMetrics._stage_durations`) or as transient event metadata
(`PIPELINE_STAGE_COMPLETED`'s `duration_ms`) — never persisted anywhere
a later reader could query. Exposing it durably would mean adding new
storage (a column or table capturing per-attempt stage timings), which is
new scope this baseline does not commit to without a concrete need driving
it. Named here explicitly so a future contributor doesn't assume it's
already available and doesn't quietly bolt it onto this sprint's ADRs.

## 3. Scope

### 3.1 Close Sprint 2's deferred item (no code)

ADR-0019 recorded the live-Anthropic-credentials verification as
explicitly attempted and explicitly deferred — not forgotten, blocked on
real API credits. This requires no design decision and no ADR: once
credits and a real key exist, run the existing pipeline against a real
document and confirm `AnthropicFieldExtractionPipeline` behaves as
designed against the actual API (not `MockFieldExtractionPipeline`).
Sequenced first because it's the cheapest possible risk reduction on the
platform's actual core value proposition, and because it's blocked on the
project owner, not on design work — better to know early if it surfaces
anything.

### 3.2 Audit trail read API

The one piece ADR-0027 explicitly deferred ("No query/read API this ADR
... additive to this schema, not a redesign of it"). Needs its own ADR
to resolve, concretely:
- Route shape: a global `GET /audit` versus a document-scoped
  `GET /documents/{id}/audit`, or both.
- Filtering: by `caller`, by `action`, by a `document_id`/`job_id` — at
  minimum whatever the schema's own columns support directly (no new
  indexes should be needed; ADR-0027's `AuditLogEntry` already indexes
  `caller`, `document_id`, `job_id`).
- Pagination: reuse the existing `DocumentListOut`-style envelope
  (`items`/`total`/`limit`/`offset`, ADR-0005) rather than inventing a
  second pagination convention.
- Whether this route requires the same `require_api_key` gate as
  `/documents*` (yes, by default — no new auth model is in scope) and
  whether every named caller can see every other caller's audit history,
  or only their own. **This access-control question is the one real
  decision this epic needs and does not yet have an answer to** — flagged
  for the ADR to resolve, not assumed here.

### 3.3 Operational metrics / analytics API

A new read endpoint (or small set of them) surfacing, from durable
storage only (section 2):
- **Queue depth**: count of jobs currently `queued`/`running`/`retrying`.
- **Retry and failure counts**: count of jobs by terminal status, and
  `retry_count` distribution.
- **Document throughput**: count of documents by status, optionally
  windowed by `created_at`.
- **Confidence distribution**: aggregate stats (min/avg/max, or a simple
  bucketed histogram) over `ExtractionResult.confidence`.

Needs its own ADR to resolve: exact route shape(s), whether this is one
endpoint or several, whether aggregation happens in SQL (`COUNT`/`AVG`
directly) or in Python after a plain fetch (SQL aggregation is the
obvious default given this project's existing query patterns, but the
ADR should say so explicitly rather than leave it to whoever implements
it), and the same caller-visibility question section 3.2 raises for
audit — is this data caller-scoped or global.

### 3.4 Explicitly out of scope this sprint

Unchanged from the Sprint 3 baseline's own out-of-scope list, still true
today, no new justification has appeared for any of them:
- RAG, search, vector databases, embeddings
- Layout analysis
- OAuth / SSO / RBAC
- Kafka / RabbitMQ or any broker
- Additional LLM providers
- Per-key management endpoints (issuance/rotation/revocation) — named
  keys (ADR-0026) remain env-configured; this sprint only makes existing
  data *readable*, it does not expand the identity model.
- Persisted per-stage duration/timing analytics (section 2) — revisit if
  a concrete need for it appears.

## 4. Dependencies on Sprint 3

- The audit read API (3.2) is additive to `AuditLogEntry` (ADR-0027) —
  no schema change expected, only new routes.
- The metrics API (3.3) is additive to `Job`/`Document`/
  `ExtractionResult` — no schema change expected either, confirmed by
  section 2's audit of what's already persisted.
- Both new read surfaces sit behind the same `require_api_key` gate
  ADR-0026 already established; neither epic touches
  `modules/auth/` itself.

## 5. ADRs Likely Required

- *"Audit trail read API: route shape, filtering, and access scope"* —
  resolves section 3.2's open access-control question.
- *"Operational metrics API: what's exposed and how it's computed"* —
  resolves section 3.3's aggregation and scoping questions.

Two ADRs, not five — this is a deliberately small sprint compared to
Sprint 3's eight, because it is closing existing epics rather than
opening new architectural surface.

## 6. Recommended Implementation Order

1. **Live-Anthropic verification** (3.1) — no design dependency on
   anything else in this sprint; do it first and independently since
   it's blocked on credentials, not on code.
2. **Audit trail read API** (3.2) — smaller schema surface (one table),
   resolves a question (access scope) the metrics epic will ask again.
3. **Operational metrics API** (3.3) — built after 3.2 so its own ADR can
   simply point at 3.2's access-scope answer instead of re-deriving it.

## Decisions Signed Off At Approval

- Sprint 4 is scoped to finishing Sprint 3's audit and metrics epics, not
  to opening any item from the Sprint 3 baseline's out-of-scope list.
- Metrics are computed from durable storage (Postgres), never from
  `modules.processing.metrics.WorkerMetrics` — that singleton stays
  exactly what it already is, a process-local live counter, not an
  analytics source.
- Persisted per-stage duration data is explicitly deferred, not part of
  this sprint's metrics epic.
- The caller-visibility question (can a caller see other callers' audit
  entries / metrics) is explicitly open, deferred to each epic's own ADR
  — not decided here.
