from modules.retrieval.answer_mock import MockAnswerGenerator
from modules.retrieval.base import RetrievedChunk

_CHUNK = RetrievedChunk(
    document_id="11111111-1111-1111-1111-111111111111",
    extraction_id="22222222-2222-2222-2222-222222222222",
    chunk_index=0,
    chunk_text="a passage",
    score=0.9,
)


def test_same_question_yields_same_answer() -> None:
    generator = MockAnswerGenerator()
    first = generator.generate(question="a question", chunks=[_CHUNK])
    second = generator.generate(question="a question", chunks=[_CHUNK])
    assert first == second
    assert first.insufficient_context is False


def test_different_questions_yield_different_answers() -> None:
    generator = MockAnswerGenerator()
    first = generator.generate(question="question one", chunks=[_CHUNK])
    second = generator.generate(question="question two", chunks=[_CHUNK])
    assert first.answer != second.answer


def test_empty_chunks_abstains() -> None:
    result = MockAnswerGenerator().generate(question="a question", chunks=[])
    assert result.insufficient_context is True
    assert result.cited_chunk_indices == []


def test_cited_indices_are_valid_for_a_single_chunk_list() -> None:
    result = MockAnswerGenerator().generate(question="a question", chunks=[_CHUNK])
    assert all(0 <= i < 1 for i in result.cited_chunk_indices)
    assert result.cited_chunk_indices  # cites something when context exists
