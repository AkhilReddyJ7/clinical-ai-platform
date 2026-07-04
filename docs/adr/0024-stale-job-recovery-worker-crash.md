# 0024: Stale RUNNING job recovery — reclaiming jobs orphaned by a worker crash

- **Status:** Accepted
- **Date:** 2026-07-04
- **Follows up on:** [0020](0020-document-and-job-state-machines.md) (the job
  lifecycle this ADR operates within, without amending its transition
  graph), [0021](0021-postgres-backed-job-queue.md) (the atomic claim this
  ADR's recovery path is symmetric with),
  [0022](0022-process-api-contract-enqueue-and-return.md) (why a
  permanently non-terminal job is a user-visible problem, not just an
  internal accounting gap), [0023](0023-retry-and-backoff-policy-for-processing-jobs.md)
  (the retry budget and backoff machinery this ADR reuses rather than
  duplicating). Prompted by Sprint 3 Increment 3
  (`modules/processing/worker.py`, `claim_next_job` in
  `modules/processing/repository.py`): the worker loop and atomic claim
  now exist, and together they expose a gap neither prior ADR closes —
  nothing detects or recovers a job whose worker died mid-execution.

## Context

Increment 2 gave the queue an atomic claim (`claim_next_job`,
`SELECT ... FOR UPDATE SKIP LOCKED`); Increment 3 gave it a worker loop
that calls it. Together they move a job `queued -> running`. Neither
records *who* claimed a job, nor enforces any bound on how long it may
stay `running` — and per [0020](0020-document-and-job-state-machines.md)'s
job transition table, `running` has exactly three legal exits:
`completed`, `retrying`, `failed`. There is no `running -> queued` edge
and no timeout concept at all today.

If the process that claimed a job dies — crash, OOM kill, host failure, a
`SIGKILL` during a hung Anthropic call — after the claim's
`UPDATE ... status = 'running'` commits but before it reaches one of
those three exits, the job is now permanently `running` with no worker
left to finish it. Per [0020](0020-document-and-job-state-machines.md),
the document also stays `processing` forever — a document only leaves
`processing` when its job reaches a terminal state — and per
[0022](0022-process-api-contract-enqueue-and-return.md), `POST /process`
on that document now returns `409 Conflict` indefinitely, since a
non-terminal job already exists. **A crashed worker permanently strands
the document it was working on.** This is precisely the crash-survivability
gap the Sprint 3 baseline named as a goal ("a submitted unit of work
survives a process restart or crash") — durability of the *row*
([0021](0021-postgres-backed-job-queue.md)) is necessary but not
sufficient; nothing yet recovers a claim that was never released.

This is **not** a new outcome for [0023](0023-retry-and-backoff-policy-for-processing-jobs.md)'s
failure classification to absorb. That ADR's transient/terminal/
not-a-failure split classifies outcomes a job's **own execution** can
reach — a rate limit, a bad file, a validation miss. A stale claim is not
such an outcome: the execution never reached any conclusion at all,
because the worker process itself failed, not the work it was doing.
Recovering from it is a **state-machine recovery question** — what
happens to a `running` job when nothing is left to run it — and this ADR
treats it strictly as that: a recovery condition applied to the existing
`running` state, not a new entry in [0023](0023-retry-and-backoff-policy-for-processing-jobs.md)'s
classification of execution outcomes.

## Decision

### 1. Staleness is defined by elapsed time since the job's last write, not a heartbeat

No new column, and no periodic liveness ping from inside a running job.
`Job.updated_at` (already present since Increment 1, and already touched
by the claim's own `UPDATE` in Increment 2) is reused as the sole
liveness signal: a job is **stale** if it is `running` and
`now() - updated_at` exceeds a configured maximum running duration. This
is a deliberate simplicity choice, consistent with
[0023](0023-retry-and-backoff-policy-for-processing-jobs.md)'s rejection
of mid-job checkpointing: this project's jobs re-run from the beginning
on every attempt and have no internal progress to report mid-flight, so a
heartbeat column would only ever be able to say "still running" — which a
timeout on the one timestamp that already exists says just as well, for
one column instead of two and one background updater instead of zero.
The exact duration is a tunable default (naming convention consistent
with `anthropic_timeout_seconds`, `max_pdf_pages` in `Settings`) — not
fixed by this ADR, matching
[0023](0023-retry-and-backoff-policy-for-processing-jobs.md)'s existing
stance that specific numbers are operational tuning, not architecture.

### 2. Detection is a polling responsibility of the existing worker loop — no new service

The same loop that claims work (`modules/processing/worker.py`) is where
stale-job detection belongs, on the same poll cycle, rather than a
separate cron/sweeper process. This follows
[0021](0021-postgres-backed-job-queue.md)'s standing principle directly:
no new service is justified until a concrete requirement Postgres-plus-
polling can't satisfy actually appears, and periodically scanning
`WHERE status = 'running' AND updated_at < cutoff` is exactly the kind of
query that requirement would need to beat. This ADR fixes *that it's the
worker's job*; the implementation (a scan interspersed with claim
attempts, its own poll cadence) is future work.

### 3. Reclaiming a stale job reuses the existing `running -> retrying` edge — this ADR does not reopen the ADR-0020 graph

A stale job is moved `running -> retrying`, exactly the edge
[0020](0020-document-and-job-state-machines.md) already legalized for an
ordinary transient failure. No new job state and no new transition (e.g.
a direct `running -> queued`) is introduced. Reusing the existing edge
means a reclaimed job re-enters the exact same `retrying -> running` path
(backoff, then a future worker claims it again) that
[0023](0023-retry-and-backoff-policy-for-processing-jobs.md) already
defined — recovery from a crash and recovery from a rate limit are the
same transition as far as everything downstream is concerned.

### 4. Reclaiming a stale job consumes a retry attempt, using ADR-0023's existing budget accounting — it is not a new classification

A stale-job reclaim is **not** added to
[0023](0023-retry-and-backoff-policy-for-processing-jobs.md)'s transient/
terminal/not-a-failure split as a fourth entry — that split classifies
outcomes a job's own execution reaches, and a stale claim isn't one
(execution reached no conclusion at all). It is, however, **metered** by
the same retry budget already governing the `running -> retrying` edge:
reclaiming a stale job counts as consuming one of the job's existing
retry attempts, for the same reason
[0023](0023-retry-and-backoff-policy-for-processing-jobs.md) bounds
ordinary transient retries — an unbounded reclaim loop against a job that
reliably kills its worker (e.g. an input that reproducibly OOMs the
process) would otherwise retry forever. Once the budget is exhausted, a
stale-job reclaim follows the same existing exhausted-budget path:
`running -> failed` (via `retrying`), document `-> failed`, per
[0020](0020-document-and-job-state-machines.md) and
[0023](0023-retry-and-backoff-policy-for-processing-jobs.md). No new
concept is introduced here — this is the existing budget mechanism
applied to an additional trigger for the same transition, not a new
category of failure.

### 5. The reclaim-vs-still-alive race requires the eventual completion write to be conditional, not just the claim

A job can be reclaimed while its original worker is not actually dead —
only slow, close to the timeout, about to finish. If the detector moves
the job to `retrying` (and it's later reclaimed and re-run by a second
worker) while the *first* worker is still executing and eventually tries
to write its own outcome (`running -> completed` or `running -> failed`),
two workers now believe they own the same job's terminal write.
[0021](0021-postgres-backed-job-queue.md)'s claim is already atomic on the
way **in** (`SKIP LOCKED`); this ADR requires the same discipline on the
way **out**: **a job's terminal/retrying write must be a conditional
update** (`UPDATE ... WHERE id = :id AND status = 'running'`, or an
equivalent optimistic check against the row a worker actually claimed),
never an unconditional write of "whatever I currently think the job's
state should be." A worker that loses this race (its conditional update
matches zero rows because the job already moved) must treat its own
result as discarded, not retry the write or raise — its work was
superseded, not wrong. This is a requirement on the future increment that
implements job-outcome writes, not something this ADR implements itself;
it is recorded here because the race is a direct structural consequence
of introducing reclamation, and would silently reappear if a future
contributor implemented outcome-writing without having read this.

## Consequences

- **No schema change.** `updated_at` already exists on `Job` (Increment 1)
  and is already touched by the claim's own update (Increment 2) —
  staleness detection is a query against data that already exists, not a
  new column or migration. Consistent with the Sprint 3 baseline's
  observability principle (section 9): this becomes a queryable fact of
  the existing model, not a new tracked concept.
- **Neither ADR-0020's transition graph nor ADR-0023's failure
  classification is amended.** This was deliberate (sections 3 and 4):
  reclamation reuses the `running -> retrying` edge and the existing
  retry budget exactly as already defined, rather than introducing a new
  state or a new failure category. Every downstream consumer of the job
  lifecycle (retry budget, backoff, observability) already knows how to
  handle a reclaimed job without being taught anything new.
- **The existing retry budget is now consumed by two different
  situations** — an ordinary transient execution failure, and recovery
  from a stale claim — without either ADR-0023's taxonomy or the job
  model needing to structurally distinguish them. A future observability
  pass (baseline section 9) could add a reason code to tell them apart if
  that distinction ever becomes operationally useful — this ADR does not
  require it.
- **Introduces a real distributed-ownership race** (section 5) that a
  naive implementation would miss entirely — a worker that assumes it
  still owns a job it claimed a while ago is exactly the bug this ADR
  exists to prevent. Flagged explicitly, with the specific mitigation
  (conditional/fenced writes), so the future increment that writes job
  outcomes is built against this requirement rather than discovering the
  race in production.
- **No implementation yet.** This ADR fixes the recovery semantics — what
  "stale" means, whose job detection is, which existing transition
  reclamation uses, whether it costs a retry attempt, and the ownership
  race it introduces. Detection logic in the worker loop, the `Settings`
  field for the timeout, and the conditional-write requirement on
  outcome-writing are implementation work for a future increment, held to
  this contract once built.
- **Deliberately out of scope:** a heartbeat/lease mechanism with its own
  liveness column (rejected in section 1, with a named revisit trigger —
  if jobs ever need genuine mid-execution progress reporting, not just a
  binary alive/dead signal); a dedicated sweeper service or cron process
  (rejected in section 2, same reasoning as
  [0021](0021-postgres-backed-job-queue.md)); pinning the actual timeout
  duration, retry-budget interaction numbers, or poll cadence (explicitly
  deferred to tunable `Settings` defaults, per this project's existing
  convention); any expansion of ADR-0023's failure taxonomy (explicitly
  rejected in section 4 — this ADR is recovery semantics for an existing
  state, not a new classification).
