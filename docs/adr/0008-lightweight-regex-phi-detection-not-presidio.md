# 0008: Lightweight regex-based PHI detection, not Presidio

- **Status:** Accepted
- **Date:** 2026-07-03

## Context

Sprint 2 needed a first PHI-detection pass, explicitly required by the
original constraints ("extendable for future ... PHI detection"). The
robust, industry-standard approach (Microsoft Presidio or similar) uses
spaCy NER models — a real dependency: tens of megabytes, a language model
to download, meaningful build-time and image-size cost. Nothing in the
system yet proves that weight is justified: the mock `ExtractionPipeline`
never produces real content (see
[0002](0002-interface-first-pipeline-stages.md)), so there is no live path
today for real PHI to reach this check.

## Decision

Implement `modules.validation.phi.PHIDetectionValidator` as a lightweight
regex pattern matcher (SSN-shaped numbers, email addresses, phone numbers)
against `ExtractionOutput.raw_text`. Compose it with the existing
`RequiredFieldsValidator` via a new `CompositeValidationPipeline`
(`modules/validation/composite.py`) rather than replacing it — data
completeness and PHI safety are different concerns with different failure
modes, and keeping them as separate, independently-tested validators avoids
one growing to absorb the other's responsibility.

`get_validation_pipeline()` in `apps/api/dependencies.py` now constructs the
composite; no router or endpoint changes were needed, since
`CompositeValidationPipeline` also implements `ValidationPipeline`.

## Consequences

- Zero new dependencies, no image size or build time cost.
- This is explicitly a guardrail against *accidental* obvious PHI (a
  malformed input, a copy-paste mistake once real OCR exists), not a
  compliance control and not comprehensive — no NER, no name or address
  recognition, no clinical-context awareness. Documented plainly in the
  README's constraints section to avoid ever implying otherwise; this
  project already commits to no HIPAA-compliance claim.
- Genuinely testable only at the unit level today: since the mock
  extraction pipeline never echoes real input content into `raw_text` (by
  design — see [0002](0002-interface-first-pipeline-stages.md)), there is
  no way to exercise "PHI gets flagged" through the real HTTP upload/process
  flow without either violating the no-real-PHI-in-fixtures constraint or
  changing the mock's deliberately content-independent behavior (out of
  scope here). Integration-level coverage instead confirms the composite
  pipeline is correctly wired and doesn't false-positive against the mock's
  synthetic output.
- Revisit this decision (toward Presidio or an equivalent) once a real OCR
  backend exists and actually produces content that could contain real PHI
  — that's the point at which the lightweight heuristic's limitations
  become a live risk rather than a theoretical one.
