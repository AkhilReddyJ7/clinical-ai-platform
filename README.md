# Clinical AI Intelligence Platform

[![CI](https://github.com/AkhilReddyJ7/clinical-ai-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/AkhilReddyJ7/clinical-ai-platform/actions/workflows/ci.yml)

A document intelligence platform for clinical documents: upload, track, and run
documents through an extraction + validation pipeline.

This is an early-stage local MVP. It is **not** HIPAA-compliant. Extracted
text is real (read from whatever you upload), and structured fields are now
real too — extracted via the Anthropic API (see
[Status & constraints](#status--constraints)) — **never upload real patient
data**.

## Architecture

```
apps/
  api/            FastAPI app: HTTP layer, routing, dependency wiring
  worker/         background worker process entrypoint (docker-compose's
                   `worker` service) -- claims and runs jobs queued by
                   POST /process; see modules/processing/

modules/
  ingestion/      document registry, upload handling, storage abstraction
  ocr/            OCR pipeline interface: bytes -> raw_text; real text via
                   local Tesseract OCR (images/PDF) or passthrough
                   (text/plain) (+ mock for tests)
  extraction/     field-extraction pipeline interface: raw_text -> structured
                   fields, via the Anthropic API (+ mock for tests)
  validation/     validation pipeline interface (required-fields rule +
                   PHI-pattern guardrail, composed together)
  auth/           named API-key auth (X-API-Key header, ADR-0026) on
                   /documents/*
  audit/          who-did-what-when recording (ADR-0027); no query API yet
  analytics/ indexing/ layout/ search/         reserved for future work

shared/
  config/         centralized Settings (env-driven)
  logging/        centralized logging setup
  database/       SQLAlchemy async engine/session, declarative Base

alembic/          schema migrations (see Migrations, below)
```

Each pipeline stage is defined as an abstract interface with a concrete
implementation behind it:

- `modules.ocr.base.ExtractionPipeline` — implemented in production by
  `TesseractExtractionPipeline`: real text via local Tesseract OCR
  (`image/png`, `image/jpeg`, `application/pdf` via page rasterization) or
  direct decode (`text/plain`, no OCR needed). Its own `fields` output is a
  synthetic placeholder (shared with `MockExtractionPipeline`, which stays
  wired into the test suite for speed) — not read by the pipeline anymore,
  see `modules.extraction` below. See `docs/adr/0010-...`.
- `modules.extraction.base.FieldExtractionPipeline` — a second, separate
  stage (`raw_text -> structured fields`), deliberately split from OCR
  (`bytes -> raw_text`) rather than folded into it. Implemented in
  production by `AnthropicFieldExtractionPipeline`: a forced tool call
  against the Anthropic API for reliably-shaped JSON output, single-provider
  by design (no provider-agnostic tree built in advance — see
  `docs/adr/0019-...`). `MockFieldExtractionPipeline` stays wired into the
  test suite. Requires `ANTHROPIC_API_KEY`; fails closed per-request
  (a clean `status: failed`, not a crash) when it's not configured.
- `modules.validation.base.ValidationPipeline` — implemented today by
  `RequiredFieldsValidator` (data completeness) and `PHIDetectionValidator`
  (regex-based guardrail for SSN/email/phone/IP-address/credit-card-shaped
  patterns), run together via `CompositeValidationPipeline`. A clinical
  rules engine, or a more robust NER-based PHI detector (e.g. Microsoft
  Presidio) for the two biggest known gaps — names and addresses — implements
  the same interface and composes in alongside them later; deliberately
  not added yet, see `docs/adr/0015-...`.
- `modules.ingestion.storage.StorageBackend` — implemented today by
  `LocalFileStorage`. An S3/GCS-backed implementation plugs in later without
  touching callers.

This keeps the upload → extract → validate flow swappable at each stage
without changing the API layer.

### Data model

- **Document** (`documents` table) — registry entry: filename, content type,
  size, storage key, and a processing `status`
  (`uploaded → processing → extracted → validated|failed`).
- **ExtractionResult** (`extraction_results` table) — output of the
  extraction pipeline for a document (raw text, structured fields,
  confidence).
- **ValidationResult** (`validation_results` table) — output of the
  validation pipeline (pass/fail + issues list).

Schema is managed with Alembic (`alembic/`), not `create_all` — see
[Migrations](#migrations).

## Quickstart (Docker Compose)

```bash
cp .env.example .env   # optional: local dev defaults work out of the box
docker compose up --build
```

This starts:
- `api` — FastAPI app on `http://localhost:8000`, with its own Docker
  healthcheck hitting `/health`. The image includes `tesseract-ocr` for
  real, local OCR — no API key or external vendor needed for OCR itself.
  `POST /documents/{id}/process` only enqueues a job here (ADR-0022) — it
  doesn't run the pipeline itself.
- `worker` — the background worker process (`apps/worker/main.py`) that
  actually claims and runs enqueued jobs: OCR → PHI gate → field
  extraction → validation. Same image as `api`, same uploads volume, no
  exposed port.
- `postgres` — Postgres 16, with a named volume for data and another for
  uploaded files (`uploads_data`, mounted at `/app/data/uploads`)

Structured field extraction (`raw_text` -> `fields`) does need a real
Anthropic API key: set `ANTHROPIC_API_KEY` in `.env` to enable it. Without
one, uploads still work and OCR still runs — the worker fails the document
cleanly with `status: failed` at the field-extraction step (no crash, no
partial writes), rather than silently succeeding with fake data.

Check it's up:

```bash
curl http://localhost:8000/health
```

`/health` checks live database connectivity (`SELECT 1`), not just that the
process is running — it returns `503` with `"status": "unhealthy"` if
Postgres is unreachable, so it's meaningful as a Docker/orchestrator health
probe rather than always reporting healthy.

## Authentication

Every `/documents*` endpoint requires an `X-API-Key` header; `/` and
`/health` do not (so orchestrator/monitoring probes don't need a
credential). The local dev default is `local-dev-key` (see `.env.example`);
override `API_KEYS` via `.env` for anything beyond local dev, and never
commit real keys.

As of ADR-0026, each configured key has a **name**: `API_KEYS` is
`label:key` pairs, comma-separated (`API_KEYS=alice:sk-...,bob:sk-...`) —
a breaking change from the earlier bare-key-list format. Every valid key
still has identical access (no per-key scoping/permissions — see
[Status & constraints](#status--constraints)); the label is resolved
per-request and available to route handlers and structured logs as
`caller`. As of ADR-0027, uploading a document or enqueuing a processing
job now durably records *who* did it, in a new `audit_log_entries` table
(`modules/audit/`) — deliberately five columns with no free-text field
(`caller`, `action`, `document_id`, `job_id`, `created_at`), so there's no
column capable of holding raw text or PHI-shaped content in the first
place. No query endpoint exists yet (`GET /audit` or similar) — that's
additive future work, not part of this ADR's scope.

```bash
curl -H "X-API-Key: local-dev-key" http://localhost:8000/documents
```

Missing or wrong key → `401`. No keys configured at all → `503` (fails
closed rather than silently allowing every request through). Enforced by
an ASGI middleware (`modules/auth/middleware.py`) that runs before
FastAPI's routing — not just a route dependency — specifically so an
unauthenticated caller can't force the server to receive a large request
body before being rejected. Measured directly: before this, an
unauthenticated 100MB upload took ~260ms to reject (the body was already
fully received); after, ~10ms. See `docs/adr/0017-...`.

This is deliberately named keys, not full accounts/sessions/OAuth (ADR-0026
bounds the scope explicitly) — every key still has identical access, and
there's no UI or multi-tenant requirement to justify more. The audit trail
(ADR-0027) records the resolved caller label against the two actions that
exist today (upload, enqueue); it does not add scoping/permissions, and
there's still no UI or query surface for it.

## Local development without Docker

Requires [uv](https://docs.astral.sh/uv/) and a running Postgres reachable at
`DATABASE_URL` (defaults to `postgresql+asyncpg://postgres:postgres@localhost:5432/clinical_ai`).

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn apps.api.main:app --reload
```

`text/plain` uploads work either way (pure passthrough, no OCR). To process
`image/*` or `application/pdf` uploads outside Docker, install Tesseract on
the host yourself (e.g. `apt install tesseract-ocr` / `brew install
tesseract`) — Docker Compose is the only path that installs it for you.

## Migrations

Schema changes go through Alembic — the app no longer creates tables
implicitly on startup. In Docker Compose, `alembic upgrade head` runs
automatically before `uvicorn` starts (see the `api` service `command` in
`docker-compose.yml`).

```bash
# apply all pending migrations
uv run alembic upgrade head
# or
make migrate

# after changing a model in modules/*/models.py, generate a migration
uv run alembic revision --autogenerate -m "add foo column to documents"
# or
make revision m="add foo column to documents"
```

Always review an autogenerated migration before committing it — Alembic
detects most model changes but not all (e.g. renames look like a drop + add).
`DocumentStatus` is stored as `VARCHAR` (`native_enum=False`), not a Postgres
`ENUM` type, specifically so new status values are a plain column-constraint
change rather than a `ALTER TYPE ... ADD VALUE` migration.

Tests don't go through Alembic — they build the schema directly from the
ORM models against an in-memory SQLite database (see
[Running tests](#running-tests)), which is faster and keeps tests independent
of migration history.

## Running tests

```bash
uv run pytest
# or
make test
```

Tests don't require Postgres, Docker, a Tesseract install, or a real
Anthropic API key — they run against an in-memory SQLite database, a
temp-directory storage backend, `MockExtractionPipeline`, and
`MockFieldExtractionPipeline`, all via dependency overrides
(`tests/conftest.py`). No test makes a real network call to Anthropic.
Coverage: health, upload (incl. size-limit rejection/boundary), registry
(list/get), pagination, status transitions, required-fields and
PHI-pattern validation (individually and composed), auth (missing/wrong/
correct key, fail-closed with no keys
configured), the real `TesseractExtractionPipeline`'s dispatch/
confidence-aggregation logic (`pytesseract` calls mocked — no binary
needed), and `AnthropicFieldExtractionPipeline`'s tool-call parsing and
error handling (the SDK's `messages.create` call mocked directly — rate
limits, API errors, and connection failures each translated into a clean
`FieldExtractionError`), plus true end-to-end tests proving: real
`text/plain` content reaches PHI detection *and* gets redacted before
persisting, *and* the field-extraction call is never made at all when PHI
is detected (asserted with a stand-in pipeline that fails the test if
invoked); a corrupted/mismatched-content-type upload fails cleanly
(`status: failed`, not stuck in `processing` behind a `500`); a
field-extraction failure fails cleanly too, while still persisting the
real (PHI-clean) `raw_text`; and an incomplete LLM response (missing a
required field) now genuinely fails `RequiredFieldsValidator` — unlike the
old synthetic fields, which always populated all three. Image/PDF OCR
itself is verified separately, in Docker — see Continuous integration,
below.

## Evaluation harness

`docs/adr/0030-evaluation-harness.md` — measures what the test suite
above doesn't: whether field extraction is actually *correct* against
known ground truth, and how well PHI detection recalls injected
PHI-shaped strings. Scores `eval/dataset/cases.jsonl` (15 hand-labeled
synthetic clinical notes) against a live pipeline run.

```bash
make eval                       # mock pipeline -- exercises the harness only, free, no credentials needed
make eval ARGS="--live"         # real AnthropicFieldExtractionPipeline -- requires ANTHROPIC_API_KEY, costs real API calls
make eval ARGS="--report-out eval/reports/run.json --fail-under 0.8"
```

Mock-mode field-extraction scores are expected to be near zero —
`MockFieldExtractionPipeline` deterministically hashes input text into
unrelated synthetic values, so it has no way to match the dataset's real
ground truth. That's not a regression: mock mode only proves the harness
itself runs end-to-end. PHI-detection scores are meaningful in both
modes, since PHI detection doesn't depend on which extraction pipeline is
selected. The real accuracy signal — and the thing that finally
discharges the live-Anthropic-credentials verification deferred since
Sprint 2 (`docs/adr/0019-...`) — only comes from `--live` with a real key
configured.

## Continuous integration

Every push and pull request runs `.github/workflows/ci.yml`, two jobs:

- **test** — `ruff check`, `black --check`, `mypy` (strict), then `pytest`,
  via `uv sync --locked` so CI fails if `uv.lock` drifts from
  `pyproject.toml`. No Postgres service needed — tests run against SQLite.
- **docker** — `docker compose build`, `docker compose up --wait` (fails the
  build if either container doesn't reach its healthcheck), a smoke test
  against `/health`, an assertion that the `api` container isn't running as
  root, a full upload → process smoke test against the live stack (auth
  header, real file write to the named volume, real Postgres), and a real
  Tesseract OCR check (runs a generated image through the real pipeline
  inside the built container, asserting OCR actually functions — not an
  exact-text match, which proved flaky against tiny test-image renders).
  This is the job that actually validates the thing this project ships —
  the Python-only `test` job wouldn't have caught a broken Dockerfile, a
  bad `docker-compose.yml`, the app regressing back to running as root, the
  named-volume permission bug (`docs/adr/0009-...`), or Tesseract silently
  missing/broken in the image (`docs/adr/0010-...`).

  No `ANTHROPIC_API_KEY` is provisioned in CI — deliberately, to avoid a
  live LLM call (and its cost) on every push. The upload → process smoke
  test therefore expects `status: failed` with a
  `"Anthropic API key is not configured"` issue, which still proves the
  full container/dependency wiring (OCR, PHI gate, field-extraction stage)
  works end-to-end via the same graceful failure path as any other
  extraction failure (`docs/adr/0012-...`). See `docs/adr/0019-...`.

## API examples

All `/documents*` calls below need `-H "X-API-Key: local-dev-key"` (or your
configured key) — omitted from response bodies for brevity, not from the
requests themselves.

**Upload a document**

```bash
curl -X POST http://localhost:8000/documents \
  -H "X-API-Key: local-dev-key" \
  -F "file=@sample_note.txt;type=text/plain"
```

```json
{
  "id": "9acadc34-1f91-4475-9f4b-9fa7425b9082",
  "filename": "sample_note.txt",
  "content_type": "text/plain",
  "size_bytes": 74,
  "status": "uploaded",
  "created_at": "2026-07-03T18:21:17.529476Z",
  "updated_at": "2026-07-03T18:21:17.529479Z"
}
```

Supported content types: `application/pdf`, `image/png`, `image/jpeg`,
`text/plain`. Max upload size: 25MB (`MAX_UPLOAD_SIZE_BYTES`), enforced by
streaming the upload in 1 MiB chunks and rejecting as soon as the running
total exceeds the limit (`413`), avoiding a second full in-memory copy —
though an authenticated oversized upload still costs a full network
transfer before that check runs, a known residual limitation. An
*unauthenticated* one is rejected before any transfer at all. See
`docs/adr/0014-...` and `docs/adr/0017-...`.

**List the document registry**

```bash
curl -H "X-API-Key: local-dev-key" "http://localhost:8000/documents?limit=20&offset=0"
```

```json
{
  "items": [ { "id": "...", "filename": "...", "status": "uploaded", "..." : "..." } ],
  "total": 3,
  "limit": 20,
  "offset": 0
}
```

Paginated, most recently uploaded first. `limit` defaults to 20 (max 100),
`offset` defaults to 0; both are validated (`422` outside range).

**Get a single document's status**

```bash
curl -H "X-API-Key: local-dev-key" http://localhost:8000/documents/{document_id}
```

**Enqueue the extraction + validation pipeline**

```bash
curl -X POST -H "X-API-Key: local-dev-key" http://localhost:8000/documents/{document_id}/process
```

As of ADR-0022, this **enqueues a job and returns immediately** (`202`,
with the job's id and initial `queued` status) — it no longer runs the
pipeline inline. The `worker` service (a separate long-running process,
`apps/worker/main.py`, its own docker-compose service) claims queued jobs
and runs the same OCR → PHI-gate → field-extraction → validation pipeline
described below; poll `GET .../result` (next section) to see progress and
the eventual outcome. `404` if the document doesn't exist; `409` if it
already has an active job or is already `validated` — see
`docs/adr/0022-...`.

The pipeline itself runs real OCR against the stored file (Tesseract for
images/PDF, direct decode for `text/plain`) to produce real `raw_text`,
then PHI-checks that text **before** anything else happens — including
before the field-extraction LLM is ever called: if PHI-shaped content is
found, a redacted placeholder is stored instead of the real text (`fields`
become `{}` too, the LLM call is skipped entirely), and the document's
status becomes `failed`. Otherwise the clean `raw_text` is sent to the
Anthropic API (`AnthropicFieldExtractionPipeline`, see
[Architecture](#architecture)) to extract real structured `fields` via a
forced tool call, the full validation pipeline runs against the real
result, and status becomes `validated` or `failed` based on the
validators (including, now genuinely, whether the LLM actually found all
the required fields). A field-extraction failure classified transient
(rate limit, connection error, 5xx) is retried with backoff up to a
bounded budget (`docs/adr/0023-...`); a terminal failure (missing/invalid
API key, a 4xx, or budget exhaustion) fails the document cleanly — the
real, already-PHI-checked `raw_text` is still persisted, only `fields`
stays empty. See `docs/adr/0011-...` and `docs/adr/0019-...`.

If the uploaded bytes don't actually match the declared content type
(corrupted file, mismatched `Content-Type`), extraction fails cleanly —
`status: failed` and a clear `issues` message in `GET .../result` — rather
than a raw `500` and a document stuck in `processing` forever. See
`docs/adr/0012-...`.

OCR runs off the worker loop's event loop
(`starlette.concurrency.run_in_threadpool`), and the worker process is
entirely separate from the API process, so one large or slow document
being processed never stalls the API — including `/health` — while it
runs. See `docs/adr/0013-...`.

PDFs over `MAX_PDF_PAGES` (default 50) are rejected immediately rather
than processed page-by-page and failed at the end. Unusually large or
malformed images (a decompression-bomb-shaped file) also fail cleanly
rather than crashing. See `docs/adr/0016-...`.

**Fetch the processing status/result**

```bash
curl -H "X-API-Key: local-dev-key" http://localhost:8000/documents/{document_id}/result
```

The one place to check both "what's the status" and "what's the result"
(ADR-0022). Always `200` except when the document itself doesn't exist
(`404`) — every other case, including "never submitted" and "processing
failed", is `200` with the state discriminated in the body:
- Never submitted: `document.status: "uploaded"`, nothing else.
- In progress: `document.status: "processing"`, plus `job_status`
  (`queued` / `running` / `retrying`) for the active job.
- Completed or failed: `document.status: "validated"`/`"failed"`, plus
  the `extraction`/`validation` results.

## End-to-end demo flow

```bash
API_KEY=local-dev-key

# 1. upload
DOC_ID=$(curl -s -X POST http://localhost:8000/documents \
  -H "X-API-Key: $API_KEY" \
  -F "file=@sample_note.txt;type=text/plain" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")

# 2. confirm it's in the registry
curl -s -H "X-API-Key: $API_KEY" http://localhost:8000/documents/$DOC_ID | python3 -m json.tool

# 3. enqueue it for extraction + validation (returns immediately, 202)
curl -s -X POST -H "X-API-Key: $API_KEY" http://localhost:8000/documents/$DOC_ID/process | python3 -m json.tool

# 4. poll for the result -- the `worker` service processes it asynchronously
curl -s -H "X-API-Key: $API_KEY" http://localhost:8000/documents/$DOC_ID/result | python3 -m json.tool
```

## Status & constraints

- **No HIPAA compliance claim.** This is a local development scaffold, not a
  compliant system.
- **Never upload real patient data — this is more load-bearing than before.**
  Through Sprint 1, both `raw_text` and `fields` were always synthetic
  regardless of what you uploaded, so real PHI structurally could not enter
  the system. As of real OCR (`docs/adr/0010-...`) and real, LLM-based
  field extraction (`docs/adr/0019-...`), both are now real — and the raw
  text is sent to a third-party API (Anthropic) as part of producing
  `fields`, which is why the PHI check runs *before* that call, not just
  before persistence. Treat this exactly like any other early-stage system
  with no compliance controls: synthetic/test data only.
- **PHI detection gates database persistence, not just document status.**
  `PHIDetectionValidator` runs before anything derived from the real text
  is written — a PHI finding gets a redacted placeholder
  (`extraction_results.raw_text`/`fields`) instead of the real content, not
  just a `status: failed` flag after the fact. Verified directly at the
  database level: uploaded a real image containing a fake-but-pattern-shaped
  SSN, queried Postgres directly, confirmed only the redaction placeholder
  is present. **Still partial** — the original uploaded file itself lands
  in the storage backend at upload time, before any scanning is possible;
  this closes the database exposure, not that one. See `docs/adr/0011-...`.
- **PHI detection is a lightweight guardrail, not a compliance control —
  and its two biggest gaps are quantified, not just theoretical.**
  `PHIDetectionValidator` is regex-based pattern matching (SSN, email,
  phone, IP address, credit card shapes) — no NER, so **no person-name or
  street-address recognition at all**. Evaluated against 17 constructed,
  synthetic-but-realistic PHI-shaped test cases: caught 4/17 before this
  pattern set was expanded, still misses names and addresses specifically
  because no regex shape reliably represents either. It exists to catch
  obvious accidental real-PHI ingestion, not to certify a document is
  PHI-free. Genuinely exercised against real OCR'd content (previously
  only unit-tested against synthetic mock text). Upgrading toward NER
  (Microsoft Presidio) was evaluated directly — measured 299-680MB of
  image growth depending on model choice, and accuracy that wasn't a
  clean win (missed an SSN this project's own regex catches reliably) —
  and **not adopted**. See `docs/adr/0015-...` and `docs/adr/0018-...`.
- **Auth is named keys, not scoped identity.** `X-API-Key` gates
  `/documents*`, and each configured key now resolves to a name (ADR-0026)
  — every named key still has identical access. Who uploaded a document or
  enqueued a job is now durably recorded (ADR-0027, `audit_log_entries`),
  but there's no query API for it yet, and no scoped
  permissions/sessions/OAuth — both explicitly out of these two ADRs'
  bounded scope.
- **Field extraction is real, single-provider (Anthropic), and requires a
  key you supply.** `AnthropicFieldExtractionPipeline` uses a forced tool
  call against the Anthropic API — no provider-agnostic tree was built in
  advance (see `docs/adr/0019-...` for why). Set `ANTHROPIC_API_KEY` (never
  commit it); without one, the pipeline still constructs successfully but
  fails closed on every request (`FieldExtractionError`, surfaced as
  `status: failed`), never silently calling the API with no key. Raw text
  sent to the API is capped at `ANTHROPIC_MAX_INPUT_CHARS` (default 12,000)
  to bound per-document cost, the same way `MAX_PDF_PAGES` bounds OCR cost.
- Extraction, validation, and storage are all interchangeable behind their
  respective interfaces — extending toward a second field-extraction
  provider, cloud/vision-LLM OCR, or RAG-based retrieval means adding a new
  implementation, not restructuring the API.
