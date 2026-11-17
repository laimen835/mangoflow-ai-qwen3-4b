import pytest
from sqlalchemy import select
from src.core.api.schemas import ChatRequest
from src.core.db.models import Document, DocumentStatus
from src.core.services.chat_service import ChatService


@pytest.mark.asyncio
async def test_identity_followup_and_topic_switch(db, llm, retriever, settings):
    url = 'https://ncsa.ru/institutes/137/'
    doc = await db.scalar(select(Document).where(Document.source_url == url,
                                                Document.source_type == 'ncsa_web').limit(1))
    if not doc:
        doc = Document(title='Институт цифровых технологий', source_type='ncsa_web',
                       source_url=url, external_id=url)
        db.add(doc)
    doc.status = DocumentStatus.INDEXED
    doc.meta = {'unit_key': '137', 'unit_director': 'Контрольный Руководитель Института',
                'unit_card_quote': 'Контрольный Руководитель Института, ict@ncsa.ru',
                'unit_card_email': ['ict@ncsa.ru']}
    await db.flush()
    svc = ChatService(db, llm, retriever, settings)
    first = await svc.handle(ChatRequest(message='Кто директор института цифровых технологий?'))
    assert 'Контрольный Руководитель Института' in first.answer
    assert [s.url for s in first.sources] == [url]
    second = await svc.handle(ChatRequest(message='А какая у него почта?', session_id=first.session_id))
    assert 'ict@ncsa.ru' in second.answer
    assert all(s.url == url for s in second.sources)
    third = await svc.handle(ChatRequest(message='Кто директор института?', session_id=first.session_id))
    assert 'Уточните' in third.answer and not third.sources
    fourth = await svc.handle(ChatRequest(message='Что преподают в юридическом институте?', session_id=first.session_id))
    assert retriever.unit_keys == ('138',)
    assert '/institutes/138/' in fourth.answer and '/institutes/spk/' not in fourth.answer


@pytest.mark.asyncio
async def test_missing_institute_card_never_uses_college(db, llm, retriever, settings):
    # Use a historical question: it must not return the current card identity.
    svc = ChatService(db, llm, retriever, settings)
    result = await svc.handle(ChatRequest(message='Кто был директором ИЦТ в 1990 году?'))
    assert 'не нашёл' in result.answer
    assert 'https://ncsa.ru/institutes/137/' in result.answer
    assert not result.sources
