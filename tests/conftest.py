from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.api.dependencies import get_extraction_pipeline, get_storage, get_validation_pipeline
from apps.api.main import app
from modules.ingestion import models as ingestion_models  # noqa: F401  (registers ORM table)
from modules.ingestion.storage import LocalFileStorage
from modules.ocr import models as ocr_models  # noqa: F401  (registers ORM table)
from modules.ocr.mock import MockExtractionPipeline
from modules.validation import models as validation_models  # noqa: F401  (registers ORM table)
from modules.validation.rules import RequiredFieldsValidator
from shared.database.base import Base
from shared.database.session import get_db

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db_engine():
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
def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
def client(session_factory, tmp_path) -> TestClient:
    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    test_storage = LocalFileStorage(tmp_path / "uploads")

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_storage] = lambda: test_storage
    app.dependency_overrides[get_extraction_pipeline] = lambda: MockExtractionPipeline()
    app.dependency_overrides[get_validation_pipeline] = lambda: RequiredFieldsValidator()

    # No `with` block: skips the app's lifespan (which targets the real
    # Postgres engine) so tests don't require a running database.
    test_client = TestClient(app)
    yield test_client

    app.dependency_overrides.clear()
