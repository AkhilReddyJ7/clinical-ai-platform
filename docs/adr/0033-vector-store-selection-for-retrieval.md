# 0033: Vector store selection for retrieval

- **Status:** Accepted
- **Date:** 2026-07-05
- **Follows up on:** `docs/architecture/idp-platform-pivot-baseline.md`
  (Phase C, which explicitly named this a real decision, not a default).

## Context

Phase C needs somewhere to store chunk embeddings and query them by
similarity. Every prior infrastructure decision in this project (the
job queue, ADR-0021; now the evaluation harness and lineage/reprocess
work) reused Postgres rather than adding a new service. Retrieval is the
first place that pattern is deliberately broken.

## Decision

**Chroma**, run as its own new `docker-compose.yml` service — not
`pgvector` (Postgres's own vector extension, which would have continued
the no-new-infrastructure pattern). Chosen because a dedicated vector
store is more RAG-idiomatic and worth exercising directly for what this
project is (see `docs/architecture/idp-platform-pivot-baseline.md`'s own
framing) — this is a deliberate exception to the "reuse Postgres"
default, not an oversight; `pgvector` remains a reasonable alternative
if this project's constraints ever change (e.g. wanting one fewer moving
part in a resource-constrained deployment).

**Connected via the lightweight `chromadb-client` package** (HTTP-only
client), not the full `chromadb` package (which bundles the server
itself) — api/worker only ever talk to Chroma over HTTP as a separate
service, so there's no reason to pull server-side dependencies into
either image.

**Image**: `chromadb/chroma:1.5.3`, pinned exactly, matching
`postgres:16-alpine`'s own precedent of not floating on `:latest`.

**Healthcheck**: confirmed directly that this image is a slim
distribution with no curl/wget/python3 inside it — a bash `/dev/tcp`
redirection (`test: ["CMD", "bash", "-c", "</dev/tcp/localhost/8000"]`)
is the one mechanism that works without those tools, confirmed by
running it against the container directly before committing to it.

## Consequences

- New `docker-compose.yml` service `chroma` + new `chroma_data` volume —
  the first genuinely new infrastructure component in this project.
- `api` and `worker` both gain a dependency on `chroma` being healthy
  before starting, alongside their existing dependency on `migrate`.
- New runtime dependency `chromadb-client`; mypy gains an
  `ignore_missing_imports` override for `chromadb`/`chromadb.*` even
  though the package ships real (if occasionally too-wide) type hints —
  see ADR-0034 for the embeddings-side dependency, `fastembed`.
- Collections are created with `metadata={"hnsw:space": "cosine"}`
  explicitly — Chroma's own default is squared L2, which doesn't map
  cleanly onto a "higher score is more similar" API response.
