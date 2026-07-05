"""ADR-0037: the committed retrieval eval dataset through the real
fastembed model. Skips when the local model cache is absent -- CI's
pytest job runs on the bare runner with no ./.fastembed_cache (the baked
cache exists only inside the docker image), so this never forces a
~130 MB download in CI; locally it self-enables after the first
`make eval-retrieval`.

Unlike test_eval_harness_live.py (structure-only, because a remote LLM's
behavior can drift), this asserts a quality floor: the embedding model is
a pinned local artifact and the dataset is committed, so the score is
deterministic. Measured baseline at authoring time: recall@5 = 1.00,
MRR = 1.00. The floor is set well below baseline so reasonable dataset
edits don't flake, while a genuine retrieval regression still fails.
"""

from pathlib import Path

import pytest

from modules.evaluation.dataset import load_retrieval_corpus, load_retrieval_queries
from modules.evaluation.service import build_retrieval_report
from modules.retrieval.mock import InMemoryVectorStore
from modules.retrieval.service import RetrievalService
from shared.config.settings import get_settings

_settings = get_settings()

pytestmark = pytest.mark.skipif(
    not Path(_settings.embedding_model_cache_dir).exists(),
    reason="fastembed model cache not present (run `make eval-retrieval` once to populate it)",
)


def test_committed_dataset_meets_quality_floor_on_real_embeddings() -> None:
    from modules.retrieval.fastembed_embeddings import FastEmbedEmbeddingPipeline

    corpus_path = Path("eval/dataset/retrieval_corpus.jsonl")
    queries_path = Path("eval/dataset/retrieval_queries.jsonl")
    corpus_docs = load_retrieval_corpus(corpus_path)
    queries = load_retrieval_queries(
        queries_path, corpus_doc_ids={doc.doc_id for doc in corpus_docs}
    )

    retrieval_service = RetrievalService(
        embedding_pipeline=FastEmbedEmbeddingPipeline(
            model_name=_settings.embedding_model_name,
            cache_dir=_settings.embedding_model_cache_dir,
        ),
        vector_store=InMemoryVectorStore(),
        chunk_size_chars=_settings.retrieval_chunk_size_chars,
        overlap_chars=_settings.retrieval_chunk_overlap_chars,
    )

    report = build_retrieval_report(
        pipeline_name=f"fastembed:{_settings.embedding_model_name}",
        corpus_path=corpus_path,
        queries_path=queries_path,
        corpus_docs=corpus_docs,
        queries=queries,
        retrieval_service=retrieval_service,
        retrieve_top_k_chunks=_settings.retrieval_max_top_k,
    )

    assert report.corpus_doc_count == len(corpus_docs)
    # the two deliberately long docs must actually split into 2+ chunks
    assert report.chunk_count > report.corpus_doc_count
    assert report.metrics.scored_query_count == len([q for q in queries if q.relevant_doc_ids])

    assert report.metrics.recall_at_5 >= 0.9
    assert report.metrics.mrr >= 0.8
