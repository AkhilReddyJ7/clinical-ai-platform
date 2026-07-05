from typing import Any, cast

import chromadb

from modules.retrieval.base import RetrievedChunk, VectorStore


class ChromaVectorStore(VectorStore):
    """Chroma-backed vector store (ADR-0033), connected via the
    lightweight `chromadb-client` HTTP-only package -- Chroma runs as its
    own docker-compose service, not embedded in this process. Embeddings
    are always computed by an EmbeddingPipeline and passed in explicitly;
    this class never calls Chroma's own default embedding function.

    chromadb's own types are wider than this project needs (numpy arrays,
    SparseVector metadata values, etc.) -- boundary values are cast to
    Any at each call, mirroring how modules/processing/repository.py
    already casts at a third-party-typed boundary (CursorResult).
    """

    def __init__(self, *, host: str, port: int, collection_name: str) -> None:
        self._client = chromadb.HttpClient(host=host, port=port)
        # Explicit cosine space: Chroma's own default is squared L2, which
        # doesn't map cleanly onto a "higher is more similar" score.
        self._collection = self._client.get_or_create_collection(
            collection_name, metadata={"hnsw:space": "cosine"}
        )

    def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, str | int]],
    ) -> None:
        self._collection.upsert(
            ids=ids,
            embeddings=cast(Any, embeddings),
            documents=documents,
            metadatas=cast(Any, metadatas),
        )

    def delete(self, *, where: dict[str, str]) -> None:
        self._collection.delete(where=cast(Any, where))

    def query(self, *, query_embedding: list[float], top_k: int) -> list[RetrievedChunk]:
        raw = self._collection.query(query_embeddings=cast(Any, [query_embedding]), n_results=top_k)
        result = cast(dict[str, list[list[Any]]], raw)
        ids = result["ids"][0]
        if not ids:
            return []
        distances = result["distances"][0]
        documents = result["documents"][0]
        metadatas = result["metadatas"][0]
        return [
            RetrievedChunk(
                document_id=str(metadata["document_id"]),
                extraction_id=str(metadata["extraction_id"]),
                chunk_index=int(metadata["chunk_index"]),
                chunk_text=document,
                # cosine distance -> cosine similarity
                score=1.0 - distance,
            )
            for document, metadata, distance in zip(documents, metadatas, distances)
        ]
