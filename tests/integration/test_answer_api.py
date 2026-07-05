"""ADR-0038: POST /retrieval/answer through the app with the mock answer
generator wired by conftest's client fixture; error-path tests override
the generator dependency with raising stubs.
"""

import uuid
from collections.abc import Awaitable, Callable

import pytest
from fastapi.testclient import TestClient

from apps.api.dependencies import get_answer_generator
from apps.api.main import app
from modules.retrieval.answer_base import (
    AnswerGenerationError,
    AnswerGenerationNotConfiguredError,
    AnswerGenerator,
    GeneratedAnswer,
)
from modules.retrieval.base import RetrievedChunk


class _RaisingGenerator(AnswerGenerator):
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def generate(self, *, question: str, chunks: list[RetrievedChunk]) -> GeneratedAnswer:
        raise self._exc


class _AbstainingGenerator(AnswerGenerator):
    def generate(self, *, question: str, chunks: list[RetrievedChunk]) -> GeneratedAnswer:
        return GeneratedAnswer(
            answer="The context does not contain this.", insufficient_context=True
        )


def test_answer_requires_api_key(client: TestClient) -> None:
    no_key_client = TestClient(app)
    response = no_key_client.post("/retrieval/answer", json={"question": "anything"})
    assert response.status_code == 401


def test_answer_rejects_blank_question(client: TestClient) -> None:
    response = client.post("/retrieval/answer", json={"question": "   "})
    assert response.status_code == 422


def test_answer_rejects_oversized_question(client: TestClient) -> None:
    response = client.post("/retrieval/answer", json={"question": "x" * 2_001})
    assert response.status_code == 422


def test_answer_rejects_top_k_above_the_configured_max(client: TestClient) -> None:
    response = client.post("/retrieval/answer", json={"question": "anything", "top_k": 10_000})
    assert response.status_code == 422


def test_empty_corpus_abstains_without_calling_the_generator(client: TestClient) -> None:
    # A generator that would blow up if reached proves the short-circuit.
    app.dependency_overrides[get_answer_generator] = lambda: _RaisingGenerator(
        AssertionError("generator must not be called for an empty corpus")
    )
    response = client.post("/retrieval/answer", json={"question": "anything at all"})
    assert response.status_code == 200
    body = response.json()
    assert body["insufficient_context"] is True
    assert body["citations"] == []
    assert body["answer"]


@pytest.mark.asyncio
async def test_answer_cites_the_validated_document(
    client: TestClient, process_job: Callable[[uuid.UUID], Awaitable[None]]
) -> None:
    upload = client.post(
        "/documents",
        files={
            "file": (
                "note.txt",
                b"synthetic clinical note content about diabetes management",
                "text/plain",
            )
        },
    )
    document_id = uuid.UUID(upload.json()["id"])
    client.post(f"/documents/{document_id}/process")
    await process_job(document_id)

    response = client.post("/retrieval/answer", json={"question": "how is diabetes being managed"})
    assert response.status_code == 200
    body = response.json()
    assert body["question"] == "how is diabetes being managed"
    assert body["answer"]
    assert body["insufficient_context"] is False
    assert len(body["citations"]) >= 1
    assert body["citations"][0]["document_id"] == str(document_id)
    assert body["citations"][0]["chunk_text"]


@pytest.mark.asyncio
async def test_model_abstention_is_a_200_with_no_citations(
    client: TestClient, process_job: Callable[[uuid.UUID], Awaitable[None]]
) -> None:
    upload = client.post(
        "/documents",
        files={"file": ("note.txt", b"synthetic clinical note content", "text/plain")},
    )
    document_id = uuid.UUID(upload.json()["id"])
    client.post(f"/documents/{document_id}/process")
    await process_job(document_id)

    app.dependency_overrides[get_answer_generator] = lambda: _AbstainingGenerator()
    response = client.post("/retrieval/answer", json={"question": "something unanswerable"})
    assert response.status_code == 200
    body = response.json()
    assert body["insufficient_context"] is True
    assert body["citations"] == []


@pytest.mark.asyncio
async def test_generation_failure_maps_to_502_with_static_detail(
    client: TestClient, process_job: Callable[[uuid.UUID], Awaitable[None]]
) -> None:
    upload = client.post(
        "/documents",
        files={"file": ("note.txt", b"synthetic clinical note content", "text/plain")},
    )
    document_id = uuid.UUID(upload.json()["id"])
    client.post(f"/documents/{document_id}/process")
    await process_job(document_id)

    app.dependency_overrides[get_answer_generator] = lambda: _RaisingGenerator(
        AnswerGenerationError("provider text that must not leak to the caller")
    )
    response = client.post("/retrieval/answer", json={"question": "anything"})
    assert response.status_code == 502
    assert response.json()["detail"] == "answer generation failed"
    assert "must not leak" not in response.text


@pytest.mark.asyncio
async def test_missing_key_maps_to_503(
    client: TestClient, process_job: Callable[[uuid.UUID], Awaitable[None]]
) -> None:
    upload = client.post(
        "/documents",
        files={"file": ("note.txt", b"synthetic clinical note content", "text/plain")},
    )
    document_id = uuid.UUID(upload.json()["id"])
    client.post(f"/documents/{document_id}/process")
    await process_job(document_id)

    app.dependency_overrides[get_answer_generator] = lambda: _RaisingGenerator(
        AnswerGenerationNotConfiguredError("Anthropic API key is not configured")
    )
    response = client.post("/retrieval/answer", json={"question": "anything"})
    assert response.status_code == 503
    assert response.json()["detail"] == "answer generation is not configured"
