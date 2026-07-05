# 0028: Audit trail read API

- **Status:** Accepted
- **Date:** 2026-07-04
- **Follows up on:** `docs/architecture/sprint-4-design-baseline.md`
  (section 3.2), [0027](0027-audit-log-schema-and-redaction-policy.md)
  (the schema this ADR exposes — explicitly deferred its own read API:
  "additive to this schema, not a redesign of it").

## Context

`AuditLogEntry` has existed since ADR-0027, durably recording who
uploaded a document or enqueued a job. Nothing today can read it back
except a direct database client. This ADR resolves the one route ADR-0027
named as future work, and the one question it left genuinely open: who
is allowed to see whose audit history.

## Decision

### 1. One route, not two

`GET /audit`, with query-parameter filters — not a separate
`GET /documents/{id}/audit` alongside it. A document-scoped view is just
`GET /audit?document_id=...`; a second, nested route would answer the
exact same question through a different shape for no benefit, and this
project has already established (`GET /documents`) that a flat,
filterable list endpoint is the right pattern for "browse a collection."

Filters, each optional and combinable: `caller`, `action`
(`document_uploaded` / `job_enqueued`), `document_id`, `job_id`. All four
map directly to `AuditLogEntry` columns already indexed by ADR-0027 — no
new index, no new query path.

Pagination reuses the existing `items`/`total`/`limit`/`offset` envelope
(`DocumentListOut`, ADR-0005) rather than inventing a second convention.
Ordered newest-first (`created_at` descending), matching
`GET /documents`'s own ordering.

### 2. Visibility: every valid caller sees every entry

Any request authenticated via `require_api_key` can query the full audit
log, filtered however it likes (including filtering *to* a specific
caller — "show me what alice did" is a valid, supported query, not a
restriction on who can ask it). There is no "you can only see your own
entries" restriction.

This is not a new decision so much as consistency with one already made:
ADR-0026 established that every named API key has **identical access** to
every document — any caller can already list, view, or process any
document regardless of who uploaded it. Restricting audit visibility to
"your own actions only" would introduce the first per-caller access
boundary this project has ever had, for no stated reason, while every
other resource stays flat. If a real need for that boundary appears, it's
an RBAC decision (explicitly out of scope per the Sprint 3 and Sprint 4
baselines) — not something to smuggle in one endpoint at a time.

### 3. No ASGI pre-body-read gate needed for this route

`ApiKeyGateMiddleware` (ADR-0017) exists specifically to reject
unauthenticated requests *before* a request body is read off the wire —
it matters for `POST /documents` because an unauthenticated multipart
upload would otherwise be fully received before FastAPI's own dependency
resolution gets a chance to reject it. `GET /audit` has no body to
protect against; the ordinary `require_api_key` route dependency (which
already gates `/documents*`'s `GET` routes the same way) is sufficient.
`protected_prefix` is not extended to `/audit`; the router-level
dependency is.

## Consequences

- **New router, `apps/api/routers/audit.py`**, mounted alongside
  `documents_router` in `apps/api/main.py`. No schema change —
  `AuditLogEntry` (ADR-0027) is read-only from this route's perspective.
- **`AuditLogEntryOut` schema** mirrors `AuditLogEntry`'s five columns
  directly (`id`, `caller`, `action`, `document_id`, `job_id`,
  `created_at`) — no additional fields, consistent with ADR-0027's
  redaction policy: this route can't leak anything the table itself
  doesn't already structurally exclude.
- **Global visibility is a deliberate, named choice**, not an oversight —
  a future contributor who wants to restrict it should read this section
  first, since it's consistent with an existing, already-approved
  precedent (ADR-0026), not an accident.
