# 0037: Retrieval-quality evaluation (recall@k, MRR)

- **Status:** Accepted
- **Date:** 2026-07-05
- **Follows up on:** [0030](0030-evaluation-harness.md) (the harness this
  extends — its Context explicitly anticipated "so that Phase C (RAG)
  later has something to evaluate retrieval quality against"),
  [0033](0033-vector-store-selection-for-retrieval.md)/[0034](0034-chunking-and-embeddings-strategy-for-clinical-documents.md)/[0035](0035-retrieval-api-shape-and-phi-safety-at-the-retrieval-boundary.md)
  (the retrieval stack being measured),
  [0036](0036-guardrails-hardening.md) (the reported-but-not-gated
  precedent reused here for no-answer queries).

## Context

The retrieval stack (ADR-0033–0035) ships with zero quality measurement:
"retrieval works" is demonstrated by a smoke test that round-trips one
document, not by any number that would move if ranking quality degraded.
Extraction and PHI detection already have measured, regression-tracked
scores (ADR-0030/0036); retrieval is the only LLM-adjacent surface
without one. This ADR closes that gap with a labeled query→document
dataset and standard ranking metrics, wired through the existing harness
seams (`modules/evaluation/`), not a parallel ad hoc script.

## Decision

### 1. Relevance is labeled at the document level

Ground truth is `query → set of relevant doc_ids`
(`eval/dataset/retrieval_queries.jsonl`), never chunk-level. Chunk
boundaries are tunable settings (`retrieval_chunk_size_chars` /
`retrieval_chunk_overlap_chars`, ADR-0034) — chunk-level labels would be
silently invalidated by any re-chunking. The scorer converts the ranked
chunk list from `RetrievalService.query()` into a ranked document list by
order-preserving first-occurrence dedup (`dedupe_ranked` in
`modules/evaluation/scoring.py`): the list arrives score-sorted, so a
multi-chunk document collapses to its best chunk's position. Tie-breaking
between equal scores is owned by the vector store and deliberately not
re-adjudicated by the scorer.

### 2. A dedicated corpus, not `cases.jsonl` reuse

`eval/dataset/retrieval_corpus.jsonl`: 16 synthetic clinical notes with
deliberately distinct topics (heart failure, asthma, CKD, migraine, …).
Reusing the extraction dataset was rejected for two reasons: its 15
baseline cases are near-duplicate demographic templates (retrieval
discrimination over near-duplicates measures nothing), and coupling
would let a routine extraction-eval edit silently move retrieval scores.
The corpus includes two documents over 2 000 characters — they split
into multiple chunks under the production settings, so document dedup is
genuinely exercised, not just theoretically supported — and two
topic-adjacent pairs (diabetes, hypertension) to support multi-relevant
queries. Loaders cross-validate: duplicate `doc_id`s and query labels
referencing unknown `doc_id`s fail at load time, not as silent
always-zero recall.

### 3. Metrics: recall@1/5, hit_rate@1/5, MRR — recall@5 is the gate

Per query with relevant set R (|R| ≥ 1), over the deduped document
ranking built from the top `retrieval_max_top_k` (20) chunks — more
chunks than the k=5 document cutoff, because a multi-chunk document can
occupy several chunk slots:

- **recall@k** = |R ∩ top-k docs| / |R|
- **hit@k** = whether *any* relevant doc appears in the top k — kept
  alongside recall because a multi-relevant query caps recall@1 at
  1/|R|, which reads as failure when the top hit was actually right
- **reciprocal rank** = 1/rank of the first relevant doc; 0.0 if none
  retrieved at all; **MRR** = mean over scored queries

**precision@k was rejected**: with binary relevance and 1–2 relevant
docs per query it is arithmetically redundant with recall/hit at small
k. **nDCG was rejected**: it needs graded relevance labels this dataset
doesn't have. `--fail-under` gates **recall@5**, matching
`retrieval_default_top_k = 5` — "was the relevant document in what the
API actually returns by default". k=1 metrics are reported for
diagnosis, not gated.

### 4. The default mode runs the real embedding model

`scripts/run_retrieval_eval.py` (`make eval-retrieval`) defaults to the
real `FastEmbedEmbeddingPipeline` — the **inverse** of
`run_eval.py --live`, and deliberately so: fastembed is a pinned local
ONNX model (ADR-0034), so measuring real retrieval quality costs
nothing, whereas the extraction eval's real mode costs Anthropic API
calls. `--mock` (hash-derived vectors) exists only for plumbing checks;
mock rankings say nothing about quality. The vector store is
`InMemoryVectorStore` in both modes: it performs the same cosine math as
Chroma's configured space, corpus indexing goes through the production
`RetrievalService.index_extraction` path (same chunking, chunk-ID
scheme, delete-then-upsert), and Chroma wiring itself is already
verified by CI's docker-job smoke test. A `--chroma` mode would re-test
transport, not ranking — deferred.

Measured baseline at adoption (pinned `BAAI/bge-small-en-v1.5`, committed
dataset): **recall@5 = 1.00, MRR = 1.00, recall@1 = 0.92** (the two
multi-relevant queries cap recall@1 at 0.5 each). Because both model and
dataset are pinned artifacts, `tests/integration/`'s real-fastembed test
asserts a floor (recall@5 ≥ 0.9, MRR ≥ 0.8) rather than the
structure-only posture of `test_eval_harness_live.py` — a remote LLM can
drift, a pinned local model cannot. The test skips when the local model
cache is absent, which keeps CI's pytest job from downloading ~130 MB
(the baked cache exists only inside the docker image).

### 5. No-answer queries are informational, never gated

Two queries with `relevant_doc_ids: []` (out-of-domain questions) are
reported in a separate section with their top-1 cosine score and are
excluded from every aggregate denominator. Gating them would require an
abstention mechanism — a score threshold below which retrieval returns
nothing — which ADR-0035 deliberately did not build (single-stage cosine
ranking always returns top_k). Same reported-but-not-gated posture as
ADR-0036's adversarial cases. The observed gap at adoption (no-answer
top-1 scores 0.59–0.63 vs. ~0.7+ for genuine hits) is the data a future
abstention-threshold decision would start from.

## Deliberately not done

- **No LLM-judged or graded relevance** — binary labels on a synthetic
  corpus are sufficient for regression tracking, and an LLM judge would
  add cost and its own eval problem.
- **No reranking eval** — ADR-0035 §6 deferred reranking itself; this
  measures the single-stage ranking that exists.
- **No CI gate** — `--fail-under` is the mechanism, wiring it into CI
  remains a separate decision, per the ADR-0030 §5 precedent.
- **No `--chroma` mode** — see §4.

## Consequences

Retrieval quality is now a number (`make eval-retrieval`), not a claim.
The cost: the labeled dataset is small (16 docs, 14 queries) and easy —
the perfect baseline recall@5 means the gate currently detects only
regressions, not headroom. Growing the corpus with harder distractors
(closer topic neighbors, longer documents) is the natural next
increment, and per-query results in the JSON report
(`--report-out`) make label mistakes visible when it happens.
