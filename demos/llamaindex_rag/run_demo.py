"""LlamaIndex RAG demo (Phase D -- orchestration-framework literacy).

Reimplements a reduced version of Phase C's retrieval flow using
LlamaIndex instead of this project's own modules/retrieval/ pipeline --
a comparison/demo, not production code. See README.md in this directory
for what's identical, what's different, and why this stays separate from
the production path (ADR-0019).

Usage:
    uv sync --group demo
    uv run python -m demos.llamaindex_rag.run_demo
"""

from pathlib import Path

from llama_index.core import Document, Settings, VectorStoreIndex
from llama_index.embeddings.fastembed import FastEmbedEmbedding

from modules.evaluation.dataset import load_cases
from shared.config.settings import get_settings

DATASET_PATH = Path("eval/dataset/cases.jsonl")
SAMPLE_SIZE = 6
QUERY = "What medication is the patient taking, and for what condition?"


def main() -> None:
    # Same embedding model as the production pipeline (ADR-0034) -- the
    # point of comparison is the orchestration layer, not the embeddings.
    Settings.embed_model = FastEmbedEmbedding(model_name="BAAI/bge-small-en-v1.5")

    cases = load_cases(DATASET_PATH)[:SAMPLE_SIZE]
    documents = [
        Document(text=case.raw_text, doc_id=case.case_id, metadata={"case_id": case.case_id})
        for case in cases
    ]
    print(f"Indexed {len(documents)} documents from {DATASET_PATH} (in-memory, no Chroma).\n")

    # LlamaIndex's own chunking/indexing -- unlike modules/retrieval/,
    # nothing here calls chunk_text() or a VectorStore interface directly;
    # the framework owns both.
    index = VectorStoreIndex.from_documents(documents)

    print(f"Query: {QUERY!r}\n")
    print("--- Retrieval only (no LLM call, no API key needed) ---")
    retriever = index.as_retriever(similarity_top_k=3)
    for node in retriever.retrieve(QUERY):
        print(f"  score={node.score:.4f} case_id={node.metadata.get('case_id')}")
        print(f"    {node.text[:120]}")

    settings = get_settings()
    if not settings.anthropic_api_key:
        print(
            "\n--- Query engine (retrieval + generation) skipped: "
            "ANTHROPIC_API_KEY is not configured ---"
        )
        return

    from llama_index.llms.anthropic import Anthropic

    print("\n--- Query engine (retrieval + generation, via Anthropic) ---")
    query_engine = index.as_query_engine(
        llm=Anthropic(model=settings.anthropic_model, api_key=settings.anthropic_api_key)
    )
    response = query_engine.query(QUERY)
    print(response)


if __name__ == "__main__":
    main()
