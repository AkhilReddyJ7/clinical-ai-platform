from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from modules.retrieval.base import RetrievedChunk


@dataclass
class GeneratedAnswer:
    answer: str
    insufficient_context: bool
    # 0-based positions into the `chunks` list passed to generate().
    # Contract: always valid indices -- implementations must drop anything
    # out of range, deduplicate, and preserve first-cited order, so the
    # route can index into `chunks` without re-validating (ADR-0038).
    cited_chunk_indices: list[int] = field(default_factory=list)


class AnswerGenerationError(Exception):
    """The generation call itself failed (unreachable provider, rejected
    request, malformed response) -- distinct from a successful abstention,
    which is a GeneratedAnswer with insufficient_context=True. Mirrors
    FieldExtractionError (modules/extraction/base.py).
    """


class AnswerGenerationNotConfiguredError(AnswerGenerationError):
    """No API key configured -- a deterministic operator misconfiguration,
    mapped to 503 at the route (the same fail-closed posture as
    require_api_key's no-keys-configured branch, ADR-0026), where the
    parent maps to 502. A subclass so a route catching only the parent
    still fails safely.
    """


class AnswerGenerator(ABC):
    """Seam for grounded answer generation over retrieved chunks
    (ADR-0038). Single-provider by design, same as FieldExtractionPipeline
    (ADR-0019) -- this ABC is where a second implementation would plug in,
    not a provider tree built in advance.
    """

    @abstractmethod
    def generate(self, *, question: str, chunks: list[RetrievedChunk]) -> GeneratedAnswer:
        """Answers `question` grounded only in `chunks`. Raises
        AnswerGenerationError when the call itself fails; returns an
        abstention (insufficient_context=True) when it succeeds but the
        context can't support an answer.
        """

    @property
    def generator_version(self) -> str:
        return type(self).__name__
