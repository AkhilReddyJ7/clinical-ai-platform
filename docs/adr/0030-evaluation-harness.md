# 0030: Evaluation harness

- **Status:** Accepted
- **Date:** 2026-07-05
- **Follows up on:** `docs/architecture/idp-platform-pivot-baseline.md` (Phase
  A — the highest-priority phase, since every later phase's quality claims
  are otherwise unmeasurable), and ADR-0025 (confidence semantics — this is
  the first place real, ground-truth field accuracy is measured at all;
  document/field confidence has only ever been a derived plausibility
  heuristic, never a measurement).

## Context

Three sprints of real LLM-based field extraction (ADR-0019) and PHI
detection (ADR-0008/0011/0015/0018) have shipped with no way to answer
"is it actually correct" — only "did it run without crashing." The
live-Anthropic-credentials verification recorded as deferred in ADR-0019
has also never been discharged (still blocked on real API credits as of
this writing). This ADR builds the harness that measures both, so that
neither question stays open indefinitely, and so that Phase C (RAG)
later has something to evaluate retrieval quality against instead of
inventing its own ad hoc measurement.

## Decision

### 1. Dataset: `eval/dataset/cases.jsonl`, 15 hand-authored synthetic cases

JSON Lines, stdlib `json` only. Each row:

```json
{"case_id": "case-001", "raw_text": "...", "expected_fields": {"patient_name": "...", "date_of_birth": "...", "mrn": "..."}, "expected_phi_labels": [], "notes": "..."}
```

`expected_fields` is a subset of `{"patient_name", "date_of_birth",
"mrn"}` (`_ALLOWED_FIELD_NAMES`, `modules/extraction/anthropic_extractor.py`)
— a key's *absence* asserts the correct behavior is to omit it, testing
the extractor's own "omit rather than guess" instruction, not "unknown,
don't check." `expected_phi_labels` is a subset of the 5 fixed labels in
`modules/validation/phi.py`'s `_PHI_PATTERNS`.

Composition of the 15: 8 clean cases (all 3 fields present and
unambiguous), 3 with fields legitimately absent from the text, 1
adversarial distractor (a decoy non-DOB date and no explicit MRN label,
testing that the extractor doesn't substitute the wrong identifier), 3
with injected PHI-shaped strings covering all 5 patterns.

**Hand-authored now, not a real Synthea (or equivalent) corpus.** Synthea
outputs FHIR resources, not free text shaped around these 3 specific
fields — turning its output into scoreable ground truth requires custom
templating regardless of source, and PHI-shaped strings must be
hand-injected on top of any base text either way. The deliverable here is
proving the harness itself works and is repeatable; a larger generated
corpus is real future value, explicitly deferred until Phase B (lineage)
or Phase C (RAG) need real volume — at which point it reuses this same
harness unchanged. Not a silent scope-narrowing: recorded here so a
future contributor doesn't assume 15 cases was an oversight rather than
a sizing decision.

### 2. Scoring: exact match primary, stdlib fuzzy match diagnostic only, no new dependency

`difflib.SequenceMatcher` (stdlib) is sufficient at this string length
(names, dates, MRNs) — a dedicated fuzzy-matching dependency
(`rapidfuzz`/`thefuzz`) would add a library for a problem stdlib already
solves, and this project has consistently declined a new dependency
without a concrete gap (Tesseract over cloud OCR, regex over Presidio,
Postgres over Redis).

- **Field-level**: exact match (`.strip() ==`) is the metric that
  actually matters — the extractor's own system prompt promises exact
  preservation, so a fuzzy "close enough" would mask a real defect.
  Fuzzy ratio ≥0.85 is computed and reported alongside purely as a
  debugging aid (separating formatting noise from genuine misses), never
  substituting for exact match in the headline numbers. TP/FP/FN per
  field name: expected-present + exact match = TP; expected-present +
  wrong-or-missing = FN (and also FP if a wrong value was substituted —
  standard slot-filling treatment, penalizing both precision and recall
  for a confident wrong answer); expected-absent + predicted-present =
  FP (hallucination). Precision/recall/F1 reported per field name plus
  an aggregated "overall" row, plus a stricter document-level
  exact-match rate (all 3 fields correct in the same case).
- **PHI detection**: case-level is the primary metric, matching how
  `PHIDetectionValidator.validate().is_valid` actually gates the whole
  document today (ADR-0011) — not a per-pattern gate. Standard
  confusion matrix (TP/FP/FN/TN) over the 15 cases. A secondary,
  best-effort per-label breakdown is computed by substring-matching each
  expected label against the validator's returned issue strings (the
  documented `"phi: possible {label} detected in extracted text"`
  format) — reading the validator's output *contract*, not its private
  `_PHI_PATTERNS` constant.

### 3. Report: stdout text always, optional JSON under `eval/reports/`, gitignored

A report is a function of (dataset revision × pipeline × model) at the
moment it ran — not source of truth, and would go stale immediately if
committed. Same reasoning `data/` (runtime upload storage) is already
gitignored. No dashboard, no monitoring stack — same restraint already
applied to the metrics API (ADR-0029): a report artifact, not
infrastructure.

### 4. Mock by default, `--live` opt-in, fails closed with no key

`scripts/run_eval.py` defaults to `MockFieldExtractionPipeline` always.
`--live` is required to attempt the real `AnthropicFieldExtractionPipeline`;
if passed while `settings.anthropic_api_key` is empty, the script exits
immediately with a clear error rather than constructing the pipeline and
letting it fail deep inside a call. `make eval` and CI never pass
`--live` — no run costs a real API call unless a human explicitly opts in
with a real key configured, consistent with `.github/workflows/ci.yml`'s
already-documented no-live-LLM-call-in-CI posture (ADR-0019).

### 5. No CI gating yet

`--fail-under` exists as a mechanism (exits 1 if the document-level
exact-match rate falls below a given threshold) but nothing wires it into
CI today. Matches ADR-0025's precedent: a new measurement does not get to
silently start gating behavior the moment it exists — that would be its
own decision, made deliberately, once there's a real basis for choosing
a threshold.

## Consequences

- New module `modules/evaluation/` — `schemas.py`, `scoring.py`,
  `dataset.py`, `service.py`, `report.py`. No owned table, no
  `__init__.py`, matching `modules/analytics/`'s shape exactly (pure
  aggregation/logic, not a persistence-owning module).
- New top-level `eval/` directory: `eval/dataset/cases.jsonl` (committed),
  `eval/reports/` (gitignored).
- New `scripts/run_eval.py` — the first real code in what was previously
  an empty `scripts/` directory; `.github/workflows/ci.yml`'s mypy
  invocation is extended to include `scripts` so it doesn't silently
  escape strict-mode type checking.
- New Makefile `eval` target; README gains an "Evaluation harness"
  section.
- This is also the mechanism that finally makes the Sprint 2 live-
  Anthropic-credentials verification dischargeable — running
  `make eval ARGS="--live"` once a real key exists *is* that verification,
  not a separate step.
