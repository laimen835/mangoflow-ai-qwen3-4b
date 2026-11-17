import pytest
from sqlalchemy import select
from src.core.db.models import Document, DocumentStatus
from src.core.rag.document_policy import real_document_filter, is_test_document


@pytest.mark.asyncio
async def test_test_sources_remain_stored_but_are_excluded(db):
    docs = [Document(title='Официальные контакты',source_type='web',status=DocumentStatus.INDEXED,meta={}),
            Document(title='ТЕСТ: расписание',source_type='schedule',status=DocumentStatus.INDEXED,meta={}),
            Document(title='Проверка',source_type='test',status=DocumentStatus.INDEXED,meta={}),
            Document(title='Проверка метаданных',source_type='web',status=DocumentStatus.INDEXED,meta={'is_test':True})]
    db.add_all(docs)
    await db.flush()
    allowed=(await db.scalars(select(Document).where(Document.id.in_([d.id for d in docs]),real_document_filter()))).all()
    assert [d.id for d in allowed] == [docs[0].id]
    assert not is_test_document(docs[0])
    assert all(is_test_document(d) for d in docs[1:])
