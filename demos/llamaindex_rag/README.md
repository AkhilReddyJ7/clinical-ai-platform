# LlamaIndex RAG Demo (Phase D)

Phase D of `docs/architecture/idp-platform-pivot-baseline.md`: an
orchestration-framework literacy demo, not production code.

## What this is

The same retrieval task Phase C solves (`modules/retrieval/`) —
chunk → embed → index → retrieve over the same synthetic clinical
notes (`eval/dataset/cases.jsonl`) — reimplemented with
[LlamaIndex](https://docs.llamaindex.ai/) instead of this project's own
hand-rolled pipeline.

## Why it's kept separate

ADR-0019 already made a deliberate, justified choice: raw Anthropic SDK
+ tool-calling for field extraction, not an orchestration framework. That
choice stands for the production path. This demo exists to show the
other approach is understood too — worth knowing directly, not worth
adopting as a dependency of the core pipeline.

Concretely, that separation is enforced, not just stated:

- **Its own `uv` dependency group** (`demo` in `pyproject.toml`) — `uv
  sync` alone does not install `llama-index-core` or its transitive
  dependencies (`boto3`, `cryptography`, `nltk`, and others); only `uv
  sync --group demo` does. Nothing in this group reaches the production
  Docker image.
- **Not covered by `mypy`/`pytest`** — deliberately excluded from this
  project's validation cycle (`mypy apps modules shared tests alembic
  scripts` and the test suite both stop short of `demos/`). This is a
  demo script, evaluated by running it, not a maintained module.
- **No changes to `modules/retrieval/`, `apps/api/`, or any production
  dependency** — this directory only *reads* `eval/dataset/cases.jsonl`
  and `shared.config.settings` (to reuse the same `ANTHROPIC_API_KEY`/
  `embedding_model_name` config, not to introduce new settings).

## What's identical vs. different, vs. Phase C

| | Phase C (`modules/retrieval/`) | This demo |
|---|---|---|
| Embedding model | `fastembed`, `BAAI/bge-small-en-v1.5` | Same model, via `llama-index-embeddings-fastembed` |
| Chunking | Custom `chunk_text()`, explicit chunk IDs | LlamaIndex's own, framework-managed |
| Vector store | Chroma (its own docker-compose service) | LlamaIndex's in-memory default — no infra dependency, so this demo runs standalone |
| Retrieval | `RetrievalService.query()`, explicit interface | `index.as_retriever().retrieve(...)` |
| Generation | Not combined with retrieval in Phase C at all (Phase C is retrieval-only) | LlamaIndex's `as_query_engine()` combines retrieval + an LLM call in one abstraction, via `llama-index-llms-anthropic` reusing the same `ANTHROPIC_API_KEY` |

The point of comparison is the **orchestration layer** — chunking,
indexing, retrieval, and (optionally) query synthesis as a framework
manages them versus as this project's own interfaces
(`modules/retrieval/base.py`) express them explicitly.

## Running it

```bash
uv sync --group demo
uv run python -m demos.llamaindex_rag.run_demo
```

Retrieval runs with no API key required. If `ANTHROPIC_API_KEY` is set
(`.env`), it also runs a full query-engine pass (retrieval + a real
Anthropic call) and prints a synthesized answer; otherwise that part is
skipped with a clear message, matching this project's existing
fail-closed-with-a-clear-message posture (ADR-0019).
