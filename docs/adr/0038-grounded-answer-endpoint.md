# 0038: Grounded Q&A endpoint — the first synchronous in-request LLM call

- **Status:** Accepted
- **Date:** 2026-07-05
- **Follows up on:** [0019](0019-anthropic-field-extraction-and-phi-gates-llm-call.md)
  (the Anthropic call pattern this mirrors),
  [0025](0025-confidence-and-quality-semantics-model.md) (binding: nothing
  here gates on confidence),
  [0035](0035-retrieval-api-shape-and-phi-safety-at-the-retrieval-boundary.md)
  (the retrieval API and PHI reasoning this extends),
  [0036](0036-guardrails-hardening.md) (the injection-defense precedent),
  [0037](0037-retrieval-quality-evaluation.md) (whose no-answer score data
  informs the abstention design).

## Context

Retrieval (ADR-0033–0035) returns ranked chunks; nothing turns them into
an answer. Phase C's title says "Retrieval-Augmented Generation" but its
concrete scope never included the generation step — the "G" was named,
not built. This ADR adds `POST /retrieval/answer`: retrieve chunks for a
question, generate an answer grounded in them via the Anthropic API, and
cite the source documents. Per the Phase D baseline note, the raw
Anthropic SDK is the production path — the LlamaIndex implementation
stays an isolated demo (`demos/llamaindex_rag/`).

## Decision

### 1. The call is synchronous and in-request — a deliberate break

Every prior LLM call runs inside the worker (ADR-0021–0024): documents
are durable state, extraction failures need retry classification, and a
job queue is the right home for both. A grounded answer is neither — it
is a **read**. Nothing is persisted, there is no state machine to
advance, and a failed answer has no retry semantics worth honoring (the
caller just asks again). Routing it through enqueue-and-poll would make
the cheapest interaction in the system the slowest, for no durability
benefit. Exposure is bounded on both axes: latency by
`anthropic_timeout_seconds` (30 s), cost by `answer_max_context_chars`
(12 000) on input and `max_tokens=1024` on output. Both blocking calls
(retrieval, generation) run via `run_in_threadpool`.

### 2. Citation-by-index: the model never sees a document id

The prompt presents chunks as numbered passages (`BEGIN CONTEXT n` /
`END CONTEXT n`) with **no document or extraction ids**; the forced tool
call (`record_grounded_answer`, same tool-forcing pattern as ADR-0019)
returns `cited_context_numbers`. The route resolves numbers back to real
chunk identity server-side. A model cannot hallucinate a valid-looking
`document_id` it was never shown — the worst possible citation failure
is a wrong *number*, which is bounded and checkable. Out-of-range,
non-integer, or duplicate citations are **dropped, not errors**:
citations are informational (ADR-0025's posture), and a good answer must
never be destroyed over one bad citation. `GeneratedAnswer`'s contract
(`modules/retrieval/answer_base.py`) guarantees in-range deduplicated
indices, so the route indexes into its chunk list without re-validation.

Citations reuse `RetrievedChunkOut` including `chunk_text` — the exact
payload `POST /retrieval/query` already exposes, so no new data surface.

### 3. Abstention is a first-class 200, at two layers

- **Empty corpus, deterministic**: if retrieval returns nothing, the
  route answers `insufficient_context: true` with a fixed string and
  **never calls the LLM** — an empty corpus is a normal state, and a paid
  API round trip cannot improve on "there is nothing to ground in."
- **Model-side**: `insufficient_context` is a *required* boolean in the
  tool schema; the system prompt and tool description both instruct
  abstain-over-guess.

ADR-0037 §5 measured the gap this vocabulary anticipates: no-answer
queries' top-1 cosine scores (0.59–0.63) sit visibly below genuine hits
(~0.7+). A retrieval-side score threshold that abstains *before* the LLM
call is the natural future increment; this ADR builds the response
vocabulary for abstention without building that threshold.

### 4. Error mapping — the first LLM-error → HTTP contract

| Condition | Status | Detail |
|---|---|---|
| missing/invalid `X-API-Key` | 401 | existing `require_api_key` |
| blank/oversized question, `top_k` < 1 | 422 | pydantic |
| `top_k` > `retrieval_max_top_k` | 422 | router (shared with `/query`) |
| empty retrieval results | 200 | `insufficient_context: true`, no LLM call |
| model abstains | 200 | `insufficient_context: true` |
| Anthropic key not configured | 503 | `answer generation is not configured` |
| rate limit / API / connection / malformed response | 502 | `answer generation failed` |

The 503/502 split mirrors `require_api_key` (ADR-0026): a missing key is
a deterministic operator misconfiguration — retrying won't help and it
should read differently in monitoring than an upstream flake.
`AnswerGenerationNotConfiguredError` subclasses `AnswerGenerationError`
so a route catching only the parent still fails to 502, never 500.
Detail strings are **static**: SDK exception text can embed request
content and is logged server-side only, never echoed to callers. The
generator itself keeps ADR-0019's construction contract — no raise on an
empty key at `__init__` (it is an `lru_cache` dependency), fail closed
per call.

### 5. PHI posture — ADR-0035 §4 extends unchanged

No PHI check on the question text, and none on the answer: the answer is
generated exclusively from chunks that are only indexed after both PHI
gates (VALIDATED documents, ADR-0035), and every cited chunk is already
readable verbatim via `POST /retrieval/query`. The injection surface is
real — chunk text originates in uploaded documents — so the
data-not-instructions defense runs in both channels (system prompt and
tool description), the same belt-and-suspenders ADR-0036's adversarial
cases exercise for extraction.

### 6. Question length is validated, not truncated

`question` is capped at 2 000 chars via schema validation (422).
Extraction truncates its input (ADR-0016 posture) because a document's
head is still useful; a question is not a document — truncating it
changes what is being asked, so an oversized one is rejected instead.
Context chunks, by contrast, are budget-packed: whole chunks in rank
order until `answer_max_context_chars` is exhausted (prefix packing
keeps citation numbers aligned with the ranked list head; if the single
top chunk exceeds the budget it is truncated rather than sending zero
context).

## Deliberately not done

- **No streaming** — the forced tool call returns one shaped block;
  streaming would complicate the citation contract for a demo-stage UX
  win.
- **No reranking** — ADR-0035 §6's deferral stands; this consumes the
  existing single-stage ranking.
- **No answer-quality eval** — groundedness/citation-precision metrics
  are the natural ADR-0037 follow-up, not part of shipping the endpoint.
- **No conversation memory** — single-turn by design; a session model is
  a product decision, not an increment of this one.
- **No retrieval-score abstention threshold** — see §3; needs its own
  decision against ADR-0037's data.
- **No per-answer confidence score** — ADR-0025 remains binding; the
  only self-assessment surfaced is the boolean `insufficient_context`.

## Consequences

The RAG story is complete end to end: upload → OCR → PHI gate →
extraction → validation → indexing → retrieval → **grounded, cited
answer** (`demos/run_e2e_demo.sh` step 6). The cost is a new operational
posture: API latency and Anthropic spend are now caller-facing on one
route, bounded but no longer absorbed by the worker. If interactive load
ever matters, the pressure points are known — caching, streaming, and
the retrieval-score short-circuit above.
