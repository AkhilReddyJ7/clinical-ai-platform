"""CLI entrypoint for the retrieval-quality evaluation (ADR-0037).

Usage:
    uv run python -m scripts.run_retrieval_eval
    uv run python -m scripts.run_retrieval_eval --mock
    uv run python -m scripts.run_retrieval_eval --report-out eval/reports/retrieval.json --fail-under 0.8

Unlike scripts/run_eval.py, the *default* here is the real embedding
model: fastembed runs locally and free (ADR-0034), so measuring real
retrieval quality costs nothing -- the inverse of run_eval.py's --live
gate on the paid Anthropic call. --mock exists for fast plumbing checks.
"""

import argparse
import sys
from pathlib import Path

from modules.evaluation.dataset import load_retrieval_corpus, load_retrieval_queries
from modules.evaluation.report import render_retrieval_json, render_retrieval_text
from modules.evaluation.service import build_retrieval_report
from modules.retrieval.base import EmbeddingPipeline
from modules.retrieval.mock import InMemoryVectorStore, MockEmbeddingPipeline
from modules.retrieval.service import RetrievalService
from shared.config.settings import get_settings

DEFAULT_CORPUS = Path("eval/dataset/retrieval_corpus.jsonl")
DEFAULT_QUERIES = Path("eval/dataset/retrieval_queries.jsonl")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument(
        "--mock",
        action="store_true",
        help="use MockEmbeddingPipeline instead of the real fastembed model (plumbing checks only"
        " -- mock rankings say nothing about retrieval quality)",
    )
    parser.add_argument("--report-out", type=Path, default=None, help="write a JSON report here")
    parser.add_argument(
        "--fail-under",
        type=float,
        default=None,
        help="exit 1 if recall@5 falls below this value",
    )
    args = parser.parse_args(argv)

    settings = get_settings()

    embedding_pipeline: EmbeddingPipeline
    if args.mock:
        embedding_pipeline = MockEmbeddingPipeline()
        pipeline_name = "mock"
    else:
        if not Path(settings.embedding_model_cache_dir).exists():
            print(
                f"first run: downloading {settings.embedding_model_name} "
                f"(~130 MB) to {settings.embedding_model_cache_dir}",
                file=sys.stderr,
            )
        from modules.retrieval.fastembed_embeddings import FastEmbedEmbeddingPipeline

        embedding_pipeline = FastEmbedEmbeddingPipeline(
            model_name=settings.embedding_model_name,
            cache_dir=settings.embedding_model_cache_dir,
        )
        pipeline_name = f"fastembed:{settings.embedding_model_name}"

    retrieval_service = RetrievalService(
        embedding_pipeline=embedding_pipeline,
        vector_store=InMemoryVectorStore(),
        chunk_size_chars=settings.retrieval_chunk_size_chars,
        overlap_chars=settings.retrieval_chunk_overlap_chars,
    )

    corpus_docs = load_retrieval_corpus(args.corpus)
    queries = load_retrieval_queries(
        args.queries, corpus_doc_ids={doc.doc_id for doc in corpus_docs}
    )

    report = build_retrieval_report(
        pipeline_name=pipeline_name,
        corpus_path=args.corpus,
        queries_path=args.queries,
        corpus_docs=corpus_docs,
        queries=queries,
        retrieval_service=retrieval_service,
        retrieve_top_k_chunks=settings.retrieval_max_top_k,
    )
    print(render_retrieval_text(report))

    if args.report_out:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(render_retrieval_json(report), encoding="utf-8")
        print(f"\nreport written to {args.report_out}")

    if args.fail_under is not None and report.metrics.recall_at_5 < args.fail_under:
        print(
            f"\nrecall@5 {report.metrics.recall_at_5:.2f} "
            f"is below --fail-under {args.fail_under:.2f}",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
