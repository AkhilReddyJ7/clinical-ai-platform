# 0018: Evaluated Microsoft Presidio for PHI detection; not adopting yet

- **Status:** Accepted
- **Date:** 2026-07-03
- **Follows up on:** [0008](0008-lightweight-regex-phi-detection-not-presidio.md),
  [0015](0015-phi-detection-re-evaluation-and-pattern-expansion.md)

## Context

0008 deferred Presidio, naming "once real OCR exists" as the point to
revisit. 0015 quantified the current regex detector's gap (4/17 realistic
PHI-shaped test cases caught, 24%) and identified names/addresses as the
two biggest remaining blind spots — exactly what NER-based detection would
address. Before implementing anything, installed `presidio-analyzer` +
spaCy in a throwaway environment and measured it directly against a
synthetic clinical note, rather than relying on commonly-cited numbers.

Two findings changed the expected conclusion:

- **Docker image impact is larger than assumed, and Presidio's own
  default reaches further than expected.** `presidio-analyzer` + spaCy +
  the *small* English model (`en_core_web_sm`) measured **299MB**
  installed. Presidio's default `AnalyzerEngine()` configuration, if not
  explicitly pinned to the small model, downloads and loads
  `en_core_web_lg` instead — another **382MB**, ~680MB total — with a
  measured **35-second** cold engine-init time. For comparison, Tesseract
  ([0010](0010-real-local-ocr-via-tesseract-fields-stay-synthetic.md))
  added roughly 50-100MB.
- **Accuracy is not a clean win over the existing regex detector.** With
  the small model (the realistic deployment choice given the size
  finding above): correctly caught the person name and email, but
  **missed the SSN entirely** — reproduced directly, including with
  `score_threshold=0.0` and explicit `entities=["US_SSN"]` filtering, even
  though the registered `UsSsnRecognizer`'s own regex pattern matches the
  test string when checked in isolation. It also produced real
  false-positive noise: "Ridge Lane" tagged `PERSON`, "IL 62704" tagged
  `ORGANIZATION`, the literal words "SSN" and "MRN" tagged `ORGANIZATION`.
  Net effect: Presidio helps with names (the real, expected win) but is
  not strictly better than the current regex detector without real tuning
  investment — it misses a case the existing code already catches
  reliably.

## Decision

**Do not adopt Presidio now.** The current regex-based
`PHIDetectionValidator` stays as-is. This isn't "regex is good enough
forever" — it's that the measured cost (image size, latency, tuning
burden to fix the false positives/negatives found above) isn't justified
against the measured benefit (partial, unverified accuracy improvement)
at this project's current stage, where the primary PHI safeguard remains
policy ("never upload real patient data," structurally true through most
of Sprint 1, now policy-enforced since real OCR landed) rather than
detection.

Considered and rejected alternatives:
- **Hand-rolled spaCy NER** (skip Presidio's framework): same model, same
  image cost, same tuning problems, but you also build the recognizer
  orchestration Presidio already provides. Strictly worse than adopting
  Presidio properly — not worth it in isolation.
- **Cloud DLP / clinical NLP APIs** (AWS Comprehend Medical, Google Cloud
  DLP, Azure Text Analytics for Health): likely the best raw accuracy,
  zero image impact, but requires the same category of vendor/credential
  decision already deferred for OCR and LLM extraction — and worse, means
  sending potentially-PHI-shaped content to a third party specifically to
  find out if it's PHI, a bigger trust-boundary commitment than anything
  else in this project.
- **LLM-based PHI detection**: plausible accuracy, but gated on the same
  pending LLM-provider decision as field extraction — revisit *together*
  with that decision rather than as a separate dependency.

## Consequences

- No code changed. `modules/validation/phi.py` and its documented
  limitations (README, 0008, 0015) stand as accurate.
- If Presidio is revisited later, the scope is not "add the dependency" —
  it's: pin `en_core_web_sm` explicitly (never let it reach for the 382MB
  large model by default), add a custom recognizer to close the SSN gap
  found here, and budget real time for confidence-threshold tuning against
  the false positives measured above. Documented so a future attempt
  starts from evidence, not a fresh assumption that Presidio is a drop-in
  upgrade.
- Sprint 2 effort redirects to LLM-based field extraction — the objective
  with no remaining viable local-only option, and the larger gap (fields
  are entirely synthetic today) versus PHI detection's narrower one
  (names/addresses specifically).
