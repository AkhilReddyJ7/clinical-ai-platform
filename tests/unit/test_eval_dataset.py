from pathlib import Path

import pytest

from modules.evaluation.dataset import load_cases


def test_load_cases_reads_each_line_as_a_case(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(
        '{"case_id": "case-001", "raw_text": "note one", '
        '"expected_fields": {"mrn": "MRN-1"}, "expected_phi_labels": [], "notes": "n/a"}\n'
        '{"case_id": "case-002", "raw_text": "note two"}\n',
        encoding="utf-8",
    )

    cases = load_cases(dataset)

    assert len(cases) == 2
    assert cases[0].case_id == "case-001"
    assert cases[0].expected_fields == {"mrn": "MRN-1"}
    assert cases[1].case_id == "case-002"
    assert cases[1].expected_fields == {}
    assert cases[1].expected_phi_labels == []


def test_load_cases_skips_blank_lines(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(
        '{"case_id": "case-001", "raw_text": "note one"}\n'
        "\n"
        '{"case_id": "case-002", "raw_text": "note two"}\n',
        encoding="utf-8",
    )

    cases = load_cases(dataset)

    assert len(cases) == 2


def test_load_cases_raises_a_clear_error_on_a_malformed_row(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(
        '{"case_id": "case-001", "raw_text": "note one"}\n'
        '{"case_id": "case-002"}\n',  # missing required raw_text
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"cases\.jsonl:2"):
        load_cases(dataset)
