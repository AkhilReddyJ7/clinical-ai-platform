import json
from pathlib import Path

from modules.evaluation.schemas import EvalCase


def load_cases(path: Path) -> list[EvalCase]:
    """Reads one EvalCase per non-blank line of a JSON Lines file
    (ADR-0030). A malformed row surfaces pydantic's own validation error,
    with the offending line number added -- clearer than a bare parse
    failure somewhere in a 15+ line file.
    """
    cases = []
    with path.open(encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                cases.append(EvalCase.model_validate_json(line))
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"{path}:{line_number}: invalid eval case: {exc}") from exc
    return cases
