import uuid

from modules.retrieval.mock import InMemoryVectorStore, MockEmbeddingPipeline
from modules.retrieval.service import RetrievalService


def _service() -> RetrievalService:
    return RetrievalService(
        embedding_pipeline=MockEmbeddingPipeline(),
        vector_store=InMemoryVectorStore(),
        chunk_size_chars=50,
        overlap_chars=10,
    )


def test_index_extraction_indexes_expected_chunk_count_and_metadata() -> None:
    service = _service()
    document_id = uuid.uuid4()
    extraction_id = uuid.uuid4()

    count = service.index_extraction(
        document_id=document_id, extraction_id=extraction_id, raw_text="short note"
    )

    assert count == 1
    results = service.query(query_text="short note", top_k=5)
    assert len(results) == 1
    assert results[0].document_id == str(document_id)
    assert results[0].extraction_id == str(extraction_id)
    assert results[0].chunk_index == 0


def test_index_extraction_returns_zero_for_blank_text() -> None:
    service = _service()
    count = service.index_extraction(
        document_id=uuid.uuid4(), extraction_id=uuid.uuid4(), raw_text="   "
    )
    assert count == 0


def test_reindexing_the_same_extraction_does_not_duplicate() -> None:
    service = _service()
    document_id = uuid.uuid4()
    extraction_id = uuid.uuid4()

    service.index_extraction(document_id=document_id, extraction_id=extraction_id, raw_text="hello")
    service.index_extraction(document_id=document_id, extraction_id=extraction_id, raw_text="hello")

    results = service.query(query_text="hello", top_k=10)
    assert len(results) == 1


def test_reprocessing_leaves_no_stale_chunks_from_the_prior_attempt() -> None:
    """The one substantive correctness behavior this design adds
    (ADR-0031/0032): a new extraction_id for the same document_id must
    replace, not accumulate alongside, the prior attempt's chunks.
    """
    service = _service()
    document_id = uuid.uuid4()
    first_extraction_id = uuid.uuid4()
    second_extraction_id = uuid.uuid4()

    service.index_extraction(
        document_id=document_id, extraction_id=first_extraction_id, raw_text="first attempt text"
    )
    service.index_extraction(
        document_id=document_id, extraction_id=second_extraction_id, raw_text="second attempt text"
    )

    results = service.query(query_text="attempt text", top_k=10)
    assert len(results) == 1
    assert results[0].extraction_id == str(second_extraction_id)
    assert results[0].chunk_text == "second attempt text"


def test_query_respects_top_k() -> None:
    service = _service()
    for i in range(5):
        service.index_extraction(
            document_id=uuid.uuid4(), extraction_id=uuid.uuid4(), raw_text=f"note number {i}"
        )

    results = service.query(query_text="note number 0", top_k=2)
    assert len(results) == 2
