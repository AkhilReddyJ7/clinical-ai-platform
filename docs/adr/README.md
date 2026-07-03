# Architecture Decision Records

Decisions made during Sprint 1 (MVP slice) and Sprint 1.5 (production-readiness
hardening). Each record follows: Status, Context, Decision, Consequences.

| # | Title | Status |
|---|---|---|
| [0001](0001-modular-monolith-over-microservices.md) | Modular monolith over microservices | Accepted |
| [0002](0002-interface-first-pipeline-stages.md) | Interface-first pipeline stages | Accepted |
| [0003](0003-alembic-migrations-over-implicit-create-all.md) | Alembic migrations over implicit `create_all` | Accepted |
| [0004](0004-sqlite-for-tests-postgres-for-runtime.md) | SQLite for tests, Postgres for runtime | Accepted |
| [0005](0005-paginated-response-envelope-breaking-change-accepted-early.md) | Paginated response envelope, breaking change accepted early | Accepted |
| [0006](0006-non-root-container-fixed-uid-venv-outside-bind-mount.md) | Non-root container, fixed UID 1000, venv outside the bind mount | Accepted |
| [0007](0007-ci-validates-docker-build-and-boot.md) | CI validates the Docker build and boot, not just the Python package | Accepted |
