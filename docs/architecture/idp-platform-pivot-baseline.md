# IDP/RAG Platform Pivot: Design Baseline

- **Status:** Approved — frozen as the project's pivot baseline
- **Date approved:** 2026-07-04
- **Scope:** Program-level, spanning several future sprints. This is not
  itself a sprint design baseline — each phase in section 4 still gets
  its own `sprint-N-design-baseline.md` and its own ADRs before
  implementation, per the process used throughout Sprints 1-4. What this
  document does is set direction: it supersedes the specific "out of
  scope" lines in the Sprint 3 and Sprint 4 baselines that ruled out RAG,
  vector databases, embeddings, evaluation frameworks, and fine-tuning —
  see section 2.

This document is frozen as written at approval time. Corrections or
revisions, if any become necessary, are appended as dated notes (matching
this project's ADR convention) rather than silently edited.

---

## 1. Why This Pivot, and What Changes

Sprints 1-4 deliberately built a narrow, correct core first: single-
document ingestion → OCR → PHI gate → LLM field extraction → validation,
made production-shaped by a durable state machine, an async worker,
retry/backoff, named identity, and read APIs over audit and metrics. At
every decision point, the project chose the boring, dependency-minimal
option and justified it in an ADR rather than reaching for infrastructure
common in this space by default (no Redis/Celery, no Kafka, no Presidio,
no additional LLM providers).

That sequencing was correct for getting a durable foundation right. But
it also means the project, as scoped through Sprint 4, is a single-
document extraction pipeline — not yet a platform that resembles what
real Intelligent Document Processing (IDP) systems at healthcare
companies actually own: retrieval across a corpus, evaluation of
accuracy and hallucination, and traceable/versioned data lineage
supporting reprocessing and backfill. Those capabilities were correctly
deferred while no concrete need existed for them. That has changed: the
explicit goal now is to build a project whose architecture and decisions
resemble what an IDP/RAG-focused healthcare startup builds and hires
for, evaluate it honestly, and be able to talk through the tradeoffs in
an interview — that is a concrete, stated need, not a hypothetical one.

**What does not change:** the modular-monolith structure (ADR-0001), the
ADR-per-decision discipline, the "boring infra until justified"
philosophy, and everything already shipped through Sprint 4 (ingestion,
OCR, extraction, validation, processing pipeline, audit, metrics) remains
the system of record. This pivot adds capability layers on top of that
foundation; it does not replace it. A future contributor should not read
this document as "Sprint 1-4 was the wrong direction" — it was the right
foundation, built in the right order. This document is about what gets
built *on* it now that the target has become concrete.

## 2. What This Supersedes

- Sprint 3 baseline, section 11 ("Explicitly Out of Scope for Sprint 3")
  listed RAG, search, vector databases, embeddings, and analytics as out
  of scope "this sprint," justified by "no concrete need exists yet."
  Analytics was resolved on its own terms by ADR-0029. RAG, search,
  vector databases, and embeddings are superseded here: the need is now
  explicit.
- Sprint 4 baseline, section 3.4, carried the same RAG/search/vector-
  database/embeddings line forward, plus explicitly deferred persisted
  per-stage duration analytics (unaffected by this pivot — still not
  needed) and per-key management endpoints (also unaffected).
- Evaluation frameworks, orchestration-framework literacy, and fine-
  tuning were never discussed in any prior baseline — this is new
  territory being opened, not a prior decision being reversed.
- A short dated note is being appended to both prior baselines pointing
  here, per this project's own convention that frozen documents are
  corrected by addition, not silent edit.

## 3. Reference Point

The shape of this pivot is deliberately anchored to what real IDP-
focused healthcare startups hire senior AI engineers to own: end-to-end
pipeline ownership (ingestion through workflow automation), RAG system
design (chunking, embeddings, reranking), structured/versioned/traceable
data with reprocessing and backfill support, evaluation frameworks for
accuracy and hallucination reduction, and familiarity with orchestration
tooling (LangChain/LlamaIndex-style) alongside custom-built pipelines.
That's used here only as a directional reference for what capabilities
are worth building and why — not as a specification of any one company's
internals.

## 4. Phased Roadmap

Each phase below is a candidate future sprint, ranked by priority. As
with the Sprint 3 epic ranking, later phases depend on earlier ones
either technically or because they're unmeasurable without them.

### Phase A — Evaluation & Verification Harness (highest priority)

**Objective:** a real, repeatable way to measure whether the pipeline is
*correct*, not just whether it runs. Today there is no evaluation
harness at all — extraction accuracy, PHI-detection recall/precision,
and hallucination rate are all unmeasured. This is also the phase that
finally discharges the live-Anthropic-credentials verification deferred
since Sprint 2: a real accuracy measurement requires the real API, not
`MockFieldExtractionPipeline`.

Concrete scope:
- A labeled evaluation set (synthetic — see section 6) with known-
  correct field values and known-injected PHI-shaped strings.
- A scoring harness: field-level precision/recall/exact-match for
  extraction, recall for PHI detection against the injected cases.
- A report artifact (CLI output or a committed report), not a dashboard
  or monitoring stack — the same "no infra beyond what's justified"
  restraint that shaped the metrics API (ADR-0029).

Needs its own ADR: eval-set format and storage, scoring methodology
(custom harness vs. adopting something like RAGAS's approach), and
whether any pass/fail threshold gates anything (e.g., CI) or is purely
reported.

### Phase B — Document-Derived Data Lifecycle: Versioning, Lineage, Reprocessing/Backfill

**Objective:** convert today's "one more row per attempt" model into a
traceable, versioned lineage model — directly the "own the document-
derived data lifecycle" capability this pivot targets. Sequenced second,
ahead of RAG, because a corpus worth retrieving over should already have
traceable provenance before retrieval is built on top of it.

Concrete scope:
- Extend the existing `ExtractionResult`/`ValidationResult` model with
  an explicit version/lineage concept: reprocessing a document produces
  a new version with a recorded pointer to what it superseded and why
  (pipeline upgrade, manual reprocess, backfill).
- A backfill/reprocess entry point that can re-run the pipeline against
  already-ingested documents at a new pipeline version, distinct from
  today's "resubmit for processing" path.

Needs its own ADR: version/lineage schema shape, how backfill is
triggered, and how it interacts with the existing Document/Job state
machines (ADR-0020) without conflating "a new version exists" with "a
new job attempt happened" — the same kind of independence-of-concepts
question ADR-0020 itself resolved for document vs. job status.

### Phase C — Retrieval-Augmented Generation

**Objective:** enable retrieval across the document corpus, not just
single-document extraction — chunking, embeddings, a vector store,
search, and reranking. Sequenced third: retrieval quality is only
meaningfully evaluable once Phase A's harness exists, and a corpus worth
retrieving over is more useful once Phase B gives it real provenance.

Concrete scope (each a real decision, not a default):
- **Vector store choice** — evaluate `pgvector` (stays inside the
  existing Postgres, one fewer moving part, consistent with this
  project's minimal-infra pattern) against a dedicated store like Chroma
  (more RAG-idiomatic, worth knowing directly) before defaulting to
  either.
- **Chunking strategy** fit for clinical notes specifically, not a
  generic default.
- **Embeddings model choice** — itself subject to Phase A's evaluation
  harness, not chosen by reputation alone.
- **Retrieval API** — a new route (or routes), following this project's
  existing conventions (pagination envelope, `require_api_key` gate).
- **PHI safety at the retrieval boundary** — retrieval is a new surface
  where PHI could leak if it isn't gated the same way ingestion already
  is (ADR-0011, ADR-0019); this cannot be an afterthought.
- Reranking is a stretch goal within this phase, not a blocking
  requirement.

Needs its own ADR (likely more than one, given the number of independent
decisions above).

### Phase D — Orchestration-Framework Literacy Demo

**Objective:** demonstrate familiarity with LangChain/LlamaIndex-style
orchestration without adopting either as the core dependency. The
existing pipeline's raw-SDK-plus-tool-calling approach (ADR-0019,
`modules/extraction/anthropic_extractor.py`) is a deliberate, already-
justified choice and stays the production path.

Concrete scope: a small, clearly separated demo — its own directory, its
own README — reimplementing a reduced version of the RAG flow (Phase C)
with a framework such as LlamaIndex, explicitly labeled as a comparison/
demo, not production code. Lower rigor than Phases A-C: a short written
rationale for keeping it separate is enough, a full ADR is not required.

### Phase E — Guardrails Hardening

**Objective:** extend today's guardrail baseline (the tool-forced
extraction schema, ADR-0019; the PHI regex gate, ADR-0008/0015/0018)
with something closer to the multi-pass, hallucination-reducing
validation a mature IDP pipeline needs — e.g., a verification/self-
consistency pass, confidence-based escalation, or adversarial test
cases exercising the extraction and PHI-detection paths deliberately.
Lower priority than A-C: today's guardrail baseline is real, if partial,
and this phase sharpens it rather than closing an absence.

Needs its own ADR once concretely scoped.

### Phase F — Fine-Tuning Demo (optional, lowest priority)

**Objective:** a small, portfolio-value demonstration of LoRA/adapter
fine-tuning familiarity — explicitly *not* adopted in the production
pipeline. A well-prompted frontier model with tool-calling (the existing
approach) is expected to outperform a fine-tuned smaller model on this
task, and fine-tuning on clinical data would introduce a training-data
governance and model-hosting surface disproportionate to this project's
scale. Scope: an isolated notebook/script, synthetic data only, clearly
labeled as exploratory, not a pipeline dependency.

## 5. Still Explicitly Out of Scope

Unchanged by this pivot — no new justification has appeared for any of
these, and this pivot's own reference point (section 3) treats them as
JD-familiarity bullets, not requirements at this project's actual scale:

- True multi-tenant SaaS (tenant isolation, per-tenant billing/limits)
- OAuth / SSO / RBAC
- Kafka / SNS / SQS or any event broker
- Additional LLM providers beyond Anthropic
- Kubernetes/ECS orchestration, Snowflake/Databricks-scale analytical
  stores

If a concrete need for any of these appears later, it gets the same
treatment every other decision in this project has: its own ADR, not a
silent addition.

## 6. Data Sourcing

**Primary and default: synthetic data via [Synthea](https://synthea.mitre.org/)**
or an equivalent synthetic patient-record generator. Zero licensing
friction, purpose-built for exactly this kind of development and
portfolio work, and safe to reference (and to commit small generated
samples from) in a public repository.

**Real de-identified clinical corpora (MIMIC-III/IV, n2c2/i2b2 NLP
challenge sets) are not ruled out, but are not the default**, and come
with binding constraints if used:
- Access requires PhysioNet (or equivalent) credentialing and a signed
  data use agreement — not a casual download, regardless of the data
  being de-identified and "available online."
- DUAs typically prohibit redistribution — if used, the data is never
  committed to this repository, only pulled locally by a script under
  the user's own credentialed access, with the credentialing step
  documented, not hidden.
- If pursued, this is worth doing deliberately and for its own stated
  reason (e.g., demonstrating the ability to operate correctly under a
  real DUA), not for convenience over Synthea.

This decision is binding across every phase in section 4 unless revised
by a dated note.

## 7. ADRs Likely Required

- *"Evaluation harness: dataset format, scoring methodology, thresholds"*
  (Phase A).
- *"Extraction/validation versioning and lineage schema"* (Phase B).
- *"Reprocessing and backfill triggering"* (Phase B).
- *"Vector store selection for retrieval"* (Phase C).
- *"Chunking and embeddings strategy for clinical documents"* (Phase C).
- *"Retrieval API shape and PHI safety at the retrieval boundary"*
  (Phase C).

Phases D and F are explicitly scoped to not require a full ADR (sections
4D, 4F); Phase E gets one once it's concretely scoped, not before.

## 8. Recommended Sequencing

1. **Phase A (evals)** first and independently — it has no dependency on
   anything else in this pivot, it's the cheapest to build, and every
   later phase's quality claims (retrieval relevance, guardrail
   effectiveness) are unmeasurable without it.
2. **Phase B (lineage/versioning)** second — establishes traceable
   provenance on the corpus before Phase C builds retrieval on top of
   it.
3. **Phase C (RAG)** third — evaluated against Phase A's harness, built
   over a corpus with Phase B's provenance.
4. **Phases D, E, F** afterward, in any order, as time/interest permits —
   none of them block or are blocked by each other, and none carry the
   same urgency as A-C.

## 9. Risks and Trade-offs

- **Scope-creep risk is real and specific to this pivot.** A roadmap
  spanning evals, lineage, RAG, an orchestration demo, guardrails, and a
  fine-tuning demo is easy to over-scope. Each phase must be held to the
  same discipline Sprint 3 and 4 already demonstrated — small, ADR-
  bounded increments, not a single sprawling implementation pass.
- **Skipping or shortcutting Phase A defeats the purpose of every phase
  after it.** If evals are treated as optional, RAG quality and
  guardrail effectiveness become exactly the kind of unmeasured claim
  this project has already been burned by once (the Anthropic
  extraction pipeline, deferred and unverified across three sprints).
- **Synthetic data has limits.** Synthea-generated notes are structurally
  realistic but may not stress the PHI detector or extraction pipeline
  the way genuinely messy, inconsistent real clinical text would. Phase
  A's harness is only as good as its data — this is a real, acknowledged
  gap, only partially closed by choosing synthetic data over no data.
- **This pivot does not retroactively fix the PHI-detection gap already
  on record** (person names and street addresses are undetected by
  design, per `modules/validation/phi.py`'s own docstring and ADR-0008/
  0015/0018). Phase A's eval harness will make that gap *measurable*; it
  does not by itself close it. Closing it remains a separate decision
  Phase E could take up, not something this pivot resolves implicitly.

## Decisions Signed Off At Approval

- This pivot is approved: the project explicitly adopts IDP/RAG-platform
  scope, superseding the RAG/search/vector-database/embeddings lines in
  the Sprint 3 and Sprint 4 baselines (section 2).
- Sequencing is approved as stated in section 8: evals, then lineage/
  versioning, then RAG, then the lower-priority phases in any order.
- Synthetic data (Synthea or equivalent) is approved as the default and
  primary corpus; real de-identified datasets require explicit
  credentialing per section 6 and are never committed to this
  repository.
- Each phase still requires its own sprint design baseline and its own
  ADRs before implementation — this document sets direction and
  sequencing, not implementation detail.
- The existing Sprint 1-4 foundation is retained as-is; this pivot adds
  capability layers on top of it, it does not revisit or replace it.
