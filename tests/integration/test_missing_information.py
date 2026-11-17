import pytest
from src.core.services.missing_information import missing_information,is_full_refusal
from src.core.services.chat_service import ChatService
from src.core.api.schemas import ChatRequest
from src.core.rag.retriever import RetrievalResult


@pytest.mark.asyncio
async def test_missing_fact_offers_related_section_without_claiming_proof(db):
    result=await missing_information(db,'Стоимость поступления в 2030 году')
    assert result.kind=='missing_fact' and 'точную стоимость' in result.text
    assert 'не подтверждение' in result.text and 'postupayushchim' in result.text


@pytest.mark.asyncio
async def test_clarification_respects_provided_group(db):
    assert (await missing_information(db,'Какие пары?')).kind=='clarification'
    assert (await missing_information(db,'Какие пары?',group='09.02.07-251')).kind=='missing_fact'
    assert (await missing_information(db,'Какие пары у 09.02.07-251?')).kind=='missing_fact'
    assert (await missing_information(db,'Какие документы нужны?')).kind=='clarification'
    assert (await missing_information(db,'Какие документы нужны для поступления?')).kind=='missing_fact'


@pytest.mark.asyncio
async def test_failed_search_uses_technical_message(db,llm,settings):
    class Unavailable:
        async def retrieve(self,*a,**kw):return RetrievalResult(status='unavailable')
    response=await ChatService(db,llm,Unavailable(),settings).handle(ChatRequest(message='Информация о стоимости'))
    assert response.usage['retrieval_status']=='unavailable'
    assert response.usage['fallback_type']=='search_error'
    assert 'техническая ошибка' in response.answer and 'Я не нашёл' not in response.answer


def test_partial_answer_is_not_replaced_with_total_refusal():
    assert is_full_refusal('Я не нашёл этой информации в базе знаний.')
    assert not is_full_refusal('Государственная и социальная стипендии указаны [1]. Точная сумма не указана.')
