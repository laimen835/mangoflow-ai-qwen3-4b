import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.core.config import settings as app_settings
from src.core.llm.base import LLMClient, LLMResponse


@pytest.fixture
def settings():
    return app_settings


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(app_settings.database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            async with AsyncSession(bind=connection, join_transaction_mode="create_savepoint",
                                    expire_on_commit=False) as session:
                yield session
            await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
def llm():
    class TestLLM(LLMClient):
        model_name = "test"
        async def generate(self, messages, **kwargs):
            return LLMResponse("Тестовый ответ", {"model": self.model_name})
    return TestLLM()


@pytest.fixture
def retriever():
    class EmptyRetriever:
        async def search(self, *args, **kwargs):
            return []
    return EmptyRetriever()
