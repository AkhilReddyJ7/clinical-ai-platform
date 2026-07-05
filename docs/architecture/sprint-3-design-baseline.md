# Sprint 3 Design Baseline: Production Document Processing Pipeline

- **Status:** Approved — frozen as the Sprint 3 design baseline
- **Date approved:** 2026-07-04
- **Scope:** Architecture planning only. Individual architectural decisions
  named in this baseline are formalized as their own ADRs (`docs/adr/`)
  before implementation, per the ADR-first process used throughout Sprints
  1 and 2. This document is not itself an implementation plan and should
  not be treated as one — it defines the shape and constraints that the
  ADRs and code that follow must satisfy.

This document is frozen as written at approval time. Corrections or
revisions, if any become necessary, are appended as dated notes (matching
this project's ADR convention) rather than silently edited.

---

## 1. Sprint 3 Objective and Success Criteria

**Objective: Production Document Processing Pipeline.**

Sprint 3 is not "add a worker." The worker is one component inside a larger
architectural upgrade: turning document processing from a single
synchronous call into a production-shaped pipeline with durable
submission, controlled execution, defined retry behavior, accurate status
reporting, and accountable audit history. Async execution is necessary but
not sufficient for that goal — it must be designed alongside retry policy,
state modeling, and observability from the start, or it will need rework
the moment any of those show up as an afterthought.

**Success criteria:**
- Document processing is a durable, multi-stage pipeline, not a single
  blocking call — a slow or rate-limited Anthropic call no longer occupies
  an HTTP request/response cycle for its full duration.
- The system has an explicit, ADR-defined state model for both the
  document and the processing job, with illegal transitions structurally
  prevented, not just avoided by convention.
- Every `/documents*` action is attributable to a named caller, with a
  durable audit record that does not itself become a PHI-exposure surface.
- The architecture is designed so future metrics (queue depth, stage
  durations, retry/failure counts) can be derived from the data model that
  already exists, without a second pass to "add observability" later.
- No new paid third-party dependency, and no infrastructure heavier than
  the problem justifies.

## 2. Processing Lifecycle Architecture

The full lifecycle, with each stage's architectural role — not its
implementation:

**Upload → Queue → Worker → Retry Policy → Status Tracking → Audit → Completion**

- **Upload.** Unchanged by this sprint. The existing registry + storage
  abstraction is the entry point where a document gets a stable identity.
  Everything downstream operates on that identity.
- **Queue.** The durable handoff between "a processing request exists" and
  "a worker is executing it." Its architectural purpose is decoupling: the
  caller's request completes without waiting on the outcome, and a
  submitted unit of work survives a process restart or crash because it's
  durably recorded, not held only in memory. This is the boundary that
  makes the rest of the pipeline meaningful — without it, "retry" and
  "status tracking" have nothing durable to operate on.
- **Worker.** The executor, not a second copy of the pipeline. It pulls a
  unit of work from the queue and invokes the *same* OCR → PHI-gate →
  field-extraction logic Sprint 2 already built — that logic does not
  change or move. The worker's architectural job is orchestration and
  timing: claim work, execute it, record what happened. Sprint 2's
  pipeline stages remain the source of truth for *how* extraction happens;
  the worker only changes *who calls them and when*.
- **Retry Policy.** A defined, explicit policy for what happens when a
  unit of work fails — specifically, the distinction between failures that
  might succeed on a later attempt (rate limit, transient network error)
  and failures that never will (malformed input, unsupported content type,
  a missing API key). This must be a designed decision, not implicit
  behavior inferred from whatever the worker loop happens to do.
- **Status Tracking.** The observable answer to two related but distinct
  questions: "what is the current standing of this document" and "what
  happened during this specific processing attempt." This distinction is
  architecturally necessary, not cosmetic — see section 3.
- **Audit.** A durable record of *who* initiated *what* action *when* —
  orthogonal to processing status. Audit answers accountability questions
  ("who triggered this document, and this API spend"); status tracking
  answers progress questions ("is it done yet, and did it work").
  Conflating the two produces a log that's bad at both jobs.
- **Completion.** The terminal boundary from the caller's perspective. Once
  reached, the document's externally-visible status reflects a final
  outcome — but that document-level terminal state is a *summary* of
  potentially several job attempts underneath it, not the same thing as
  any single job reaching its own terminal state.

## 3. Document Lifecycle vs. Job Lifecycle — Dedicated Architectural Concepts

These must be modeled as **two independent state models**, not one shared
enum, and not a job status folded into the document row.

**Document lifecycle** answers: *what is currently true about this
artifact, from the perspective of an API consumer?* Illustrative
(non-final) states: `Uploaded`, `Processing`, `Extracted`, `Validated`,
`Failed`.

**Job lifecycle** answers: *what happened during one specific execution
attempt against that document?* Illustrative (non-final) states: `Queued`,
`Running`, `Retrying`, `Completed`, `Failed`, `Cancelled`.

These are not the same concept wearing two names — they have different
cardinality and different purposes:

- A document has **exactly one** current standing at any time. A document
  can have **zero, one, or many** job attempts over its life — a retry is
  a new (or continued) job, not a new document, and today's system already
  allows re-`/process`ing a document, which under an async model becomes
  explicitly "another job attempt," not an implicit re-run.
- Collapsing them loses information the retry policy and observability
  work both need: how many attempts happened, why each one failed, and how
  long each took. A document status of `Failed` alone cannot answer "was
  this the third attempt, and did the first two fail on rate limits or on
  a bad file?" — only a separate job history can.
- Coupling them also produces awkward, undefined states under the current
  single-enum model — there is no way today to represent "processing,
  attempt 2 of 3" without inventing new document statuses for what is
  really job-level information.

**Recommendation: keep these as two independent models**, correlated (a
document's displayed status can be derived from its most recent or active
job) but never merged into a shared state machine. The exact state names
above are illustrative and open for discussion in the state-machine ADR
(section 4) — the independence of the two models is not.

## 4. Processing State Machine — Requires Its Own ADR

**Today, document status has no transition validation at all.**
`update_status()` sets any `DocumentStatus` onto any document
unconditionally — confirmed directly in the current codebase. Nothing
prevents an invalid jump (e.g., `Validated` → `Processing` → `Uploaded`)
from being written by a future bug. This has been tolerable so far because
the only caller of `update_status()` is a single, linear, synchronous
function. It stops being tolerable the moment a worker, retries, and
concurrent job attempts are introduced — those are exactly the conditions
under which an unvalidated state machine produces silently inconsistent
documents (e.g., a document marked `Validated` while its last job is still
`Running`, or a document stuck in `Processing` with no job actually in
flight after a crash).

**Recommendation: before any worker code is written, produce an ADR that
defines the legal transition graph for both the document lifecycle and the
job lifecycle** — as an explicit table or diagram, not inferred from call
sites. That ADR needs to resolve concrete questions such as:
- Can a document move from `Failed` back to `Processing` (a manual retry —
  today's implicit behavior), and should that require a new job, not a
  mutation of an old one?
- Can a document move directly from `Validated` to `Processing`
  (re-processing an already-successful document), or should that be
  disallowed/require an explicit override?
- Can a job move from `Completed` to `Retrying` (no — retries only apply
  to non-terminal failures), or from `Queued` directly to `Cancelled` vs.
  `Running` to `Cancelled` (can an in-flight external API call be safely
  cancelled, or only prevented from starting)?

This ADR is a prerequisite for the worker epic, not a parallel task —
building the worker against an undefined state model guarantees rework
once the model is formalized.

## 5. Candidate Epics, Ranked by Priority

| Rank | Epic | Why this rank |
|---|---|---|
| 1 | **Production processing pipeline architecture** (state machine + document/job lifecycle separation, formalized via ADR) | Everything else in this sprint depends on this being decided first — it is the schema of agreement the worker, retry policy, and audit trail all get built against. |
| 2 | **Background worker** | The concrete mechanism that executes the pipeline defined by (1). Directly addresses the live risk Sprint 2 introduced: a slow/rate-limited third-party call sitting inside a synchronous request handler. |
| 3 | **Retry and failure handling** | Only meaningful once a durable job model exists to retry *against* — not a standalone feature bolted onto the current single-shot call. |
| 4 | **Named API keys / identity** | Additive to the existing auth middleware; doesn't block on the pipeline work, but is more valuable once there's a real job lifecycle to attribute actions to. |
| 5 | **Audit trail** | Depends on both (4) (who) and (1)/(2) (what happened) being defined — sequencing it last avoids building an audit schema against an undefined identity model or an undefined job lifecycle. |
| 6+ | RAG/search, vector databases, embeddings, analytics, layout analysis, additional LLM providers | Explicitly out of scope this sprint — see section 11. |

## 6. Dependencies on Sprint 2

- The pipeline redesign exists *because of* Sprint 2: before real field
  extraction, `/process` only did local OCR and regex validation — fast,
  deterministic, no external failure mode worth building a queue and
  retry policy around. The Anthropic call is what changes the risk profile
  enough to justify this sprint's scope.
- The worker must call the *existing* Sprint 2 pipeline stages (OCR → PHI
  gate → field extraction) unchanged — this sprint is about *how* and
  *when* that logic is invoked, not a rewrite of it.
- The graceful-failure architecture from ADR-0012 and ADR-0019 (clean
  `FieldExtractionError` → clean failure, never a crash) is the correct
  foundation for the job-level failure states in section 3 — a job's
  `Failed` state should be reached the same way a document's `Failed`
  status is reached today: cleanly, with a clear reason, never via an
  unhandled exception.
- The audit epic's motivation is a direct consequence of Sprint 2: a
  per-call-cost external API is what makes "who triggered this spend"
  newly load-bearing, not just good hygiene.
- Sprint 2's one open item (live Anthropic verification, deferred on
  missing credits) is independent of this sprint and should not block it.

## 7. Architectural Decisions That Must Be Made Before Implementation

- **The state-transition ADR itself** (section 4) — the single hardest
  prerequisite, and the one most likely to cause rework if skipped.
- **Queue mechanism** — see the dedicated comparison in section 7a below.
- **API contract shape for `/process`.** Does it remain synchronous
  (blocking, as today) with the worker used only internally, or does it
  become "enqueue and return immediately," pushing status polling (or
  another notification mechanism) onto the caller? This is a behavior
  change to the existing demo flow and needs an explicit decision, not an
  assumption carried in from habit.
- **In-flight re-processing / duplicate-submission handling.** Today,
  calling `/process` twice creates two full sets of result rows with no
  guard. Once a queue exists, "two enqueue calls before the first job
  finishes" becomes a real, reachable race — the state-machine ADR must
  resolve what's legal here (reject, supersede, queue behind it).
- **Identity model scope.** Named API keys with a label/identifier — the
  smallest step up from a shared secret — versus full accounts, sessions,
  or OAuth. Given no UI and no multi-tenant requirement exists yet, this
  must be explicitly bounded down, not left open to grow.
- **Audit log content and redaction policy.** What gets recorded (caller
  identity, action, document identity, timestamp) versus what must never
  be recorded (raw extracted text, PHI-shaped content) — the audit log
  must not become a second, unguarded copy of exactly the data ADR-0011
  and ADR-0019 were built to protect.

### 7a. Queue Mechanism: Postgres-Backed vs. Redis-Backed

**Recommendation: a Postgres-backed queue remains the preferred direction
unless a stronger engineering justification emerges.** This project has
consistently chosen the boring, dependency-minimal option when it clears
the bar (local Tesseract over cloud OCR, regex over Presidio, a
single-provider LLM ABC over a provider tree) — the queue decision should
be held to the same standard, evaluated on the dimensions that actually
matter here, not on which option is more commonly reached for elsewhere:

| Dimension | Postgres-backed queue | Redis-backed (RQ/Celery/arq) |
|---|---|---|
| Operational simplicity | No new service; reuses the database already running and already backed up/monitored as part of this stack | A new stateful service to run, secure, and keep healthy alongside Postgres |
| Deployment complexity | No change to `docker-compose.yml` topology | New service definition, new health check, new dependency ordering |
| Testing | Consistent with the existing SQLite-for-tests / Postgres-for-runtime split (ADR-0004) — queue behavior can be exercised the same way | Requires an in-memory or fake Redis for tests, or skipping queue-specific tests in the fast suite — a new testing pattern this project doesn't currently have |
| Maintainability | One less technology for a small team/solo maintainer to reason about, patch, and upgrade | Mature, well-documented tooling, but a genuinely separate operational surface with its own failure modes |

Redis becomes the right answer only if a concrete requirement emerges that
Postgres genuinely can't satisfy at this project's scale (e.g., a
demonstrated throughput ceiling on `SKIP LOCKED`-style polling) — not
because it's the more common choice for job queues in general. That
justification does not exist yet and should not be assumed into this
proposal.

## 8. ADRs Likely Required

- *"Document and job state machines: legal transitions"* — the section 4
  prerequisite; almost certainly the first ADR of this sprint.
- *"Queue mechanism for asynchronous document processing"* — the
  Postgres-vs-Redis comparison in 7a, formalized with any further
  findings.
- *"`/process` API contract for asynchronous processing"* — sync-blocking
  vs. enqueue-and-poll, and whether it's a breaking change accepted early
  (as ADR-0005 did for pagination) or introduced as a new, additive
  surface.
- *"In-flight re-processing / duplicate-submission handling"* — resolves
  the race condition named in section 7.
- *"Retry and backoff policy for processing jobs"* — transient-vs-terminal
  failure classification, attempt limits, backoff shape.
- *"Per-caller identity replaces the shared static API key"* — explicitly
  scoped to named keys, not sessions or OAuth.
- *"Audit log schema and redaction policy"* — what's recorded, what's
  structurally excluded, retention.

## 9. Observability: Designing for Future Metrics Without Building Monitoring Infrastructure

No Prometheus, Grafana, dashboards, or monitoring stack this sprint — that
would be scope well beyond what's justified right now. Instead, the
pipeline architecture from section 2 should be designed so the following
become **derivable from the data model that already needs to exist**, not
bolted on later:

- **Queue depth** — a natural consequence of jobs being durably recorded
  rather than held in memory; if a job's current state is queryable, "how
  many are currently queued" is a query against existing state, not new
  instrumentation.
- **Processing duration, OCR duration, extraction duration** — a
  consequence of the job lifecycle (section 3) recording *when* each stage
  transition happens, not just *that* it happened. If the job model
  captures the timing of its own state transitions as a first-class part
  of what a job is, per-stage duration is arithmetic on data that already
  exists for other reasons (retry policy, audit, status reporting) — not a
  separate metrics-collection concern.
- **Retry count and failure count** — a direct consequence of the job
  lifecycle explicitly modeling `Retrying` as a distinct state and
  preserving job history rather than overwriting a job in place. If
  retries are represented as attempts within a job's recorded history,
  counting them is a query, not new tracking logic.

The principle for this sprint: **make these numbers queryable later by
designing the job/document lifecycle correctly now — do not build the
collection, aggregation, or visualization layer.** Getting the state model
right (section 4) is what makes this possible cheaply later; getting it
wrong is what would force a second migration specifically to "add
observability."

## 10. Risks and Trade-offs

- **The state-machine ADR is a real prerequisite, not a formality.**
  Skipping straight to worker implementation without it is the single
  most likely source of Sprint 3 rework — exactly the failure mode this
  baseline is structured to prevent.
- **Breaking the synchronous demo flow.** The README's upload → process →
  result walkthrough currently assumes `/process` finishes inline. Moving
  to enqueue-and-poll changes that shape for every existing example and
  test — a deliberate, discussed trade-off (section 7), not a side effect
  to discover afterward.
- **New infra risk is contained, not eliminated.** A Postgres-backed queue
  avoids a new service, but polling-based queues have their own
  correctness concerns (lock contention, poll interval tuning) that need
  real design attention, not just "no new Docker service" as the whole
  justification.
- **The worker does not address Sprint 2's deferred item.** It makes the
  system more resilient to a slow or rate-limited Anthropic call; it does
  not get any closer to verifying real inference, which remains blocked on
  credits, independent of this sprint.
- **Identity scope creep.** "Per-caller identity" can silently grow into
  full auth (sessions, roles, permissions) if not held to the explicitly
  narrow scope stated in section 7 — named keys and audit trail, nothing
  more, this sprint.
- **Audit log as a second PHI surface.** An audit trail that logs request
  or extraction content "for completeness" would quietly reintroduce the
  exact risk ADR-0011 and ADR-0019 were built to close — this must be a
  first-class constraint in its ADR, not a review-time catch.

## 11. Explicitly Out of Scope for Sprint 3

- RAG
- Search
- Vector databases
- Embeddings
- Analytics
- Layout analysis
- OAuth / SSO / RBAC
- Kafka / RabbitMQ (or any broker beyond what section 7a's comparison
  justifies)
- Additional LLM providers

## 12. Recommended Implementation Order

1. **Production processing pipeline architecture** (state machine ADR,
   document/job lifecycle separation, queue-mechanism ADR). Everything
   downstream is built against this — doing it first is what prevents
   rework, not just good sequencing hygiene.
2. **Background worker.** Implements the mechanism the architecture in (1)
   defines. Cannot be meaningfully started before (1) exists, since "what
   does the worker claim, execute, and record" is exactly what (1)
   specifies.
3. **Retry and failure handling.** Operates *within* the worker's
   execution loop against the job lifecycle defined in (1) — not a
   bolt-on feature, a direct consumer of the state machine and the
   durable job history the queue provides.
4. **Named API keys / identity.** Independent of (1)–(3) technically, but
   sequenced after the processing architecture is stable so it doesn't
   need to guess at what a "caller-attributed action" looks like once jobs
   (not just requests) exist.
5. **Audit trail.** Sequenced last deliberately: it depends on (4) for
   *who*, and on (1)–(3) for *what happened* — building it earlier would
   mean building it against an undefined identity model, an undefined job
   lifecycle, or both.

This ordering minimizes rework by resolving the shared foundation (state
machine, lifecycle separation, queue mechanism) exactly once, before
anything is built against assumptions about it — the same discipline this
project already applies at the ADR level for every other cross-cutting
decision (OCR vendor, PHI detector, LLM provider scope).

## Decisions Signed Off At Approval

- The document/job lifecycle separation in section 3 is approved (state
  *names* were left open pending the state-machine ADR; the *separation*
  was not).
- Postgres-backed queue is approved as the starting direction (section
  7a), pending no stronger justification for Redis surfacing during
  implementation.
- Identity scope is approved as bounded to named API keys, not
  sessions/OAuth (section 7).
- The out-of-scope list in section 11 is approved as binding for this
  sprint.
- The `/process` API contract shape (sync vs. enqueue-and-poll) was
  **not** resolved at approval time — it is deferred to its own ADR
  (section 8), to be discussed before that specific decision is finalized.

---

**Dated note (2026-07-04):** Section 11's exclusion of RAG, search,
vector databases, and embeddings is superseded by
`docs/architecture/idp-platform-pivot-baseline.md`, which adopts these
as future in-scope phases. Analytics was resolved separately and on its
own terms by ADR-0029. This note is additive, per this document's own
stated correction convention — section 11 above is left as originally
written.
