import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from modules.evaluation.schemas import EvalCase, RetrievalCorpusDoc, RetrievalQueryCase

_M = TypeVar("_M", bound=BaseModel)


def _load_jsonl(path: Path, model: type[_M], *, label: str) -> list[_M]:
    """Reads one `model` per non-blank line of a JSON Lines file
    (ADR-0030). A malformed row surfaces pydantic's own validation error,
    with the offending line number added -- clearer than a bare parse
    failure somewhere in a 15+ line file.
    """
    rows = []
    with path.open(encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(model.model_validate_json(line))
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"{path}:{line_number}: invalid {label}: {exc}") from exc
    return rows


def load_cases(path: Path) -> list[EvalCase]:
    return _load_jsonl(path, EvalCase, label="eval case")


def load_retrieval_corpus(path: Path) -> list[RetrievalCorpusDoc]:
    """Duplicate doc_ids are rejected outright -- relevance labels key on
    doc_id (ADR-0037), so a duplicate would make ground truth ambiguous.
    """
    docs = _load_jsonl(path, RetrievalCorpusDoc, label="retrieval corpus doc")
    seen: set[str] = set()
    for doc in docs:
        if doc.doc_id in seen:
            raise ValueError(f"{path}: duplicate doc_id {doc.doc_id!r}")
        seen.add(doc.doc_id)
    return docs


def load_retrieval_queries(path: Path, *, corpus_doc_ids: set[str]) -> list[RetrievalQueryCase]:
    """Cross-validates every relevant_doc_id against the loaded corpus so
    a typo in the labels fails loudly at load time, not as a silent
    always-zero recall.
    """
    queries = _load_jsonl(path, RetrievalQueryCase, label="retrieval query case")
    for query in queries:
        for doc_id in query.relevant_doc_ids:
            if doc_id not in corpus_doc_ids:
                raise ValueError(
                    f"{path}: query {query.query_id!r} references unknown doc_id {doc_id!r}"
                )
    return queries
