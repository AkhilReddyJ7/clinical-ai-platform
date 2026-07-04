# 0020: Document and job state machines: legal transitions

- **Status:** Accepted
- **Date:** 2026-07-04
- **Follows up on:** `docs/architecture/sprint-3-design-baseline.md`
  (sections 3 and 4), the approved Sprint 3 design baseline. First ADR of
  Sprint 3, per the baseline's recommended implementation order.

## Context

Document status has never had transition validation. `update_status()`
(`modules/ingestion/service.py`) sets any `DocumentStatus` onto any
document unconditionally — confirmed directly in the current code, not
assumed. This has been tolerable through Sprints 1–2 because the only
caller is a single, linear, synchronous function (`process_document`)
that always transitions states in the same order. It stops being
tolerable once a queue, a worker, and retries are introduced: those are
exactly the conditions under which an unvalidated state machine produces
silently inconsistent documents (a document marked `validated` while its
last job is still running; a document stuck in `processing` with no job
actually in flight after a crash).

The Sprint 3 baseline also established that **document state** and **job
state** must be two independent models, not one shared enum — a document
has exactly one current standing, but can have zero, one, or many
processing attempts (jobs) over its life. This ADR defines the legal
transition graph for both, and — because the two questions turned out to
be inseparable — resolves the baseline's open "in-flight re-processing"
question as part of the same decision, rather than as a separate ADR (see
Consequences).

## Decision

### Document lifecycle (states unchanged from today; transitions now explicit)

No new document states are introduced. `DocumentStatus` stays
`uploaded`, `processing`, `extracted`, `validated`, `failed` — renaming
working, tested states would be churn with no architectural benefit.
What's new is the legal transition graph:

| From | To | Legal? | Notes |
|---|---|---|---|
| `uploaded` | `processing` | Yes | A job is created for the document. |
| `processing` | `extracted` | Yes | OCR + PHI gate + field extraction all completed without error. |
| `processing` | `failed` | Yes | Extraction error, PHI detected, or field-extraction error — matches today's three failure paths in `process_document`. |
| `extracted` | `validated` | Yes | Full validation pipeline passed. |
| `extracted` | `failed` | Yes | Full validation pipeline failed (e.g. a required field the LLM didn't find). |
| `failed` | `processing` | Yes | Manual retry — today's implicit behavior, made explicit. Creates a **new** job; never mutates the failed one. |
| `validated` | `processing` | **No, by default** | See below. |
| everything else | — | No | Not reachable under normal operation; a future validator can reject any transition not in this table. |

**`validated → processing` is disallowed by default.** A validated
document is a trusted, completed result. Silently allowing arbitrary
re-processing risks overwriting a good result with a worse one (LLM
outputs are not guaranteed deterministic) and spends real API cost with no
forcing function. If a genuine need for "force re-process a validated
document" emerges (an operational override, not a routine action), it
should be introduced later as its own explicit, audited action — not as a
default-legal transition discovered by accident.

### Job lifecycle (new)

States: `queued`, `running`, `retrying`, `completed`, `failed`,
`cancelled`. One job represents one *series* of attempts at processing a
document — internal retries move the same job between `running` and
`retrying`; they do not create new jobs. A new job is only created when a
document is resubmitted for processing (e.g., a `failed` document manually
retried per the table above).

| From | To | Legal? | Notes |
|---|---|---|---|
| `queued` | `running` | Yes | A worker claims the job. |
| `running` | `completed` | Yes | The pipeline executed to completion — see the "completed ≠ validated" note below. |
| `running` | `retrying` | Yes | A transient failure occurred and the retry budget isn't exhausted (ADR to follow, per the baseline's "Retry and backoff policy" item). |
| `retrying` | `running` | Yes | The worker picks the job back up after its backoff interval. |
| `running` | `failed` | Yes | A non-retryable error, or the retry budget is exhausted. |
| `queued` | `cancelled` | Yes | Cancelled before any worker claimed it. |
| `retrying` | `cancelled` | Yes | Cancelled while waiting in backoff — the job isn't actively executing at this point, so this is safe. |
| `running` | `cancelled` | **No, this sprint** | See below. |
| `completed` / `failed` / `cancelled` | anything | No | All three are terminal. |

**`running → cancelled` is disallowed this sprint.** The field-extraction
call is a blocking synchronous request from inside a worker thread; there
is no cheap, safe way to interrupt it mid-flight without added complexity
this sprint doesn't need yet (the underlying HTTP call would either need
to complete or time out regardless of a "cancel" signal). If cancelling an
actively-running job becomes a real operational need, it's a follow-up
decision with its own trade-offs (e.g., a cooperative cancellation check
between retry attempts), not something to half-implement here.

**"Completed" means the execution finished, not that the document passed
validation.** A job that runs the full pipeline and lands on a document
status of `extracted → failed` (e.g., `RequiredFieldsValidator` failed
because the model didn't find an MRN) is a job that **completed** —
execution finished cleanly, exactly as designed by ADR-0012/0019's
graceful-failure architecture. A job only reaches job-level `failed` when
the *execution itself* couldn't finish (retry budget exhausted, a
non-retryable pipeline exception). Conflating "the job failed" with "the
document failed validation" would make job-level failure counts (a
planned observability signal, per the baseline's section 9) meaningless —
they'd be dominated by ordinary validation failures rather than actual
execution problems.

### Resolving "in-flight re-processing" (baseline section 7, folded into this ADR)

The baseline listed this as a candidate for its own ADR; it turned out to
be a direct consequence of the tables above and didn't need a separate
document. **A document may have at most one non-terminal job
(`queued`/`running`/`retrying`) at a time.** Submitting a new processing
request while one is already active is illegal — not queued behind it,
not silently ignored, rejected outright, matching this project's existing
fail-explicitly-not-silently posture (e.g., ADR-0011's PHI gate, ADR-0012's
graceful failure). This is a direct consequence of the document-level
table above: a document can only be in `processing` due to exactly one
active job at a time, by construction, once this is enforced.

## Consequences

- **This ADR intentionally does not decide `/process`'s API contract**
  (synchronous vs. enqueue-and-poll) or the retry/backoff policy's
  specific limits and backoff shape. Both remain their own ADRs per the
  baseline (section 8) — this one only fixes the state graph both of them
  will be built against.
- **Consolidation, not scope creep:** the baseline named "in-flight
  re-processing / duplicate-submission handling" as a fourth, separate
  ADR. It's resolved above instead, because it turned out to be a direct
  corollary of the transition tables, not an independent design question.
  Recorded here explicitly so the consolidation is visible, not a silent
  scope change.
- **No implementation yet.** This ADR fixes the legal graph; enforcing it
  (rejecting illegal transitions at the point they'd occur) is
  implementation work for the worker epic, not this ADR. Today's
  `update_status()` remains unchanged until that epic lands — this ADR is
  the contract that implementation will be held to, not a retroactive
  claim that enforcement already exists.
- **`validated → processing` and `running → cancelled` are both
  deliberately conservative "no" decisions**, not oversights — each has a
  named reason above and a named path to revisit if a real need appears
  later. Flagging both explicitly so a future contributor doesn't
  "fix" them without first reading why they were disallowed.
- Sets up the next two Sprint 3 ADRs cleanly: the queue-mechanism ADR
  (job durability) and the `/process` API-contract ADR (what triggers
  `uploaded → processing`, and what a caller sees while a job is
  `queued`/`running`/`retrying`) both now have a fixed vocabulary of
  states to reference instead of inventing one inline.
