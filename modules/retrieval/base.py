from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class RetrievedChunk:
    document_id: str
    extraction_id: str
    chunk_index: int
    chunk_text: str
    score: float


class EmbeddingPipeline(ABC):
    """Interface for turning text into vectors. Separate from
    FieldExtractionPipeline (modules/extraction/base.py) -- a distinct
    stage with its own swappable implementation, per ADR-0002's
    interface-first convention.
    """

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class VectorStore(ABC):
    """Interface over a vector store's write/delete/query operations.
    Embeddings are always computed by an EmbeddingPipeline and passed in
    explicitly (ADR-0034) -- a VectorStore implementation never computes
    its own embeddings.
    """

    @abstractmethod
    def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, str | int]],
    ) -> None: ...

    @abstractmethod
    def delete(self, *, where: dict[str, str]) -> None: ...

    @abstractmethod
    def query(self, *, query_embedding: list[float], top_k: int) -> list[RetrievedChunk]: ...
