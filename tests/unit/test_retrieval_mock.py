from modules.retrieval.mock import InMemoryVectorStore, MockEmbeddingPipeline


def test_mock_embedding_pipeline_is_deterministic() -> None:
    pipeline = MockEmbeddingPipeline()
    first = pipeline.embed(["a clinical note"])
    second = pipeline.embed(["a clinical note"])
    assert first == second


def test_mock_embedding_pipeline_different_text_gives_different_vectors() -> None:
    pipeline = MockEmbeddingPipeline()
    vectors = pipeline.embed(["note one", "note two"])
    assert vectors[0] != vectors[1]


def test_mock_embedding_pipeline_returns_one_vector_per_text() -> None:
    pipeline = MockEmbeddingPipeline()
    vectors = pipeline.embed(["a", "b", "c"])
    assert len(vectors) == 3
    assert all(len(v) == 384 for v in vectors)


def test_in_memory_vector_store_upsert_and_query_round_trips() -> None:
    store = InMemoryVectorStore()
    pipeline = MockEmbeddingPipeline()
    vectors = pipeline.embed(["chunk about diabetes", "chunk about fractures"])

    store.upsert(
        ids=["ext-1:0", "ext-1:1"],
        embeddings=vectors,
        documents=["chunk about diabetes", "chunk about fractures"],
        metadatas=[
            {"document_id": "doc-1", "extraction_id": "ext-1", "chunk_index": 0},
            {"document_id": "doc-1", "extraction_id": "ext-1", "chunk_index": 1},
        ],
    )

    results = store.query(query_embedding=pipeline.embed(["chunk about diabetes"])[0], top_k=1)

    assert len(results) == 1
    assert results[0].chunk_text == "chunk about diabetes"
    assert results[0].document_id == "doc-1"
    assert results[0].extraction_id == "ext-1"
    assert results[0].chunk_index == 0


def test_in_memory_vector_store_delete_by_metadata_filter() -> None:
    store = InMemoryVectorStore()
    pipeline = MockEmbeddingPipeline()
    vectors = pipeline.embed(["from doc 1", "from doc 2"])

    store.upsert(
        ids=["a", "b"],
        embeddings=vectors,
        documents=["from doc 1", "from doc 2"],
        metadatas=[
            {"document_id": "doc-1", "extraction_id": "ext-1", "chunk_index": 0},
            {"document_id": "doc-2", "extraction_id": "ext-2", "chunk_index": 0},
        ],
    )

    store.delete(where={"document_id": "doc-1"})

    results = store.query(query_embedding=vectors[0], top_k=10)
    assert len(results) == 1
    assert results[0].document_id == "doc-2"


def test_in_memory_vector_store_query_respects_top_k() -> None:
    store = InMemoryVectorStore()
    pipeline = MockEmbeddingPipeline()
    texts = [f"chunk number {i}" for i in range(5)]
    vectors = pipeline.embed(texts)

    store.upsert(
        ids=[f"id-{i}" for i in range(5)],
        embeddings=vectors,
        documents=texts,
        metadatas=[
            {"document_id": "doc-1", "extraction_id": "ext-1", "chunk_index": i} for i in range(5)
        ],
    )

    results = store.query(query_embedding=vectors[0], top_k=2)
    assert len(results) == 2
