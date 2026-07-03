# 0005: Paginated response envelope, breaking change accepted early

- **Status:** Accepted
- **Date:** 2026-07-03

## Context

`GET /documents` originally returned every row as a bare JSON array —
correct for a demo with a handful of documents, a real scalability problem
once the registry grows. Fixing it means changing the response shape of an
already-shipped, already-tested endpoint.

## Decision

Changed `GET /documents` directly, on the existing endpoint, to accept
`limit` (default 20, max 100) and `offset` (default 0) query parameters
(FastAPI-validated, `422` outside range) and return
`{items, total, limit, offset}` instead of a bare array. No versioned
endpoint, no parallel "v2" route, no deprecation window.

## Consequences

- Callers of the old bare-array shape break immediately, with no migration
  path — acceptable because Sprint 1 has no external consumers of this API
  yet. This was judged to be the cheapest possible moment to make this
  change: the alternative was making it later, after something depended on
  the old shape, at which point it becomes a real versioned-API problem.
- `total` in the response requires a `COUNT(*)` query alongside the paged
  `SELECT` on every list call — an accepted, small, constant overhead.
- Ordering is fixed (`created_at DESC`, newest first) and undocumented as a
  configurable sort — a deliberate scope limit, not an oversight.
