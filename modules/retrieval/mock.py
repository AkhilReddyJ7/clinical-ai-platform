import hashlib
import math

from modules.retrieval.base import EmbeddingPipeline, RetrievedChunk, VectorStore

# Matches BAAI/bge-small-en-v1.5's real output dimensionality (ADR-0034) --
# not load-bearing for correctness, just keeps mock vectors a realistic shape.
_EMBEDDING_DIM = 384


class MockEmbeddingPipeline(EmbeddingPipeline):
    """Deterministic, hash-derived stand-in vectors -- not real embeddings,
    exercises the harness/pipeline plumbing cheaply and offline, mirroring
    modules/extraction/mock.py's synthesize_fields_from_text approach.
    """

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = (digest * ((_EMBEDDING_DIM // len(digest)) + 1))[:_EMBEDDING_DIM]
        return [b / 255.0 for b in raw]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class InMemoryVectorStore(VectorStore):
    """Dict-backed fake for tests -- no network, real cosine scoring over
    the (small) in-memory set, so ranking behavior is genuinely exercised,
    not just plumbing.
    """

    def __init__(self) -> None:
        self._entries: dict[str, tuple[list[float], str, dict[str, str | int]]] = {}

    def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, str | int]],
    ) -> None:
        for id_, embedding, document, metadata in zip(ids, embeddings, documents, metadatas):
            self._entries[id_] = (embedding, document, metadata)

    def delete(self, *, where: dict[str, str]) -> None:
        to_delete = [
            id_
            for id_, (_, _, metadata) in self._entries.items()
            if all(metadata.get(key) == value for key, value in where.items())
        ]
        for id_ in to_delete:
            del self._entries[id_]

    def query(self, *, query_embedding: list[float], top_k: int) -> list[RetrievedChunk]:
        scored = [
            (_cosine_similarity(query_embedding, embedding), metadata, document)
            for embedding, document, metadata in self._entries.values()
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            RetrievedChunk(
                document_id=str(metadata["document_id"]),
                extraction_id=str(metadata["extraction_id"]),
                chunk_index=int(metadata["chunk_index"]),
                chunk_text=document,
                score=score,
            )
            for score, metadata, document in scored[:top_k]
        ]
