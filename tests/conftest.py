import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from apps.api.dependencies import (
    get_extraction_pipeline,
    get_field_extraction_pipeline,
    get_phi_validator,
    get_storage,
    get_validation_pipeline,
)
from apps.api.main import app
from modules.extraction.mock import MockFieldExtractionPipeline
from modules.ingestion import models as ingestion_models  # noqa: F401  (registers ORM table)
from modules.ingestion.models import Document, DocumentStatus
from modules.ingestion.storage import LocalFileStorage
from modules.ocr import models as ocr_models  # noqa: F401  (registers ORM table)
from modules.ocr.mock import MockExtractionPipeline
from modules.processing import models as processing_models  # noqa: F401  (registers ORM table)
from modules.processing.models import Job
from modules.processing.pipeline import run_processing_pipeline
from modules.processing.worker import start_worker, stop_worker
from modules.validation import models as validation_models  # noqa: F401  (registers ORM table)
from modules.validation.composite import CompositeValidationPipeline
from modules.validation.phi import PHIDetectionValidator
from modules.validation.rules import RequiredFieldsValidator
from shared.config.settings import get_settings
from shared.database.base import Base
from shared.database.session import get_db

TEST_API_KEY = "test-api-key"
TEST_API_KEY_LABEL = "test-caller"
PROCESS_JOB_POLL_INTERVAL = 0.01


@pytest_asyncio.fixture
async def db_engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    # A real file, not `sqlite:///:memory:`: an in-memory SQLite database
    # is only visible to the single DBAPI connection that created it, so
    # any test exercising the real worker loop (a background task making
    # its own connection, concurrently with the test's own queries and
    # TestClient's request handling on its own portal thread) needs
    # something all of those can see consistently. A real file gives that
    # for free via the filesystem, without `:memory:`'s shared-cache mode
    # (tried first) -- shared-cache introduces its own table-level locking
    # protocol that, unlike ordinary SQLite file locking, doesn't reliably
    # honor `timeout`/busy_timeout under real concurrent writers, and
    # produced exactly the "database table is locked" flakiness a real
    # file avoids. Never persists past the test: tmp_path is unique and
    # cleaned up per test by pytest.
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
def client(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    test_storage = LocalFileStorage(tmp_path / "uploads")

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_storage] = lambda: test_storage
    app.dependency_overrides[get_extraction_pipeline] = lambda: MockExtractionPipeline()
    app.dependency_overrides[get_field_extraction_pipeline] = lambda: MockFieldExtractionPipeline()
    app.dependency_overrides[get_validation_pipeline] = lambda: CompositeValidationPipeline(
        [RequiredFieldsValidator(), PHIDetectionValidator()]
    )
    # Mutates the actual Settings singleton rather than overriding a
    # FastAPI dependency: ApiKeyGateMiddleware (modules/auth/middleware.py)
    # reads get_valid_api_keys() directly as a plain function call, bypassing
    # app.dependency_overrides entirely by design (it runs before FastAPI's
    # routing/DI layer even starts) — this is the one thing both it and the
    # require_api_key route dependency actually share.
    monkeypatch.setattr(get_settings(), "api_keys", f"{TEST_API_KEY_LABEL}:{TEST_API_KEY}")

    # No `with` block: skips the app's lifespan (which targets the real
    # Postgres engine) so tests don't require a running database.
    test_client = TestClient(app, headers={"X-API-Key": TEST_API_KEY})
    yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def process_job(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> Callable[[uuid.UUID], Awaitable[None]]:
    """Drains the queue for one document (ADR-0022: `/process` only
    enqueues now, it no longer runs the pipeline inline).

    Runs the real worker loop (real claim_next_job, real outcome writes)
    against the same on-disk storage the `client` fixture's uploads were
    written to (same tmp_path/"uploads"), using whatever pipelines a test
    configured via app.dependency_overrides -- falling back to the same
    real defaults apps/api/dependencies.py resolves otherwise. This is
    what lets every existing PHI/field-extraction-failure test keep
    configuring its scenario exactly as before (via
    app.dependency_overrides[get_extraction_pipeline] = ...); only the
    "how does it actually get processed" step changed.
    """
    storage = LocalFileStorage(tmp_path / "uploads")

    async def _process(document_id: uuid.UUID, *, timeout: float = 5.0) -> None:
        extraction_pipeline = app.dependency_overrides.get(
            get_extraction_pipeline, get_extraction_pipeline
        )()
        field_extraction_pipeline = app.dependency_overrides.get(
            get_field_extraction_pipeline, get_field_extraction_pipeline
        )()
        phi_validator = app.dependency_overrides.get(get_phi_validator, get_phi_validator)()
        validation_pipeline = app.dependency_overrides.get(
            get_validation_pipeline, get_validation_pipeline
        )()

        async def process_job_fn(job: Job) -> object:
            async with session_factory() as db:
                return await run_processing_pipeline(
                    job,
                    db=db,
                    storage=storage,
                    extraction_pipeline=extraction_pipeline,
                    field_extraction_pipeline=field_extraction_pipeline,
                    phi_validator=phi_validator,
                    validation_pipeline=validation_pipeline,
                )

        async def _wait_until_terminal() -> None:
            while True:
                async with session_factory() as session:
                    document = await session.get(Document, document_id)
                    if document is not None and document.status in (
                        DocumentStatus.VALIDATED,
                        DocumentStatus.FAILED,
                    ):
                        return
                await asyncio.sleep(PROCESS_JOB_POLL_INTERVAL)

        task = await start_worker(
            session_factory,
            process_job_fn=process_job_fn,
            poll_interval_seconds=PROCESS_JOB_POLL_INTERVAL,
        )
        try:
            await asyncio.wait_for(_wait_until_terminal(), timeout=timeout)
        finally:
            await stop_worker(task)

    return _process


class _UnreachableSession:
    """Stand-in for AsyncSession that fails like a dropped DB connection."""

    async def execute(self, *args: object, **kwargs: object) -> None:
        raise SQLAlchemyError("simulated database connectivity failure")


@pytest.fixture
def unhealthy_db_client() -> Iterator[TestClient]:
    async def override_get_db() -> AsyncIterator[_UnreachableSession]:
        yield _UnreachableSession()

    app.dependency_overrides[get_db] = override_get_db

    test_client = TestClient(app)
    yield test_client

    app.dependency_overrides.clear()
