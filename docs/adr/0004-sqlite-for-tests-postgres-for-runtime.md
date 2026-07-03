# 0004: SQLite for tests, Postgres for runtime

- **Status:** Accepted
- **Date:** 2026-07-03

## Context

Tests needed to run fast, in CI, without requiring Docker or a live Postgres
instance — a hard requirement for a lightweight, frequently-run CI job (see
[0007](0007-ci-validates-docker-build-and-boot.md)). At the same time, the
application's real runtime target is Postgres, and the two engines don't
behave identically for every feature SQLAlchemy exposes.

## Decision

`tests/conftest.py` builds the schema directly from the SQLAlchemy ORM
models (`Base.metadata.create_all`) against an in-memory SQLite database,
and overrides the `get_db`, `get_storage`, `get_extraction_pipeline`, and
`get_validation_pipeline` FastAPI dependencies so the full request path is
exercised without touching Postgres or the local filesystem. Alembic is
never invoked by the test suite — it is exercised only against real
Postgres, manually and via the CI `docker` job.

## Consequences

- The full test suite runs in well under a second, with zero external
  infrastructure — a meaningful enabler for the CI `test` job staying fast
  and cheap to run on every push.
- This is a deliberate scope boundary, not a blind spot we're unaware of:
  SQLite and Postgres diverge on native `ENUM` types, `JSONB`-specific
  operators, and concurrent-transaction semantics, none of which the test
  suite exercises. This is currently safe because the schema deliberately
  avoids Postgres-specific column types (see
  [0003](0003-alembic-migrations-over-implicit-create-all.md)) — plain
  `JSON`, not `JSONB`; plain `VARCHAR`, not native `ENUM`.
- If the schema ever adopts a genuinely Postgres-specific feature, this
  decision needs revisiting — either accept an untested divergence, or add
  a Postgres-backed integration test tier.
