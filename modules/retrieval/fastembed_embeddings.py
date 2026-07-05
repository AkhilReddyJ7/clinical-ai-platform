from fastembed import TextEmbedding

from modules.retrieval.base import EmbeddingPipeline


class FastEmbedEmbeddingPipeline(EmbeddingPipeline):
    """Local, ONNX-based embeddings (ADR-0034) -- no network call at
    embed time, no paid API key. The model itself is baked into the
    Docker image at build time (infrastructure/docker/api.Dockerfile);
    cache_dir must match that bake path or this downloads at runtime.
    """

    def __init__(self, *, model_name: str, cache_dir: str) -> None:
        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [vector.tolist() for vector in self._model.embed(texts)]
