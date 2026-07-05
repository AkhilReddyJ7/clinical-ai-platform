"""ADR-0037: JSONL loaders for the retrieval corpus and query datasets,
using tmp_path files (not the committed dataset, so these tests don't
drift when the dataset is edited) -- same posture as test_eval_dataset.py.
"""

from pathlib import Path

import pytest

from modules.evaluation.dataset import load_retrieval_corpus, load_retrieval_queries

CORPUS_ROW = '{"doc_id": "doc-a", "text": "a clinical note"}'
QUERY_ROW = '{"query_id": "rq-1", "query_text": "a question", "relevant_doc_ids": ["doc-a"]}'


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_load_retrieval_corpus_happy_path(tmp_path: Path) -> None:
    path = _write(tmp_path, "corpus.jsonl", CORPUS_ROW + "\n")
    docs = load_retrieval_corpus(path)
    assert len(docs) == 1
    assert docs[0].doc_id == "doc-a"
    assert docs[0].notes == ""


def test_load_retrieval_corpus_skips_blank_lines(tmp_path: Path) -> None:
    path = _write(tmp_path, "corpus.jsonl", CORPUS_ROW + "\n\n   \n")
    assert len(load_retrieval_corpus(path)) == 1


def test_load_retrieval_corpus_malformed_row_names_path_and_line(tmp_path: Path) -> None:
    path = _write(tmp_path, "corpus.jsonl", CORPUS_ROW + "\nnot json\n")
    with pytest.raises(ValueError, match=rf"{path}:2"):
        load_retrieval_corpus(path)


def test_load_retrieval_corpus_rejects_duplicate_doc_id(tmp_path: Path) -> None:
    path = _write(tmp_path, "corpus.jsonl", CORPUS_ROW + "\n" + CORPUS_ROW + "\n")
    with pytest.raises(ValueError, match="duplicate doc_id 'doc-a'"):
        load_retrieval_corpus(path)


def test_load_retrieval_queries_happy_path(tmp_path: Path) -> None:
    path = _write(tmp_path, "queries.jsonl", QUERY_ROW + "\n")
    queries = load_retrieval_queries(path, corpus_doc_ids={"doc-a"})
    assert len(queries) == 1
    assert queries[0].relevant_doc_ids == ["doc-a"]


def test_load_retrieval_queries_allows_no_answer_query(tmp_path: Path) -> None:
    row = '{"query_id": "rq-2", "query_text": "out of domain"}'
    path = _write(tmp_path, "queries.jsonl", row + "\n")
    queries = load_retrieval_queries(path, corpus_doc_ids={"doc-a"})
    assert queries[0].relevant_doc_ids == []


def test_load_retrieval_queries_rejects_unknown_doc_id(tmp_path: Path) -> None:
    path = _write(tmp_path, "queries.jsonl", QUERY_ROW + "\n")
    with pytest.raises(ValueError, match="query 'rq-1' references unknown doc_id 'doc-a'"):
        load_retrieval_queries(path, corpus_doc_ids={"doc-b"})


def test_committed_datasets_load_and_cross_validate() -> None:
    corpus = load_retrieval_corpus(Path("eval/dataset/retrieval_corpus.jsonl"))
    queries = load_retrieval_queries(
        Path("eval/dataset/retrieval_queries.jsonl"),
        corpus_doc_ids={doc.doc_id for doc in corpus},
    )
    assert len(corpus) >= 10
    assert any(len(q.relevant_doc_ids) > 1 for q in queries)
    assert any(not q.relevant_doc_ids for q in queries)
