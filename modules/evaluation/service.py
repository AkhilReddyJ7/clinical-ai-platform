import uuid
from pathlib import Path

from modules.evaluation.schemas import (
    CaseResult,
    EvalCase,
    EvalReport,
    RetrievalCorpusDoc,
    RetrievalEvalReport,
    RetrievalQueryCase,
    RetrievalQueryResult,
)
from modules.evaluation.scoring import (
    aggregate_field_metrics,
    aggregate_phi_metrics,
    aggregate_retrieval_metrics,
    dedupe_ranked,
    hit_at_k,
    recall_at_k,
    reciprocal_rank,
    score_fields,
    score_phi,
)
from modules.extraction.base import FieldExtractionPipeline
from modules.ocr.base import ExtractionOutput
from modules.retrieval.service import RetrievalService
from modules.validation.base import ValidationPipeline

_RETRIEVAL_EVAL_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "clinical-ai-platform/retrieval-eval")


def build_report(
    *,
    pipeline_name: str,
    dataset_path: Path,
    cases: list[EvalCase],
    extractor: FieldExtractionPipeline,
    phi_validator: ValidationPipeline,
) -> EvalReport:
    """Runs every case through the given extraction pipeline and PHI
    validator and scores both (ADR-0030). `phi_validator` receives a
    throwaway `ExtractionOutput(raw_text=..., fields={}, confidence=0.0)`
    -- the same construction `modules/processing/pipeline.py`'s
    PHI-precheck already uses to run text-only PHI detection.
    """
    field_outcomes_per_case = []
    phi_outcomes_per_case = []
    case_results = []

    for case in cases:
        extraction = extractor.extract_fields(raw_text=case.raw_text)
        field_outcomes = score_fields(case.expected_fields, extraction.fields)
        field_outcomes_per_case.append(field_outcomes)
        document_exact_match = all(fp == 0 and fn == 0 for _, fp, fn in field_outcomes.values())

        phi_result = phi_validator.validate(ExtractionOutput(raw_text=case.raw_text))
        expected_phi_flagged = bool(case.expected_phi_labels)
        predicted_phi_flagged = not phi_result.is_valid
        phi_outcomes_per_case.append(
            score_phi(
                expected_flagged=expected_phi_flagged, predicted_flagged=predicted_phi_flagged
            )
        )

        case_results.append(
            CaseResult(
                case_id=case.case_id,
                predicted_fields=extraction.fields,
                expected_fields=case.expected_fields,
                document_exact_match=document_exact_match,
                expected_phi_flagged=expected_phi_flagged,
                predicted_phi_flagged=predicted_phi_flagged,
                phi_issues=phi_result.issues,
            )
        )

    exact_match_rate = (
        sum(1 for r in case_results if r.document_exact_match) / len(case_results)
        if case_results
        else 0.0
    )

    return EvalReport(
        pipeline_name=pipeline_name,
        dataset_path=str(dataset_path),
        case_count=len(cases),
        field_metrics=aggregate_field_metrics(field_outcomes_per_case),
        document_exact_match_rate=exact_match_rate,
        phi_metrics=aggregate_phi_metrics(phi_outcomes_per_case),
        cases=case_results,
    )


def build_retrieval_report(
    *,
    pipeline_name: str,
    corpus_path: Path,
    queries_path: Path,
    corpus_docs: list[RetrievalCorpusDoc],
    queries: list[RetrievalQueryCase],
    retrieval_service: RetrievalService,
    retrieve_top_k_chunks: int = 20,
) -> RetrievalEvalReport:
    """Indexes the corpus through the *production* index path
    (RetrievalService.index_extraction -- same chunking params, chunk-ID
    scheme, and delete-then-upsert as pipeline.py's hook) and scores each
    query's ranked results at the document level (ADR-0037).

    `retrieve_top_k_chunks` defaults to 20 (= retrieval_max_top_k): a
    multi-chunk document can occupy several chunk slots, so more chunks
    than the k=5 document cutoff must be retrieved before deduping.
    """
    uuid_to_doc_id: dict[str, str] = {}
    chunk_count = 0
    for doc in corpus_docs:
        document_id = uuid.uuid5(_RETRIEVAL_EVAL_NAMESPACE, doc.doc_id)
        extraction_id = uuid.uuid5(_RETRIEVAL_EVAL_NAMESPACE, doc.doc_id + ":extraction")
        uuid_to_doc_id[str(document_id)] = doc.doc_id
        chunk_count += retrieval_service.index_extraction(
            document_id=document_id, extraction_id=extraction_id, raw_text=doc.text
        )

    scored_results = []
    no_answer_results = []
    for query in queries:
        chunks = retrieval_service.query(query_text=query.query_text, top_k=retrieve_top_k_chunks)
        ranked_doc_ids = dedupe_ranked([uuid_to_doc_id[c.document_id] for c in chunks])
        top_score = chunks[0].score if chunks else None

        if not query.relevant_doc_ids:
            no_answer_results.append(
                RetrievalQueryResult(
                    query_id=query.query_id,
                    ranked_doc_ids=ranked_doc_ids,
                    relevant_doc_ids=[],
                    reciprocal_rank=0.0,
                    recall_at_1=0.0,
                    recall_at_5=0.0,
                    hit_at_1=False,
                    hit_at_5=False,
                    top_score=top_score,
                )
            )
            continue

        scored_results.append(
            RetrievalQueryResult(
                query_id=query.query_id,
                ranked_doc_ids=ranked_doc_ids,
                relevant_doc_ids=query.relevant_doc_ids,
                reciprocal_rank=reciprocal_rank(ranked_doc_ids, query.relevant_doc_ids),
                recall_at_1=recall_at_k(ranked_doc_ids, query.relevant_doc_ids, 1),
                recall_at_5=recall_at_k(ranked_doc_ids, query.relevant_doc_ids, 5),
                hit_at_1=hit_at_k(ranked_doc_ids, query.relevant_doc_ids, 1),
                hit_at_5=hit_at_k(ranked_doc_ids, query.relevant_doc_ids, 5),
                top_score=top_score,
            )
        )

    return RetrievalEvalReport(
        pipeline_name=pipeline_name,
        corpus_path=str(corpus_path),
        queries_path=str(queries_path),
        corpus_doc_count=len(corpus_docs),
        chunk_count=chunk_count,
        query_count=len(queries),
        metrics=aggregate_retrieval_metrics(scored_results),
        queries=scored_results,
        no_answer_queries=no_answer_results,
    )
