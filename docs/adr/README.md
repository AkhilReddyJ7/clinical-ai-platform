# Architecture Decision Records

Decisions made during Sprint 1 (MVP slice), Sprint 1.5 (production-readiness
hardening), and Sprint 2 (auth, PHI detection, real OCR). Each record
follows: Status, Context, Decision, Consequences.

| # | Title | Status |
|---|---|---|
| [0001](0001-modular-monolith-over-microservices.md) | Modular monolith over microservices | Accepted |
| [0002](0002-interface-first-pipeline-stages.md) | Interface-first pipeline stages | Accepted |
| [0003](0003-alembic-migrations-over-implicit-create-all.md) | Alembic migrations over implicit `create_all` | Accepted |
| [0004](0004-sqlite-for-tests-postgres-for-runtime.md) | SQLite for tests, Postgres for runtime | Accepted |
| [0005](0005-paginated-response-envelope-breaking-change-accepted-early.md) | Paginated response envelope, breaking change accepted early | Accepted |
| [0006](0006-non-root-container-fixed-uid-venv-outside-bind-mount.md) | Non-root container, fixed UID 1000, venv outside the bind mount | Accepted (corrected by 0009) |
| [0007](0007-ci-validates-docker-build-and-boot.md) | CI validates the Docker build and boot, not just the Python package | Accepted |
| [0008](0008-lightweight-regex-phi-detection-not-presidio.md) | Lightweight regex-based PHI detection, not Presidio | Accepted |
| [0009](0009-preseed-upload-directory-ownership-in-image.md) | Pre-seed the upload directory in the image so named-volume ownership is correct | Accepted |
| [0010](0010-real-local-ocr-via-tesseract-fields-stay-synthetic.md) | Real local OCR via Tesseract; fields stay synthetic pending real extraction | Accepted (resolved by 0011) |
| [0011](0011-phi-detection-gates-persistence.md) | PHI detection gates persistence, not just document status | Accepted |
