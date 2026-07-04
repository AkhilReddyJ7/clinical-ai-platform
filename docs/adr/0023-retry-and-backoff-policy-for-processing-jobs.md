# 0023: Retry and backoff policy for processing jobs

- **Status:** Accepted
- **Date:** 2026-07-04
- **Follows up on:** `docs/architecture/sprint-3-design-baseline.md`
  (section 8, "Retry and backoff policy for processing jobs"),
  [0020](0020-document-and-job-state-machines.md) (defines the `running` /
  `retrying` / `failed` states this policy operates within).

## Context

Today, nothing retries. `AnthropicFieldExtractionPipeline` catches
`RateLimitError`, `APIStatusError`, and `APIConnectionError` and converts
all three, uniformly, into a single `FieldExtractionError` — a clean
failure, but a first-and-only attempt every time, whether the underlying
cause was a one-off network blip or a permanently invalid API key. The
job lifecycle in [0020](0020-document-and-job-state-machines.md) added a
`retrying` state specifically to change this, but a state existing is not
a policy — this ADR defines the policy that state serves.

**This retry layer sits above, not in place of, the Anthropic SDK's own
internal retries.** The SDK already retries 408/409/429/5xx and
connection errors within a single call (`max_retries`, default 2,
exponential backoff on the order of seconds). That layer absorbs
short-lived, single-call blips. The policy below operates one level up:
deciding whether an entire **job attempt** — which may have already
exhausted the SDK's own retries and still failed — is worth trying again
later, and how long "later" should be. The two layers are complementary,
not redundant: the SDK's retries are invisible to the job model; this
ADR's retries are visible, bounded, and recorded as part of job history.

## Decision

### 1. Failure classification (decided first — everything else depends on it)

Every way a job attempt can end is classified into exactly one of three
buckets. This is a stricter split than today's code makes (today,
`APIStatusError` is caught as one undifferentiated category) —
implementing this ADR requires distinguishing *within* that category by
status code, not just catching it as a whole.

**Not a failure at all — job completes.** A job's execution can correctly
conclude that the document doesn't get real content, and that is still a
*successful* execution, per [0020](0020-document-and-job-state-machines.md)'s
"completed ≠ validated" principle, extended here to cover every such case
explicitly:
- The PHI gate correctly identifies PHI-shaped content and halts before
  the LLM call ([0011](0011-phi-detection-gates-persistence.md),
  [0019](0019-anthropic-field-extraction-and-phi-gates-llm-call.md)). The
  pipeline did exactly what it was supposed to do.
- Full validation (`RequiredFieldsValidator`) fails because the model
  genuinely didn't find a required field. The extraction ran to
  completion; the *content* didn't validate.

In both cases: job → `completed`, document → `failed`. Retrying either
case would mean retrying a job that already succeeded at its actual task
— a wasted, pointless call.

**Terminal — job fails immediately, no retry.** The failure is
deterministic: the same input will produce the same outcome on a second
attempt, so retrying only delays an inevitable failure while spending
real API cost.
- `ExtractionError` from the OCR stage (corrupted file, unsupported
  content type, decompression bomb, page-count limit) — happens before
  the LLM is ever reached, and is a property of the input bytes, not of
  network conditions.
- Missing or invalid Anthropic API key (`FieldExtractionError`: "API key
  is not configured", or an authentication/permission `APIStatusError` —
  401/403). A configuration problem that requires a human to fix, not
  time to pass.
- A malformed or unusable model response (wrong `stop_reason`, no usable
  `tool_use` block) — classified terminal by default. This is a judgment
  call, not a certainty: an individual response is a sampling event and
  *could* differ on a second attempt, but a malformed response is far
  more likely to indicate a prompt/schema mismatch that will recur than a
  one-off fluke. Treating it as terminal avoids spending retries on a
  systemic problem; revisit if evidence (once real inference is verified,
  per Sprint 2's deferred item) shows otherwise.
- A `4xx` `APIStatusError` other than rate limiting (e.g., a malformed
  request) — the request itself is invalid; retrying an invalid request
  produces the same invalid request.

**Transient — eligible for retry within budget.**
- `RateLimitError` (429).
- `APIConnectionError` (network failures, timeouts) that persisted
  through the SDK's own internal retries.
- A `5xx`/overloaded `APIStatusError` — server-side, plausibly resolved
  by the time of a later attempt.

### 2. Retry policy

A job attempt classified **transient** may be retried up to a small,
fixed number of additional times before the job is abandoned — a bounded
budget, not unbounded persistence, since each attempt costs a real API
call against a paid, rate-limited service. **A retry re-runs the entire
job from the beginning** (OCR through field extraction), not just the
failed stage. This is a deliberate simplicity choice: OCR is local and
free, so re-running it wastes no money and negligible time next to the
network call it precedes; resuming from a specific failed stage would
require persisting intermediate state between attempts (checkpointing) —
real complexity this project doesn't need yet, given only the
field-extraction stage has any external, retryable failure mode today.
Revisit if OCR itself ever becomes slow, costly, or failure-prone enough
that re-running it on every retry stops being free.

### 3. Backoff strategy

Exponential backoff with jitter between attempts — the same shape the
Anthropic SDK already uses internally for its own retries, chosen for
consistency with the layer beneath it rather than inventing an
independent strategy. A fixed or linear delay would either retry too
aggressively against a rate limit (defeating the point of backing off at
all) or waste time waiting longer than necessary once the underlying
condition has cleared. The delay grows with each attempt and is capped at
a maximum, so worst-case total latency for an abandoned job stays bounded
rather than growing unboundedly with the attempt count. Exact starting
delay, growth factor, and cap are tunable defaults, not fixed
architectural constants — the same posture this project already takes
with `max_pdf_pages` and `anthropic_timeout_seconds`.

### 4. Terminal failure behavior

Once a job's retry budget is exhausted, or a terminal-classified failure
occurs on any attempt: job → `failed`, document → `failed`
(per [0020](0020-document-and-job-state-machines.md)'s transition table),
via the same graceful-failure architecture already established
([0012](0012-graceful-extraction-failure-handling.md),
[0019](0019-anthropic-field-extraction-and-phi-gates-llm-call.md)) — a
clean, informative failure record, never an unhandled exception.

**No automatic re-enqueueing after terminal failure.** The document
simply remains `failed` until a caller explicitly resubmits it via
`POST /process`, which — per [0020](0020-document-and-job-state-machines.md)
— is a legal `failed → processing` transition that creates a **new** job,
distinct from the exhausted one. This is a deliberate choice against
auto-requeueing: it bounds cost and avoids any risk of an unbounded retry
loop against a systemic problem (e.g., a genuinely invalid key would
otherwise requeue forever), and it matches how a failed document already
behaves today — nothing happens to it until a caller acts.

### 5. State transitions

Restated precisely against [0020](0020-document-and-job-state-machines.md)'s
job lifecycle, now with the policy above governing which edges get taken:

- `queued → running` — a worker claims the job (attempt 1).
- `running → completed` — the pipeline reached any correct conclusion
  (real fields extracted, PHI correctly halted the call, or validation
  correctly reported missing fields). Per section 1, none of these retry.
- `running → retrying` — a **transient**-classified failure occurred and
  the retry budget is not yet exhausted; the backoff delay from section 3
  is scheduled before the next attempt.
- `retrying → running` — the backoff delay elapsed; a worker claims the
  job again for the next attempt.
- `running → failed` — a **terminal**-classified failure occurred on any
  attempt, or a transient-classified failure occurred with the retry
  budget already exhausted.
- The document remains `processing` throughout every `queued` /
  `running` / `retrying` cycle within a single job — retries do not
  introduce new document-visible states. The document only leaves
  `processing` when the job reaches a terminal state (`completed` or
  `failed`), exactly as [0020](0020-document-and-job-state-machines.md)
  already defined.

### 6. Audit implications

A job's attempt history — each attempt's classification, outcome, and
timing — must be **retained, not overwritten in place**, as a first-class
part of what a job is. This is what makes the Sprint 3 baseline's
observability goal (section 9: retry count and failure count as a
consequence of correct modeling, not separately built) actually true —
if an attempt's record is discarded once superseded by the next one, "how
many times did this job retry" stops being a query and becomes
unanswerable after the fact.

This is distinct from the **audit trail** epic itself (its own ADR, per
the baseline's list, not decided here). Two different kinds of event, not
to be conflated when that ADR is written:
- **Attempt history is job-internal execution detail** — retries within a
  single job are the system retrying itself, not a caller action. It
  belongs in the job's own record.
- **Manual resubmission is a caller action** — a human or system calling
  `POST /process` again on a `failed` document, creating a new job, is
  exactly the kind of accountable event ("who resubmitted this, and when,
  and how many times") the future audit trail should capture. A job's
  internal retries and a caller's repeated manual resubmissions are
  different questions and should not be recorded in the same place or
  conflated when the audit-trail ADR defines what gets logged.

## Consequences

- **Requires finer-grained exception handling than exists today.**
  `AnthropicFieldExtractionPipeline` currently treats all of
  `APIStatusError` as one category; implementing this policy means
  splitting it by status code (401/403/400 → terminal, 5xx → transient) —
  a real change to the pipeline's error handling, not just new code
  around it. Implementation work for the worker epic, not resolved here.
- **The malformed-response-is-terminal call is explicitly a judgment
  call, named as such**, so a future contributor doesn't mistake it for
  settled fact — it's the one classification in section 1 most likely to
  be revisited once real inference is actually verified (Sprint 2's
  deferred item).
- **Retrying the whole job, not just the failed stage, is a simplicity
  trade-off with a named revisit trigger** (OCR becoming slow, costly, or
  failure-prone) — not an oversight.
- **No new infrastructure.** This policy is expressed entirely through
  the job lifecycle and queue already defined in
  [0020](0020-document-and-job-state-machines.md) and
  [0021](0021-postgres-backed-job-queue.md) — no new service, no new
  dependency.
- Completes the ADR set for Sprint 3's "production processing pipeline
  architecture" priority (state machine, queue, API contract, retry
  policy). The background worker epic can now be implemented against a
  fully specified architecture rather than filling in gaps as it goes.
