# 0025: Confidence and quality semantics model for document processing

- **Status:** Accepted
- **Date:** 2026-07-04
- **Follows up on:** Sprint 3 Increment 6
  (`modules/processing/pipeline.py`: `_aggregate_confidence`,
  `_compute_field_confidence`, `_categorize_issues`, `_build_metadata`),
  which introduced confidence-aware processing without a formal
  system-level definition of what a confidence signal *means* or who is
  allowed to act on it. [0020](0020-document-and-job-state-machines.md)
  (document/job state this model must not perturb),
  [0023](0023-retry-and-backoff-policy-for-processing-jobs.md) (the
  failure classification this model is explicitly subordinate to).

## Context

Before Increment 6, a processed document's outcome was binary: the
document's status was `validated` or `failed`, and a job was `completed`
or `failed` — no representation existed for *how reliable* an extraction
was, only whether it passed validation. Increment 6 added confidence
computation (`ProcessingResult.confidence`, `.field_confidence`,
`.metadata`), but did so as an implementation, not a decision — nothing
fixed what these numbers are supposed to mean, how they relate to each
other, or (most importantly) what they are *not* allowed to influence.
That gap is exactly how a future contributor ends up quietly wiring
`low_confidence` into a retry decision or a state transition, at which
point ADR-0023's carefully-scoped failure classification stops being the
single source of truth for what happens to a job. This ADR closes that
gap: it is a semantic ADR, formalizing meaning for code that already
exists, not authorizing new implementation.

## Decision

### 1. Confidence has two levels, and the derivation runs stage → document → field

Two pipeline stages each already produce their own scalar confidence:
OCR (`ExtractionOutput.confidence`) and field extraction
(`FieldExtractionOutput.confidence`). This ADR fixes the direction data
flows between the levels:

- **Document-level confidence** (`ProcessingResult.confidence`) is the
  geometric mean of the two stage confidences
  (`_aggregate_confidence`) — not an aggregate of individual field
  scores. A geometric mean rather than an arithmetic mean is a
  deliberate choice: it lets one badly-failed stage pull the whole score
  down instead of being masked by averaging against a stage that
  happened to work (`sqrt(0.95 * 0.05) ≈ 0.22`, vs. an arithmetic
  mean's misleading `0.5`).
- **Field-level confidence** (`ProcessingResult.field_confidence`) is
  *derived from* document-level confidence, not the other way around:
  each present field's score is the document-level confidence scaled by
  a plausibility heuristic for that field
  (`_field_plausibility` — e.g. does a `date_of_birth` value look
  date-shaped). It is a per-field *view* of the same underlying signal,
  not an independent measurement — neither the OCR stage nor the field-
  extraction stage returns real per-field confidence today, and this ADR
  does not claim otherwise.

Any future change that makes a field-extraction backend return genuine
per-field scores (e.g. per-tool-call-argument log-probabilities) would
reverse this derivation — document-level becoming an aggregate of real
field-level scores — and should be recorded as a revision to this ADR,
not a silent reinterpretation of it.

### 2. Confidence and validation are orthogonal axes

Validation (`ValidationOutput.is_valid`) answers "is the extracted data
complete and safe to use" — a correctness gate. Confidence answers "how
reliable was the process that produced it" — an uncertainty signal. They
are computed independently (validation from `RequiredFieldsValidator`/
`PHIDetectionValidator` against the extracted fields; confidence from
the OCR/field-extraction stage scores) and this ADR fixes that all four
quadrants are legitimate, representable outcomes, not edge cases to be
collapsed:

| | High confidence | Low confidence |
|---|---|---|
| **Valid** | The common case: a clean scan, all required fields found. | All required fields present, but from a noisy scan or a weak field-extraction pass — worth a second look despite passing validation. |
| **Invalid** | A confidently-extracted document that is genuinely missing a required field (or was correctly PHI-gated) — the process worked; the content didn't qualify. | Both the process and the outcome are suspect. |

### 3. Confidence signals live in metadata only, and are read-only with respect to state

`ProcessingResult.metadata`'s `low_confidence`, `low_confidence_fields`,
and `issue_categories` (`_build_metadata`) are the *entire* surface
confidence exposes. This ADR fixes that this surface is, and remains,
informational:

- It does not gate, delay, or duplicate any transition in
  [0020](0020-document-and-job-state-machines.md)'s document/job state
  machines.
- It does not participate in
  [0023](0023-retry-and-backoff-policy-for-processing-jobs.md)'s
  transient/terminal/not-a-failure classification —
  `_is_transient_field_extraction_error` has no dependency on confidence,
  and this ADR fixes that it must stay that way. `issue_categories`
  including `"uncertain_extraction"` is a **reporting label**, read off
  existing validator issue text and a confidence threshold — it is
  emphatically **not** a fourth ADR-0023 failure bucket, and must never
  be treated as one (e.g. by making low confidence a retry trigger).
- It does not affect [0024](0024-stale-job-recovery-worker-crash.md)'s
  stale-job reclamation, which operates purely on elapsed time since a
  job's last write.

Confidence can inform a future human-review or ranking workflow (Sprint
3 baseline's own observability goal — data that already exists becoming
queryable, not a new collection mechanism); it cannot, today or in any
change built on this ADR alone, change what the system *does* with a
job or document.

### 4. The confidence-affects-nothing rule requires an explicit override, not a workaround

If a genuine future need arises to let confidence influence behavior
(e.g., auto-routing low-confidence documents to a review queue, or
requiring a second opinion before `validated`), that is a new decision
requiring its own ADR — one that would need to explain why an
informational signal is becoming a behavioral one, and update this ADR's
"read-only" framing explicitly rather than have a code change quietly
outrun the documented model.

## Consequences

- **Positive:** confidence is now a named, structurally-fixed concept —
  future work (ranking, filtering, review queues) has a stable contract
  to build against instead of ad hoc numbers scattered across
  `ProcessingResult`. The stage → document → field derivation direction
  is now explicit, preventing a future contributor from assuming (or
  building tooling around the assumption) that field-level confidence is
  independently measured.
- **Negative:** a second axis of meaning (confidence, alongside
  validity) is a real increase in what a caller of `ProcessingResult`
  needs to understand — mitigated by keeping confidence strictly
  informational (section 3), so nothing *breaks* by ignoring it, only by
  acting on it incorrectly.
- **No implementation changes.** This ADR is purely semantic, per its own
  constraint — it names and fixes the meaning of behavior Increment 6
  already built (`modules/processing/pipeline.py`); it does not modify
  OCR, prompting, validation rules, or storage/schema, and does not
  reopen ADR-0023 or ADR-0024.
- **Explicitly out of scope:** OCR quality changes, LLM prompting
  changes, validation rule changes, and schema changes — all Increment 6
  concerns already settled elsewhere, not reopened here.
