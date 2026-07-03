# 0015: PHI detection re-evaluation and pattern expansion

- **Status:** Accepted
- **Date:** 2026-07-03
- **Follows up on:** [0008](0008-lightweight-regex-phi-detection-not-presidio.md), which named
  "once a real OCR backend lands" as the trigger to revisit this decision.
  It has (see [0010](0010-real-local-ocr-via-tesseract-fields-stay-synthetic.md)).

## Context

Ran the existing `PHIDetectionValidator` (SSN/email/phone patterns only)
against 17 constructed, synthetic-but-realistic PHI-shaped test cases —
not real patient data, fabricated examples covering the categories a real
clinical document could plausibly contain: names, addresses, dates of
birth, medical record numbers, credit card numbers, IP addresses, and
variations on SSN/phone formatting. Result: **4 of 17 caught (24%)**.
Quantifies what [0008](0008-lightweight-regex-phi-detection-not-presidio.md)
described qualitatively ("no NER, no name/address recognition").

## Decision

Split the gaps into three categories and handled each differently:

1. **Fixable with more regex, low false-positive risk — added:** IP
   addresses (`\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:...)){3}\b`, bounded to
   valid 0-255 octets so it doesn't fire on any dotted number sequence),
   credit card numbers (4-4-4-4 digit grouping, with or without
   separators), and space-separated SSNs (extended the existing SSN
   pattern's separator character class from hyphen-only to hyphen-or-space).
   Zero new dependencies — same regex-list architecture as before.

2. **Theoretically regex-matchable, deliberately left out — would trade
   signal for noise:** generic dates (a clinical note typically has
   several non-DOB dates — visit date, admission date, follow-up date;
   flagging all of them makes the guardrail noisy enough to be ignored),
   age+zip-code combinations (distinguishing "age" from "any other number
   near a zip code" needs context a regex can't evaluate), and unformatted
   bare digit runs for SSN/phone (a 9- or 10-digit number with no
   separator is indistinguishable from countless benign IDs — invoice
   numbers, tracking numbers — without surrounding context this
   architecture doesn't analyze).

3. **Not regex-matchable at all — names and street addresses.** No
   reliable shape exists for either. This is exactly what NER (Microsoft
   Presidio + spaCy, as originally named in
   [0008](0008-lightweight-regex-phi-detection-not-presidio.md)) would
   add. **Not implemented here** — deliberately deferred as a decision for
   the project owner, not made unilaterally. It's a real dependency
   category: spaCy + a language model means meaningful build time, image
   size, and per-call latency (NER inference is orders of magnitude slower
   than regex matching) — comparable in weight to the OCR vendor decision
   that was explicitly discussed before implementing
   [0010](0010-real-local-ocr-via-tesseract-fields-stay-synthetic.md), not
   something to add silently alongside a pattern-list update.

## Consequences

- Re-ran the same 17 test cases after the pattern additions: SSN
  (space-separated), IP address, and credit card (3 separator variants)
  now correctly caught; person names, street addresses, dates, and
  unformatted digit runs remain uncaught, on purpose. Also verified no
  false positives introduced: invalid-octet number sequences
  (`999.999.999.999`), the mock pipeline's synthetic output, and plain
  PHI-free clinical prose all still pass cleanly.
- Verified end-to-end against the live compose stack, not just unit
  tests: a real image with an OCR-readable credit-card-shaped number is
  correctly flagged and redacted before persistence — the full chain from
  [0010](0010-real-local-ocr-via-tesseract-fields-stay-synthetic.md)
  (real OCR) through [0011](0011-phi-detection-gates-persistence.md)
  (gates storage) working together against the newly-added pattern.
- **Person names and street addresses remain the two largest gaps.**
  README and the validator's own docstring updated to say so plainly —
  this was already documented as a limitation, now it's a *quantified*
  one, and adding IP/credit-card coverage doesn't change the fact that
  the biggest realistic PHI categories are still unaddressed.
- The NER/Presidio question is still open, now with concrete evidence
  behind it rather than a theoretical "someday." That's a deliberate
  stopping point, not an oversight.
