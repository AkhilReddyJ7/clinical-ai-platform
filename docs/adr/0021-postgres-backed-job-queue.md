# 0021: Postgres-backed job queue, not Redis/Celery

- **Status:** Accepted
- **Date:** 2026-07-04
- **Follows up on:** `docs/architecture/sprint-3-design-baseline.md`
  (section 7a), [0020](0020-document-and-job-state-machines.md) (defines
  the job states this queue will store and transition).

## Context

Sprint 3's processing pipeline needs a durable queue: a way for a
processing request to be recorded and survive a crash or restart before a
worker claims and executes it (baseline section 2, "Queue" stage). Two
realistic options exist: a Postgres-backed queue (a jobs table, claimed
via `SELECT ... FOR UPDATE SKIP LOCKED`-style polling, no new service) or
a Redis-backed queue via an existing library (RQ, Celery, or arq).

This project has a consistent, evidence-based track record of choosing
the dependency-minimal option when it clears the bar rather than the more
commonly-reached-for one: local Tesseract over cloud OCR vendors
([0010](0010-real-local-ocr-via-tesseract-fields-stay-synthetic.md)),
regex-based PHI detection over Presidio after directly measuring
Presidio's cost ([0018](0018-evaluated-presidio-not-adopting-yet.md)), and
a single-provider LLM implementation over a provider-agnostic tree
([0019](0019-anthropic-field-extraction-and-phi-gates-llm-call.md)). The
queue decision was held to the same standard rather than defaulting to
"what's popular for job queues in general."

## Decision

**Use a Postgres-backed queue.** Compared on the dimensions that actually
matter for this project, not popularity:

| Dimension | Postgres-backed queue | Redis-backed (RQ/Celery/arq) |
|---|---|---|
| Operational simplicity | No new service; reuses the database already running, already backed up, already part of this stack's healthcheck story | A new stateful service to run, secure, and keep healthy alongside Postgres |
| Deployment complexity | No change to `docker-compose.yml` topology | New service definition, new health check, new dependency ordering in compose and in CI's `docker` job |
| Testing | Consistent with the existing SQLite-for-tests / Postgres-for-runtime split ([0004](0004-sqlite-for-tests-postgres-for-runtime.md)) — queue behavior is exercised the same way as everything else that touches the database | Requires an in-memory/fake Redis for the fast test suite, or excludes queue behavior from it entirely — a new testing pattern this project doesn't currently have anywhere |
| Maintainability | One less technology for a solo/small-team maintainer to patch, upgrade, and reason about | Mature, well-documented tooling, but a genuinely separate operational and failure-mode surface |

Redis/Celery-class tooling brings real advantages at higher throughput or
with a distributed worker fleet — neither of which this project has or
has near-term evidence of needing. Choosing it now would be solving for a
scale problem that doesn't exist yet, at the cost of infrastructure this
project would then own indefinitely.

## Consequences

- **No new service in `docker-compose.yml`.** The queue lives in Postgres,
  the database already running for `documents`/`extraction_results`/
  `validation_results`. This preserves the current two-service topology
  (`api` + `postgres`) exactly.
- **Job durability inherits Postgres's existing guarantees** — the same
  ACID/durability story the rest of this project's data already relies
  on, with no separate persistence/backup story to design for a broker.
- **Polling has its own correctness concerns that still need real design
  attention** — lock contention under concurrent workers, poll interval
  tuning (latency vs. database load), and correctly implementing
  `SKIP LOCKED`-style claiming so two workers never claim the same job.
  Choosing Postgres does not make these free; it only avoids a *second*
  set of operational concerns (a broker's own failure modes) on top of
  them. These are implementation-phase concerns for the worker epic, not
  resolved by this ADR.
- **Revisit trigger, named explicitly so this isn't re-litigated without
  cause:** move to Redis/Celery-class tooling only if a concrete,
  measured requirement emerges that Postgres genuinely can't satisfy at
  this project's actual scale (e.g., a demonstrated throughput ceiling on
  polling-based claiming) — not preemptively, and not because Redis is
  more commonly used for this purpose elsewhere.
- Sets up the worker epic and the retry/backoff ADR to assume Postgres as
  the system of record for job state — both build directly on the job
  lifecycle defined in [0020](0020-document-and-job-state-machines.md),
  now with a fixed storage model to implement it against.
