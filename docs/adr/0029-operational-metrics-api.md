# 0029: Operational metrics API

- **Status:** Accepted
- **Date:** 2026-07-04
- **Follows up on:** `docs/architecture/sprint-4-design-baseline.md`
  (section 3.3), and section 2's finding that
  `modules/processing/metrics.py`'s `WorkerMetrics` cannot back this route
  (process-local, not durable — see that section for why).

## Context

Today, answering "how many jobs are queued," "what's our failure rate," or
"how confident is our extraction on average" requires a raw SQL query
against the running database. `Job`, `Document`, and `ExtractionResult`
already durably hold everything needed to answer those questions —
nothing new needs to be stored, only read and aggregated.

## Decision

### 1. One route, not several

`GET /metrics`, returning one composite payload — not
`/metrics/jobs`, `/metrics/documents`, `/metrics/confidence` as separate
routes. This mirrors ADR-0028's own reasoning for `/audit`: a caller
asking "what's the state of the system" wants one answer, not three
requests stitched together client-side, and there is no filtering/
pagination axis here (unlike `/audit` or `/documents`) that would justify
a collection-style route.

Response shape:

```json
{
  "jobs": {
    "by_status": {"queued": 0, "running": 0, "retrying": 0, "completed": 0, "failed": 0, "cancelled": 0},
    "avg_retry_count": 0.0,
    "max_retry_count": 0
  },
  "documents": {
    "by_status": {"uploaded": 0, "processing": 0, "extracted": 0, "validated": 0, "failed": 0}
  },
  "confidence": {
    "count": 0,
    "min": null,
    "avg": null,
    "max": null
  }
}
```

`by_status` always reports every enum member, defaulting to `0` for a
status with no rows — a caller should not have to know a status is
absent from a `GROUP BY` result to treat it as zero. `confidence`'s three
stats are `null` (not `0`) when no `ExtractionResult` rows exist yet,
since `0.0` would misrepresent "no data" as "confirmed zero confidence."

### 2. Aggregation happens in SQL, not Python

Every count and statistic (`COUNT`, `AVG`, `MAX`, grouped `COUNT`) is
computed by the database via `func.count`/`func.avg`/`func.max` with
`GROUP BY`, not by fetching rows and reducing them in Python. This is the
same pattern `modules/audit/service.list_entries` and
`modules/ingestion/service.list_documents` already use for `total`
counts — consistent with this project's existing query style, and it
means the payload's cost stays proportional to the number of distinct
statuses/aggregates returned (a handful of rows), not to the number of
jobs/documents/extractions that exist.

**Per-stage duration is still out of scope**, per the Sprint 4 baseline's
section 2 — it was never durably persisted, and adding storage for it is
new scope this ADR does not take on.

**Document throughput is reported as current counts by status, not
windowed by `created_at`.** The baseline named windowing as optional
("count of documents by status, optionally windowed by `created_at`");
this ADR chooses the simpler of the two for the same reason ADR-0028
chose one route over two — no concrete need for a time window has been
stated yet, and a `since`/`until` filter is a straightforward additive
change to `GET /metrics` later if one appears.

### 3. Visibility: global, same as `/audit`

`GET /metrics` sits behind the ordinary `require_api_key` router
dependency, with no per-caller scoping — identical reasoning to
ADR-0028 section 2 (consistency with ADR-0026's flat, equal-access
model across all named keys). Operational metrics describe the system as
a whole, not any one caller's activity, so there isn't even a plausible
per-caller partition here the way there arguably is for audit history.

## Consequences

- **New module `modules/analytics/`** (an already-present, previously
  empty placeholder directory) holds `service.py` (the three aggregation
  queries) and `schemas.py` (`JobMetricsOut`, `DocumentMetricsOut`,
  `ConfidenceMetricsOut`, `MetricsOut`). Unlike audit/ingestion/ocr, this
  module owns no table of its own — it only reads across `Job`,
  `Document`, and `ExtractionResult`.
- **New router, `apps/api/routers/metrics.py`**, mounted alongside
  `documents_router` and `audit_router` in `apps/api/main.py`. No ASGI
  gate change: `GET /metrics` has no body, same reasoning as ADR-0028
  section 3.
- No schema/migration change: this ADR is a read-only aggregation over
  existing tables.
