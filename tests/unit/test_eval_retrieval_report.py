"""ADR-0037: text and JSON renderers for the retrieval eval report."""

from modules.evaluation.schemas import (
    RetrievalEvalReport,
    RetrievalMetrics,
    RetrievalQueryResult,
)
from modules.evaluation.report import render_retrieval_json, render_retrieval_text


def _report() -> RetrievalEvalReport:
    scored = RetrievalQueryResult(
        query_id="rq-1",
        ranked_doc_ids=["doc-a", "doc-b"],
        relevant_doc_ids=["doc-a"],
        reciprocal_rank=1.0,
        recall_at_1=1.0,
        recall_at_5=1.0,
        hit_at_1=True,
        hit_at_5=True,
        top_score=0.91,
    )
    no_answer = RetrievalQueryResult(
        query_id="rq-2",
        ranked_doc_ids=["doc-b"],
        relevant_doc_ids=[],
        reciprocal_rank=0.0,
        recall_at_1=0.0,
        recall_at_5=0.0,
        hit_at_1=False,
        hit_at_5=False,
        top_score=0.42,
    )
    return RetrievalEvalReport(
        pipeline_name="axis-fake",
        corpus_path="corpus.jsonl",
        queries_path="queries.jsonl",
        corpus_doc_count=2,
        chunk_count=3,
        query_count=2,
        metrics=RetrievalMetrics(
            scored_query_count=1,
            mrr=1.0,
            recall_at_1=1.0,
            recall_at_5=1.0,
            hit_rate_at_1=1.0,
            hit_rate_at_5=1.0,
        ),
        queries=[scored],
        no_answer_queries=[no_answer],
    )


def test_render_retrieval_text_sections() -> None:
    text = render_retrieval_text(_report())
    assert "pipeline=axis-fake" in text
    assert "recall@1=1.00" in text
    assert "MRR=1.00" in text
    assert "rq-1: rr=1.00" in text
    assert "No-answer queries (informational, not gated):" in text
    assert "rq-2: top-1 score=0.420" in text


def test_render_retrieval_text_omits_no_answer_section_when_empty() -> None:
    report = _report().model_copy(update={"no_answer_queries": []})
    assert "No-answer queries" not in render_retrieval_text(report)


def test_render_retrieval_json_round_trips() -> None:
    report = _report()
    restored = RetrievalEvalReport.model_validate_json(render_retrieval_json(report))
    assert restored == report
