"""ADR-0037: build_retrieval_report against InMemoryVectorStore with an
in-test fake embedding pipeline whose vectors are hand-chosen, so the
ranking -- and therefore every metric -- is fully controlled and asserted
exactly (stronger than the hash-based MockEmbeddingPipeline, whose
rankings are arbitrary).
"""

from pathlib import Path

import pytest

from modules.evaluation.schemas import RetrievalCorpusDoc, RetrievalQueryCase
from modules.evaluation.service import build_retrieval_report
from modules.retrieval.base import EmbeddingPipeline
from modules.retrieval.mock import InMemoryVectorStore, MockEmbeddingPipeline
from modules.retrieval.service import RetrievalService


class _AxisEmbeddingPipeline(EmbeddingPipeline):
    """Maps each text to a fixed near-one-hot vector by keyword, so cosine
    ranking is deterministic and readable in the test itself.
    """

    _AXES = {"alpha": 0, "beta": 1, "gamma": 2}

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.01] * 4  # small floor keeps cosine defined for unmatched text
            for keyword, axis in self._AXES.items():
                if keyword in text:
                    vector[axis] = 1.0
            vectors.append(vector)
        return vectors


def _service(pipeline: EmbeddingPipeline) -> RetrievalService:
    return RetrievalService(
        embedding_pipeline=pipeline,
        vector_store=InMemoryVectorStore(),
        chunk_size_chars=50,  # small on purpose: makes doc-alpha span multiple chunks
        overlap_chars=10,
    )


CORPUS = [
    # long enough (with chunk_size_chars=50) to split into several chunks,
    # all containing "alpha" -- exercises document-level dedup
    RetrievalCorpusDoc(doc_id="doc-alpha", text="alpha topic " * 12),
    RetrievalCorpusDoc(doc_id="doc-beta", text="a short note about the beta topic"),
    RetrievalCorpusDoc(doc_id="doc-gamma", text="a short note about the gamma topic"),
]


def test_build_retrieval_report_exact_metrics_and_dedup() -> None:
    queries = [
        RetrievalQueryCase(query_id="q-alpha", query_text="alpha", relevant_doc_ids=["doc-alpha"]),
        RetrievalQueryCase(query_id="q-beta", query_text="beta", relevant_doc_ids=["doc-beta"]),
        RetrievalQueryCase(
            query_id="q-multi",
            query_text="alpha beta",
            relevant_doc_ids=["doc-alpha", "doc-beta"],
        ),
        RetrievalQueryCase(query_id="q-none", query_text="delta", relevant_doc_ids=[]),
    ]

    report = build_retrieval_report(
        pipeline_name="axis-fake",
        corpus_path=Path("inline-corpus"),
        queries_path=Path("inline-queries"),
        corpus_docs=CORPUS,
        queries=queries,
        retrieval_service=_service(_AxisEmbeddingPipeline()),
    )

    assert report.corpus_doc_count == 3
    # doc-alpha split into multiple chunks under the tiny chunk size
    assert report.chunk_count > 3
    assert report.query_count == 4

    by_id = {r.query_id: r for r in report.queries}
    assert set(by_id) == {"q-alpha", "q-beta", "q-multi"}

    # despite doc-alpha occupying several chunk slots, it appears once
    assert by_id["q-alpha"].ranked_doc_ids.count("doc-alpha") == 1
    assert by_id["q-alpha"].ranked_doc_ids[0] == "doc-alpha"
    assert by_id["q-alpha"].reciprocal_rank == 1.0

    assert by_id["q-beta"].ranked_doc_ids[0] == "doc-beta"

    # both relevant docs retrieved -> full recall@5; top-1 covers only one
    assert by_id["q-multi"].recall_at_5 == 1.0
    assert by_id["q-multi"].recall_at_1 == 0.5
    assert by_id["q-multi"].hit_at_1 is True

    assert report.metrics.scored_query_count == 3
    assert report.metrics.hit_rate_at_5 == 1.0

    # the no-answer query is reported separately, never aggregated
    assert [r.query_id for r in report.no_answer_queries] == ["q-none"]
    assert report.no_answer_queries[0].top_score is not None


def test_build_retrieval_report_is_deterministic_with_mock_pipeline() -> None:
    queries = [
        RetrievalQueryCase(query_id="q-1", query_text="anything", relevant_doc_ids=["doc-alpha"]),
    ]

    def run() -> str:
        report = build_retrieval_report(
            pipeline_name="mock",
            corpus_path=Path("inline-corpus"),
            queries_path=Path("inline-queries"),
            corpus_docs=CORPUS,
            queries=queries,
            retrieval_service=_service(MockEmbeddingPipeline()),
        )
        return report.model_dump_json()

    assert run() == run()


def test_build_retrieval_report_empty_queries() -> None:
    report = build_retrieval_report(
        pipeline_name="axis-fake",
        corpus_path=Path("inline-corpus"),
        queries_path=Path("inline-queries"),
        corpus_docs=CORPUS,
        queries=[],
        retrieval_service=_service(_AxisEmbeddingPipeline()),
    )
    assert report.metrics.scored_query_count == 0
    assert report.metrics.mrr == pytest.approx(0.0)
