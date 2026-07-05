from modules.evaluation.schemas import EvalReport

_FIELD_HEADER = f"{'field':<16}{'tp':>6}{'fp':>6}{'fn':>6}{'precision':>11}{'recall':>9}{'f1':>9}"


def _field_row(
    name: str, tp: int, fp: int, fn: int, precision: float, recall: float, f1: float
) -> str:
    return f"{name:<16}{tp:>6}{fp:>6}{fn:>6}{precision:>11.2f}{recall:>9.2f}{f1:>9.2f}"


def render_text(report: EvalReport) -> str:
    """Plain stdlib string formatting -- no `rich`/`tabulate` dependency,
    matching the same restraint already applied to the metrics API
    (ADR-0029), since a report artifact is not infrastructure (ADR-0030).
    """
    lines = [
        f"Evaluation report -- pipeline={report.pipeline_name} "
        f"dataset={report.dataset_path} cases={report.case_count}",
        "",
        "Field extraction:",
        _FIELD_HEADER,
    ]
    for m in report.field_metrics:
        lines.append(
            _field_row(
                m.field_name,
                m.true_positives,
                m.false_positives,
                m.false_negatives,
                m.precision,
                m.recall,
                m.f1,
            )
        )
    lines.append(f"document exact-match rate: {report.document_exact_match_rate:.2f}")
    lines.append("")

    phi = report.phi_metrics
    lines.append("PHI detection (case-level):")
    lines.append(
        f"tp={phi.true_positives} fp={phi.false_positives} "
        f"fn={phi.false_negatives} tn={phi.true_negatives} "
        f"precision={phi.precision:.2f} recall={phi.recall:.2f} f1={phi.f1:.2f}"
    )
    lines.append("")

    lines.append("Per-case results:")
    for case in report.cases:
        match_marker = "match" if case.document_exact_match else "MISMATCH"
        phi_marker = (
            "phi-ok" if case.expected_phi_flagged == case.predicted_phi_flagged else "PHI-MISS"
        )
        lines.append(
            f"  {case.case_id}: {match_marker}, {phi_marker} "
            f"predicted={case.predicted_fields} expected={case.expected_fields}"
        )

    return "\n".join(lines)


def render_json(report: EvalReport) -> str:
    return report.model_dump_json(indent=2)
