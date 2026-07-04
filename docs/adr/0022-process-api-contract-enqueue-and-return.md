# 0022: `/process` becomes enqueue-and-return; `/result` becomes the canonical status/result endpoint

- **Status:** Accepted
- **Date:** 2026-07-04
- **Follows up on:** `docs/architecture/sprint-3-design-baseline.md`
  (section 7, "API contract shape for `/process`" — explicitly left open
  at approval time), [0020](0020-document-and-job-state-machines.md)
  (document/job states this contract reports on),
  [0021](0021-postgres-backed-job-queue.md) (the queue this contract now
  fronts).
- **Precedent:** [0005](0005-paginated-response-envelope-breaking-change-accepted-early.md)
  — a breaking API change accepted early, on the same reasoning: no real
  external callers exist yet to protect against the break.

## Context

Today, `POST /documents/{id}/process` blocks for the full duration of OCR,
the PHI gate, and the Anthropic call, then returns the finished result in
one response. This directly conflicts with the Sprint 3 baseline's own
success criterion: "a slow or rate-limited Anthropic call no longer
occupies an HTTP request/response cycle for its full duration." Three
options were discussed: stay fully synchronous (rejected — defeats the
queue's purpose entirely), a bounded synchronous wait with async fallback
(rejected — real added complexity, two behaviors for one endpoint
depending on timing, and it doesn't remove the core risk, only bounds it),
and fully async enqueue-and-poll. Fully async was chosen, discussed
directly with the project owner rather than decided unilaterally, given
it's a breaking change to the current demo flow and README.

`GET /documents/{id}/result` already exists but today only distinguishes
two cases: processed (200, full result) or not-yet-processed-or-unknown
(404, same response either way). That collapse is no longer acceptable
once "not yet processed" must be split into "never submitted," "actively
queued/running/retrying," and the two terminal outcomes — a polling client
needs to tell "keep waiting" apart from "this will never resolve, wrong
ID."

## Decision

### `POST /documents/{id}/process`

No longer executes the pipeline inline. Enqueues a job (per
[0021](0021-postgres-backed-job-queue.md)) and returns immediately:

| Condition | Response |
|---|---|
| Document exists, no active job, status is `uploaded` or `failed` (legal per [0020](0020-document-and-job-state-machines.md)) | `202 Accepted`. Body confirms a job was created and its initial status (`queued`) — no extraction/validation content, since none exists yet. |
| Document does not exist | `404 Not Found` — unchanged from today. |
| Document already has an active job, or document status is `validated` (both illegal starting points per [0020](0020-document-and-job-state-machines.md)) | `409 Conflict`. Body states the reason (already processing, or already validated) and points the caller at `GET .../result` to observe current state — not a queued-behind-it or silently-ignored request, per this project's existing fail-explicitly posture (e.g. the PHI gate in [0011](0011-phi-detection-gates-persistence.md)). |

`extracted` is not listed as a legal or illegal starting *document* status
here deliberately: per [0020](0020-document-and-job-state-machines.md), it
is reachable only as a transient interior state within a single job's
own execution, never an at-rest state a caller should observe between
requests under normal operation. If it is ever observed at rest (e.g.
after a crash mid-job), it should be treated the same as an active job
for this decision's purposes — recovery from that exact scenario is
implementation-phase work for the worker epic, not resolved here.

### `GET /documents/{id}/result` — the canonical status/result endpoint

Becomes the **one place** a caller looks to answer both "what's the status"
and "what's the result," replacing today's narrower "give me the finished
result or 404" behavior. Exactly one outcome returns a non-`200` status —
every other case is `200` with the document's current state discriminated
in the response body. This is deliberate: it keeps the contract
deterministic (one true "this doesn't exist" signal, not several
different negative signals for different flavors of "not ready yet").

| Case | Response | Body content |
|---|---|---|
| **Document not found** | `404 Not Found` | No such document exists. Terminal for a polling client — stop, don't retry. |
| **Document never processed** | `200 OK` | Document status (`uploaded`); no job has ever been created; no extraction/validation content. Distinct from `404` specifically so a poll loop can tell "wrong ID, give up" apart from "right ID, nothing started yet." |
| **Processing in progress** | `200 OK` | Document status (`processing`); the *active job's own status* (`queued` / `running` / `retrying`) surfaced explicitly — this is where the document/job lifecycle separation from [0020](0020-document-and-job-state-machines.md) becomes directly visible to callers, rather than collapsing "queued" and "actively retrying after a transient failure" into one opaque "processing." No extraction/validation content yet (may be partial internally, but nothing durable to report until the job reaches a terminal state). |
| **Processing completed** | `200 OK` | Document status (`validated`); full `ExtractionResult` + `ValidationResult` content — same shape as today's success response. |
| **Processing failed** | `200 OK` | Document status (`failed`); whatever `ExtractionResult`/`ValidationResult` content the graceful-failure path produced ([0012](0012-graceful-extraction-failure-handling.md), [0019](0019-anthropic-field-extraction-and-phi-gates-llm-call.md)) — a failed *outcome* is still a successful *observation* of status, so it is not a 4xx/5xx. This matches the project's existing behavior (a failed document already returns `200` with `is_valid: false` today) and keeps the HTTP status code answering "did I successfully learn the status" rather than "did processing succeed." |

**This endpoint reports the document's current/most recent processing
attempt only.** Browsing full historical job-by-job attempt history (e.g.,
"show me all three prior failed attempts") is explicitly out of scope for
this ADR — a future extension once a real need for it appears, not
speculative generality added now.

### Explicitly rejected

- **No hybrid synchronous behavior.** No bounded wait, no timeout-based
  fallback between synchronous and async response shapes. One endpoint,
  one behavior, always.
- **No dual processing endpoints.** No separate "sync" and "async"
  variants of `/process`. `/process` means enqueue-and-return, full stop.

## Consequences

- **This is a breaking API change**, accepted early on the same reasoning
  as [0005](0005-paginated-response-envelope-breaking-change-accepted-early.md):
  no real external callers exist yet to protect against the break. The
  README's upload → process → result walkthrough, the end-to-end demo
  script, and any test asserting `/process` returns a finished result
  inline will need updating when this is implemented — tracked as
  implementation work for the worker epic, not resolved by this ADR.
- **`/result`'s "never processed" vs. "not found" split is a genuine
  behavior change**, not just documentation — today both cases return a
  bare `404`. Any caller (including this project's own tests) currently
  relying on `404` for "not processed yet" will need updating to expect
  `200` with a status field instead.
- **The job/document lifecycle separation is now caller-visible, not just
  internal.** Exposing the active job's specific status
  (`queued`/`running`/`retrying`) inside the "processing in progress"
  response is a direct, deliberate payoff of
  [0020](0020-document-and-job-state-machines.md) — without it, callers
  would only ever see a generic "processing" with no way to distinguish
  "hasn't started" from "failing and retrying."
- **Sets up (without deciding) the retry/backoff ADR and the observability
  groundwork** from the Sprint 3 baseline (section 9): once a job's status
  is a first-class part of this response, exposing attempt counts or
  per-stage timing later is an additive change to an existing field, not
  a redesign of this contract.
- **No implementation yet.** This ADR fixes the contract; the worker,
  queue-claiming logic, and the actual route changes are implementation
  work for the worker epic, held to this contract once built.
