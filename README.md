# Clinical AI Intelligence Platform

[![CI](https://github.com/AkhilReddyJ7/clinical-ai-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/AkhilReddyJ7/clinical-ai-platform/actions/workflows/ci.yml)

A document intelligence platform for clinical documents: upload, track, and run
documents through an extraction + validation pipeline.

This is an early-stage local MVP. It is **not** HIPAA-compliant. Extracted
text is now real (read from whatever you upload); structured fields are
still synthetic placeholders — **never upload real patient data**, see
[Status & constraints](#status--constraints).

## Architecture

```
apps/
  api/            FastAPI app: HTTP layer, routing, dependency wiring
  worker/         reserved for a future async processing worker

modules/
  ingestion/      document registry, upload handling, storage abstraction
  ocr/            extraction pipeline interface; real text via local
                   Tesseract OCR (images/PDF) or passthrough (text/plain),
                   structured fields still synthetic (+ mock for tests)
  validation/     validation pipeline interface (required-fields rule +
                   PHI-pattern guardrail, composed together)
  auth/           static API-key auth (X-API-Key header) on /documents/*
  audit/ analytics/ indexing/ layout/ search/         reserved for future work

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
  direct decode (`text/plain`, no OCR needed). Structured `fields` are
  still synthetic (shared with `MockExtractionPipeline`, which stays wired
  into the test suite for speed) — a real field-extraction backend (e.g.
  LLM-based) implements the same interface and replaces just that part
  later. See `docs/adr/0010-...`.
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
  real, local OCR — no API key or external vendor needed.
- `postgres` — Postgres 16, with a named volume for data and another for
  uploaded files (`uploads_data`, mounted at `/app/data/uploads`)

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
override `API_KEYS` (comma-separated for multiple valid keys) via `.env` for
anything beyond local dev, and never commit real keys.

```bash
curl -H "X-API-Key: local-dev-key" http://localhost:8000/documents
```

Missing or wrong key → `401`. No keys configured at all → `503` (fails
closed rather than silently allowing every request through).

This is deliberately a single shared static key, not per-user identity —
enough to gate these endpoints before Sprint 2 adds anything that costs
money per call (real OCR, LLM extraction) or exposes more data (RAG/search).
Per-user auth, sessions, and audit trails are future work
(`modules/auth/`, `modules/audit/`).

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

Tests don't require Postgres, Docker, or a Tesseract install — they run
against an in-memory SQLite database, a temp-directory storage backend, and
`MockExtractionPipeline`, all via dependency overrides (`tests/conftest.py`).
Coverage: health, upload (incl. size-limit rejection/boundary), registry
(list/get), pagination, status transitions, required-fields and
PHI-pattern validation (individually and composed), auth (missing/wrong/
correct key, fail-closed with no keys
configured), and the real `TesseractExtractionPipeline`'s dispatch/
confidence-aggregation logic (`pytesseract` calls mocked — no binary
needed) plus true end-to-end tests proving: real `text/plain` content now
reaches PHI detection *and* gets redacted before persisting (asserting
against both the API response and a follow-up `GET /result` call, not just
one response); and a corrupted/mismatched-content-type upload fails
cleanly (`status: failed`, not stuck in `processing` behind a `500`) —
neither needs an OCR binary, both exercise real code paths. Image/PDF OCR
itself is verified separately, in Docker — see Continuous integration,
below.

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
total exceeds the limit (`413`) — never buffers the whole file into memory
first to find out. See `docs/adr/0014-...`.

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

**Run the extraction + validation pipeline**

```bash
curl -X POST -H "X-API-Key: local-dev-key" http://localhost:8000/documents/{document_id}/process
```

Runs real OCR against the stored file (Tesseract for images/PDF, direct
decode for `text/plain`) to produce real `raw_text`, generates still-
synthetic `fields` (see [Architecture](#architecture)), then runs the
validation pipeline — including PHI detection against the *real* text —
**before** persisting anything: if PHI-shaped content is found, a redacted
placeholder is stored instead of the real text (`fields` become `{}` too),
and the document's status becomes `failed`. Otherwise the real `raw_text`/
`fields` are persisted and status becomes `validated` or `failed` based on
the other validators. See `docs/adr/0011-...`.

If the uploaded bytes don't actually match the declared content type
(corrupted file, mismatched `Content-Type`), extraction fails cleanly —
`200` with `status: failed` and a clear `issues` message — rather than a
raw `500` and a document stuck in `processing` forever. See
`docs/adr/0012-...`.

OCR runs off the request's event loop (`starlette.concurrency.run_in_threadpool`),
so one large document being processed doesn't stall other requests —
including `/health` — while it runs. Measured directly: a 25-page PDF
blocked a concurrent `/health` call for the full ~20s OCR took before this
fix, ~0.03s after. See `docs/adr/0013-...`.

PDFs over `MAX_PDF_PAGES` (default 50) are rejected immediately rather
than processed page-by-page and failed at the end — a 51-page PDF returns
`status: failed` in ~50ms instead of taking as long as it would to
actually OCR every page. Unusually large or malformed images (a
decompression-bomb-shaped file) also fail cleanly rather than crashing.
See `docs/adr/0016-...`.

**Fetch the processing result**

```bash
curl -H "X-API-Key: local-dev-key" http://localhost:8000/documents/{document_id}/result
```

Returns the document, its `ExtractionResult`, and its `ValidationResult`
together. Returns `404` if the document hasn't been processed yet.

## End-to-end demo flow

```bash
API_KEY=local-dev-key

# 1. upload
DOC_ID=$(curl -s -X POST http://localhost:8000/documents \
  -H "X-API-Key: $API_KEY" \
  -F "file=@sample_note.txt;type=text/plain" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")

# 2. confirm it's in the registry
curl -s -H "X-API-Key: $API_KEY" http://localhost:8000/documents/$DOC_ID | python3 -m json.tool

# 3. run it through extraction + validation
curl -s -X POST -H "X-API-Key: $API_KEY" http://localhost:8000/documents/$DOC_ID/process | python3 -m json.tool

# 4. fetch the result later
curl -s -H "X-API-Key: $API_KEY" http://localhost:8000/documents/$DOC_ID/result | python3 -m json.tool
```

## Status & constraints

- **No HIPAA compliance claim.** This is a local development scaffold, not a
  compliant system.
- **Never upload real patient data — this is more load-bearing than before.**
  Through Sprint 1, `raw_text` was always synthetic regardless of what you
  uploaded, so real PHI structurally could not enter the system. As of
  real OCR (`docs/adr/0010-...`), `raw_text` reflects whatever you actually
  upload. Structured `fields` are still synthetic placeholders, but the
  extracted *text* is real. Treat this exactly like any other early-stage
  system with no compliance controls: synthetic/test data only.
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
  (e.g. Microsoft Presidio) is a live, evaluated, and still-open decision
  — see `docs/adr/0015-...`.
- **Auth is a shared static key, not identity.** `X-API-Key` gates
  `/documents*` but there's no concept of a user, session, or per-caller
  audit trail yet — anyone with the key has full access. Real identity,
  scoped permissions, and audit logging are future work (`modules/audit`).
- **Field extraction is still synthetic.** Real OCR (text) shipped; turning
  that real text into real structured fields needs an LLM (or comparable)
  backend, which needs a vendor/credential decision not made yet.
- Extraction, validation, and storage are all interchangeable behind their
  respective interfaces — extending toward LLM-based field extraction,
  cloud/vision-LLM OCR, or RAG-based retrieval means adding a new
  implementation, not restructuring the API.
