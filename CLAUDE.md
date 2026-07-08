# Clinical AI Intelligence Platform

IDP pipeline for clinical documents: upload → Tesseract OCR → PHI gate → Anthropic tool-forced field extraction → validation, on a durable Postgres state machine with an async worker, plus RAG (fastembed local embeddings → Chroma) with grounded, citation-backed answering and a first-class abstention path. Repo: AkhilReddyJ7/clinical-ai-platform. Python 3.12+, uv-managed.

## Commands (via Makefile — prefer these over raw invocations)
- `make up` / `make down` — docker compose stack (Postgres, Chroma, API, worker)
- `make test` — `uv run pytest`
- `make lint` / `make fmt` — ruff (+ black on fmt)
- `make migrate` / `make revision m="msg"` — alembic
- `make eval` / `make eval-retrieval` — evaluation harness (extraction/PHI, retrieval recall@k + MRR); retrieval eval runs the real local embedding model at zero API cost
- `make backfill` / `make reindex` — corpus maintenance scripts

## Hard rules
- **Never use real patient data anywhere** — tests, fixtures, demos, examples. Synthetic only. The project is explicitly not HIPAA-compliant.
- **PHI safety is enforced at the point of indexing**, not downstream — don't add code paths that index chunks before the PHI gate.
- The grounded-answer endpoint cites opaque passage numbers; the model must never see document ids it could hallucinate (ADR-0038). Preserve this indirection.
- The LlamaIndex implementation is a deliberately isolated demonstration — keep it out of the production path.

## Conventions
- **Every non-trivial architectural decision gets an ADR** in `docs/` (38 exist). Before changing architecture, check for an ADR covering it; when making a new decision, write one stating problem, choice, tradeoff. Several ADRs document what was deliberately *not* built — respect those unless asked to revisit.
- Layout: `apps/` (entrypoints), `modules/` (domain logic), `shared/`, `eval/`, `scripts/`, `tests/`, `alembic/` migrations.
- LLM calls go through the Anthropic API with tool-forced structured output; embeddings are local fastembed (no paid embeddings API).
