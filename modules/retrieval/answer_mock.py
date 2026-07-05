import hashlib

from modules.retrieval.answer_base import AnswerGenerator, GeneratedAnswer
from modules.retrieval.base import RetrievedChunk


class MockAnswerGenerator(AnswerGenerator):
    """Deterministic, hash-derived stand-in answers -- no network call,
    exercises the route/response plumbing cheaply and offline, mirroring
    MockFieldExtractionPipeline (modules/extraction/mock.py).
    """

    @property
    def generator_version(self) -> str:
        return "mock"

    def generate(self, *, question: str, chunks: list[RetrievedChunk]) -> GeneratedAnswer:
        if not chunks:
            return GeneratedAnswer(
                answer="Mock abstention: no context available.", insufficient_context=True
            )
        digest = hashlib.sha256(question.encode("utf-8")).hexdigest()
        return GeneratedAnswer(
            answer=f"Synthetic grounded answer [{digest[:8]}] to: {question}",
            insufficient_context=False,
            # always a valid index: chunks is non-empty here (the
            # GeneratedAnswer contract requires in-range indices)
            cited_chunk_indices=[0],
        )
