# 0027: Audit log schema and redaction policy

- **Status:** Accepted
- **Date:** 2026-07-04
- **Follows up on:** `docs/architecture/sprint-3-design-baseline.md`
  (section 2's "Audit" definition, section 7's "Audit log content and
  redaction policy," section 8 listing *"Audit log schema and redaction
  policy"* as a required ADR), [0020](0020-document-and-job-state-machines.md)
  (the document/job lifecycle audit answers "what happened" against),
  [0026](0026-named-api-keys-replace-the-shared-static-secret.md) (the
  resolved caller label this ADR's "who" is built on). Epic 5 in the
  baseline's ranked epic list (section 5) — sequenced last deliberately,
  since it depends on both.

## Context

`modules/audit/` has existed as an empty placeholder directory since the
initial repository scaffold — confirmed directly, zero files in it, and
the README already states plainly: "there's no concept of a user,
session, or per-caller audit trail yet." Nothing today records *who*
uploaded a document or *who* triggered a processing job (an action that
now costs real money per ADR-0019's Anthropic call) — only *what state*
that document/job is currently in, which ADR-0020's models already
answer.

The baseline is explicit that this is a **distinct concern from status
tracking**: "Audit answers accountability questions... status tracking
answers progress questions... Conflating the two produces a log that's
bad at both jobs" (section 2). It's also explicit that this is
**distinct from a job's own internal retry history**
([0023](0023-retry-and-backoff-policy-for-processing-jobs.md) section 6):
a job retrying itself is the system acting, not a caller acting; a human
or service calling `POST /process` again is exactly the accountable event
this ADR concerns itself with. And it names the one risk most likely to
be gotten wrong: **"An audit trail that logs request or extraction
content 'for completeness' would quietly reintroduce the exact risk
[0011](0011-phi-detection-gates-persistence.md) and
[0019](0019-anthropic-field-extraction-and-phi-gates-llm-call.md) were
built to close"** (section 10) — this ADR treats that as a first-class
constraint, not a review-time catch.

## Decision

### 1. A new table, not a log file or the existing event system

`modules/audit/models.py` adds `AuditLogEntry`, a new Postgres-backed
table (ADR-0021's precedent: this project already treats Postgres as the
durable store for exactly this kind of accountable, queryable record —
`Document`, `Job`). Not the in-process event system
(`modules/processing/events.py`): that system is explicitly built for
*observability* (metrics, logs) with no durability guarantee and no
consumer contract beyond "best-effort, in this process" — audit requires
the opposite (durable, queryable, survives a restart), and conflating the
two would compromise both, the same reasoning the baseline gives for not
conflating audit with status tracking.

### 2. Schema: only what's needed to answer "who did what, when" — structurally, not by discipline

```python
class AuditAction(str, enum.Enum):
    DOCUMENT_UPLOADED = "document_uploaded"
    JOB_ENQUEUED = "job_enqueued"

class AuditLogEntry(Base):
    id: uuid.UUID            # primary key
    caller: str               # the resolved API key label (ADR-0026)
    action: AuditAction
    document_id: uuid.UUID | None   # FK -> documents.id
    job_id: uuid.UUID | None        # FK -> jobs.id (only set for JOB_ENQUEUED)
    created_at: datetime
```

**No free-text field, anywhere, on this model.** This is the redaction
policy: not a filter applied to content before writing it, which would
require trusting every future call site to apply it correctly, but a
schema that structurally cannot hold raw text, filenames, extracted
fields, or PHI-shaped content in the first place — there is no column
capable of storing it. `document_id`/`job_id` are enough to correlate an
audit entry with the `Document`/`Job` rows that already hold whatever
content is appropriate to store, at the retention/access-control policy
those tables already have — this table never duplicates it.

Immutable and append-only: an `AuditLogEntry`, once written, is never
updated (no `updated_at`) or deleted by application code. Accountability
requires the record of an action to be as durable as the action itself.

### 3. Recorded actions this sprint: the two caller-attributed actions that exist today

- `DOCUMENT_UPLOADED` — one entry per successful `POST /documents` (the
  document now exists because of a specific caller).
- `JOB_ENQUEUED` — one entry per successful `POST /documents/{id}/process`
  (the specific "who triggered this API spend" the baseline names
  directly — this is the action that eventually costs a real Anthropic
  API call).

Both routes already resolve `caller: str` via
[0026](0026-named-api-keys-replace-the-shared-static-secret.md)'s
`require_api_key` dependency; this ADR is what that resolved value is
*for*, closing the loop the identity ADR opened. No other action is
recorded this sprint: a job's own retry/stale-reclaim transitions are the
system acting on itself, not a caller acting (ADR-0023 section 6), and
remain visible only via `Job.retry_count`/`last_error`, exactly as today.

### 4. Recording never blocks or fails the action it records

Audit is "orthogonal to processing status" (baseline section 2) —
extended here to mean orthogonal to the *success* of the action it
audits, not only its status. `modules/audit/service.py`'s `record_action`
catches its own failures (logs and rolls back its own write) rather than
propagating: a caller successfully uploading a document must not receive
a `500` because the audit write itself hit a transient DB error. This
mirrors `modules/processing/events.py`'s existing precedent exactly
("observability must never be able to break execution") — audit is a
second, independent case of the same principle, not a new one.

### 5. Explicitly out of scope this ADR

- **No query/read API.** Nothing exposes `GET /audit` or similar this
  sprint. The baseline's scope for this epic (section 8) is the schema
  and recording mechanism, not a browsing surface — adding one later is
  additive to this schema, not a redesign of it.
- **No retention/deletion policy.** Entries are kept indefinitely for
  now; a real archival/retention requirement (compliance-driven or
  volume-driven) would define its own policy against real data, not a
  guess made here.
- **No structured "why"/reason field, no request metadata (IP, user
  agent).** Anything beyond who/what/when/which-document risks becoming
  exactly the "logs it for completeness" pattern section 10 warns
  against; add a field only when a concrete, named consumer needs it.

## Consequences

- **New migration, new model, new module (`modules/audit/`)** — the one
  piece of Sprint 3 identity/audit work that genuinely needs new
  infrastructure (unlike [0026](0026-named-api-keys-replace-the-shared-static-secret.md),
  which deliberately avoided a new table): a durable, queryable
  accountability record is the entire point of this epic, so unlike named
  keys there's a concrete, immediate consumer (this ADR itself) for
  Postgres-backed storage.
- **Two existing routes gain one additional, non-blocking DB write
  each** (`upload_document`, `process_document`) — no change to their
  response shape, status codes, or existing behavior.
- **Sets up, but does not build, real accountability reporting.** A
  future "show me everything caller X did" or "show me this document's
  full history" is now a query against data that already exists, per the
  baseline's section 9 observability principle applied here to audit
  instead of metrics — not a redesign.
- **Deliberately minimal**: two actions, five columns, no free-text
  field. A future contributor finding this thin should read the ADR's
  named omissions above before adding to it, not assume the thinness was
  an oversight.
