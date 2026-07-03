# 0001: Modular monolith over microservices

- **Status:** Accepted
- **Date:** 2026-07-03

## Context

The repository scaffold pre-defined `modules/{ingestion,ocr,extraction,
validation,layout,indexing,search,auth,audit,analytics}` as sibling
directories within a single codebase — not as separate deployable services.
Sprint 1 needed a working local MVP fast, without weakening the architecture
for future growth (real OCR, RAG, PHI detection, LLM extraction).

## Decision

Build one deployable FastAPI application. Domain logic is separated by
**module boundary and interface**, not by process or network boundary. Each
module (`ingestion`, `ocr`, `validation`) owns its own ORM models, Pydantic
schemas, and — where a future swap is expected — an abstract interface with
one concrete implementation. `apps/api` is the only layer permitted to
compose across modules; no module imports another module's internals.

## Consequences

- Local development stays trivial: `docker compose up` runs two containers
  total (api, postgres).
- No distributed-systems tax yet — no service mesh, no inter-service auth,
  no network-partition handling, no cross-service transaction concerns.
- The cost is deferred, not avoided: if/when OCR or extraction genuinely
  need independent scaling (e.g. GPU-bound real OCR vs. lightweight CRUD),
  extracting a module into its own service will require a real decomposition
  plan. The interface boundaries chosen in
  [0002](0002-interface-first-pipeline-stages.md) are what make that
  extraction plausible later without a rewrite.
