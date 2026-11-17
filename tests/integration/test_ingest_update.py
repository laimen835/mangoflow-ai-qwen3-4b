import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.core.config import settings
from src.core.db.models import Chunk, Document
from src.core.rag.exceptions import IngestionError
from src.core.rag.ingest import ingest_text


@pytest.mark.asyncio
async def test_update_replaces_chunks_and_failed_embedding_preserves_old_content():
    class Embedder:
        async def embed_many(self, texts):
            return [[0.1] * settings.embedding_dim for _ in texts]

    class BrokenEmbedder:
        async def embed_many(self, texts):
            raise RuntimeError("embedding service unavailable")

    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            async with AsyncSession(bind=connection, join_transaction_mode="create_savepoint",
                                    expire_on_commit=False) as db:
                args = dict(title="ТЕСТ обновления", source_type="test", external_id=str(uuid.uuid4()), settings=settings)
                first = await ingest_text(db, text="Первая версия", embedder=Embedder(), **args)
                second = await ingest_text(db, text="Обновлённая версия", embedder=Embedder(), **args)
                assert first["document_id"] == second["document_id"]
                assert second["status"] == "updated"
                with pytest.raises(IngestionError):
                    await ingest_text(db, text="Незавершённая версия", embedder=BrokenEmbedder(), **args)
                contents = (await db.execute(select(Chunk.content).where(Chunk.document_id == first["document_id"]))).scalars().all()
                assert contents == ["Обновлённая версия"]
                assert await db.get(Document, first["document_id"])
            await transaction.rollback()
    finally:
        await engine.dispose()
