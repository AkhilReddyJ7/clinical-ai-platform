# 0034: Chunking and embeddings strategy for clinical documents

- **Status:** Accepted
- **Date:** 2026-07-05
- **Follows up on:** `docs/architecture/idp-platform-pivot-baseline.md`
  (Phase C), [0033](0033-vector-store-selection-for-retrieval.md) (the
  vector store this feeds).

## Context

Turning `ExtractionResult.raw_text` into retrievable vectors requires
two decisions: how to split long text into chunks, and what produces the
embedding for each chunk.

## Decision

### 1. Embeddings: `fastembed` (local, ONNX-based), not Voyage AI

Anthropic doesn't offer its own embeddings model; Voyage AI is its
recommended paid partner (`voyage-3-large` leads MTEB, ~$0.18/M tokens).
This project chose **`fastembed`'s `BAAI/bge-small-en-v1.5`** (384-dim)
instead — a deliberate, explicit exception to "use what the target
ecosystem actually uses," for the same reason this project has
consistently chosen local/free over cloud/paid when it clears the bar:
Tesseract over cloud OCR, regex over Presidio, and now this. No new API
key, no per-call cost, no new "credentials not configured" failure mode
to add alongside the existing Anthropic one.

`fastembed` specifically (not `sentence-transformers`) because it's
ONNX-based with no `torch`/CUDA dependency — confirmed directly: a
`sentence-transformers`-based image runs roughly 5GB, `fastembed`'s
stays under 0.5GB, a real difference for this project's build/CI cost
that a heavier, marginally-more-accurate library doesn't justify at this
scale.

**The model is baked into the Docker image at build time**
(`infrastructure/docker/api.Dockerfile`), not downloaded at first
request — no runtime network dependency, no CI flakiness, same
discipline already applied to `tesseract-ocr`. `Settings.embedding_model_cache_dir`
must match the bake path exactly (`/opt/fastembed_cache` in-container —
outside `/app`, the same reason the venv lives in `/opt/venv`: a path
under `/app` would be shadowed by docker-compose's bind mount of the
host repo at runtime, discovered directly when a first CI run hit
`PermissionError: [Errno 13] Permission denied: '/app/.fastembed_cache'`)
or the bake is silently wasted and the container downloads the
model at runtime instead.

### 2. Chunking: character-based, no new tokenizer dependency

`chunk_text(text, *, chunk_size_chars=2000, overlap_chars=200)` — pure
Python, whitespace-snapped boundaries, no tokenizer library. Same
posture as `anthropic_max_input_chars` already bounding the LLM call by
character count, not tokens. Clinical notes here rarely approach that
12,000-character cap, so most documents produce a handful of chunks.

### 3. Chunk identity and the reprocessing correctness trap

Chunk IDs are `f"{extraction_id}:{chunk_index}"` — useful for debugging/
lineage. **The actual mechanism that keeps re-indexing and reprocessing
correct is not the ID scheme — it's deleting every existing chunk for a
`document_id` before upserting the new attempt's chunks**
(`RetrievalService.index_extraction`). Reprocessing (ADR-0032) creates a
new `extraction_id` for the same `document_id`; without the delete step,
the prior attempt's chunks would remain in the store forever,
indistinguishable from current ones in a query result. This was caught
during design, not discovered later — verified directly with a test
(`test_reprocessing_leaves_no_stale_chunks_from_the_prior_attempt`).

No explicit "index status" table in Postgres — Chroma's own per-vector
metadata (`document_id`, `extraction_id`, `chunk_index`) is the single
source of truth for what's indexed, avoiding the same redundant-mirror
anti-pattern ADR-0031 already rejected for a `supersedes_id` column.

## Consequences

- New runtime dependency `fastembed`; mypy gains an
  `ignore_missing_imports` override.
- `EmbeddingPipeline` (`modules/retrieval/base.py`) is the swappable
  interface — `FastEmbedEmbeddingPipeline` is the only implementation
  today, matching `FieldExtractionPipeline`'s own "seam for a future
  second implementation, not a provider tree built in advance" posture
  (ADR-0019).
- Settings gain `embedding_model_name`, `embedding_model_cache_dir`,
  `retrieval_chunk_size_chars`, `retrieval_chunk_overlap_chars`.
