import uuid

from modules.retrieval.base import EmbeddingPipeline, RetrievedChunk, VectorStore
from modules.retrieval.chunking import chunk_text


class RetrievalService:
    """Orchestrates chunk -> embed -> upsert/query (ADR-0034/0035). One
    facade, two call sites: pipeline.py's indexing hook and the
    POST /retrieval/query route.
    """

    def __init__(
        self,
        *,
        embedding_pipeline: EmbeddingPipeline,
        vector_store: VectorStore,
        chunk_size_chars: int = 2_000,
        overlap_chars: int = 200,
    ) -> None:
        self._embedding_pipeline = embedding_pipeline
        self._vector_store = vector_store
        self._chunk_size_chars = chunk_size_chars
        self._overlap_chars = overlap_chars

    def index_extraction(
        self, *, document_id: uuid.UUID, extraction_id: uuid.UUID, raw_text: str
    ) -> int:
        """(Re-)indexes one ExtractionResult's text. Deletes any chunks
        already indexed for this document_id first -- keeps reprocessing
        (a new extraction_id for the same document_id, ADR-0032) from
        leaving stale chunks behind, since the old attempt's text is no
        longer "current" (ADR-0031). Returns the number of chunks indexed
        (0 if raw_text was blank).
        """
        self._vector_store.delete(where={"document_id": str(document_id)})

        chunks = chunk_text(
            raw_text, chunk_size_chars=self._chunk_size_chars, overlap_chars=self._overlap_chars
        )
        if not chunks:
            return 0

        embeddings = self._embedding_pipeline.embed(chunks)
        ids = [f"{extraction_id}:{i}" for i in range(len(chunks))]
        metadatas: list[dict[str, str | int]] = [
            {
                "document_id": str(document_id),
                "extraction_id": str(extraction_id),
                "chunk_index": i,
            }
            for i in range(len(chunks))
        ]
        self._vector_store.upsert(
            ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas
        )
        return len(chunks)

    def query(self, *, query_text: str, top_k: int) -> list[RetrievedChunk]:
        query_embedding = self._embedding_pipeline.embed([query_text])[0]
        return self._vector_store.query(query_embedding=query_embedding, top_k=top_k)
