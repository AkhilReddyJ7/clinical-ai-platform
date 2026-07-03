from collections.abc import AsyncIterator, Iterator
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
from sqlalchemy.pool import StaticPool

from apps.api.dependencies import get_extraction_pipeline, get_storage, get_validation_pipeline
from apps.api.main import app
from modules.auth.api_key import get_valid_api_keys
from modules.ingestion import models as ingestion_models  # noqa: F401  (registers ORM table)
from modules.ingestion.storage import LocalFileStorage
from modules.ocr import models as ocr_models  # noqa: F401  (registers ORM table)
from modules.ocr.mock import MockExtractionPipeline
from modules.validation import models as validation_models  # noqa: F401  (registers ORM table)
from modules.validation.rules import RequiredFieldsValidator
from shared.database.base import Base
from shared.database.session import get_db

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
TEST_API_KEY = "test-api-key"


@pytest_asyncio.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> Iterator[TestClient]:
    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    test_storage = LocalFileStorage(tmp_path / "uploads")

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_storage] = lambda: test_storage
    app.dependency_overrides[get_extraction_pipeline] = lambda: MockExtractionPipeline()
    app.dependency_overrides[get_validation_pipeline] = lambda: RequiredFieldsValidator()
    app.dependency_overrides[get_valid_api_keys] = lambda: frozenset({TEST_API_KEY})

    # No `with` block: skips the app's lifespan (which targets the real
    # Postgres engine) so tests don't require a running database.
    test_client = TestClient(app, headers={"X-API-Key": TEST_API_KEY})
    yield test_client

    app.dependency_overrides.clear()


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
