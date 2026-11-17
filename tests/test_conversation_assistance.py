import pytest
from sqlalchemy import select

from src.core.api.schemas import ChatRequest
from src.core.db.models import Conversation, Message, MessageRole
from src.core.llm.base import LLMClient, LLMResponse
from src.core.services.chat_service import ChatService
from src.core.services.conversation_assistance import (
    social_reply, without_social_prefix, utility_kind, utility_request,
    validate_reformulation, safe_task_output,
)


class RecordingLLM(LLMClient):
    model_name = 'test-writing'

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    async def generate(self, messages, **kwargs):
        self.calls.append(messages)
        return LLMResponse(self.answer, {'model': self.model_name})


@pytest.mark.parametrize('text', ['Привет!', 'Спасибо большое!', 'не понял', 'объясни проще'])
def test_social_and_rephrase_are_not_writing_tasks(text):
    assert utility_kind(text) is None


def test_precise_task_routing_and_missing_details():
    assert social_reply('Привет, кто директор института?') is None
    assert without_social_prefix('Привет, кто директор института?') == 'кто директор института?'
    assert utility_kind('Переведи меня на бюджет') is None
    assert utility_kind('Объясни фотосинтез') is None
    assert utility_request('Переведи на английский')['clarification']
    assert utility_request('Помоги составить письмо преподавателю')['clarification']
    assert utility_request('Составь план подготовки к экзамену')['clarification']
    assert utility_request('Составь план подготовки к экзамену по математике на 7 дней')['clarification'] is None
    assert utility_request('Кто директор колледжа?', {'kind':'utility','task':'letter','request':'Письмо'}) is None


def test_no_new_sources_or_numbers_and_rephrase_citations():
    assert not safe_task_output('letter', 'Попроси консультацию', 'Встреча 27 ноября')
    assert not safe_task_output('translation', 'Переведи: hello', 'Привет [1]')
    assert not safe_task_output('study_plan', 'План по математике', 'https://invented.example/')
    assert validate_reformulation('Срок 7 дней [1]', 'Есть 7 дней [1]')
    assert not validate_reformulation('Срок 7 дней [1]', 'Есть 8 дней [1]')
    assert not validate_reformulation('Срок 7 дней [1]', 'Есть 7 дней [2]')
    assert not validate_reformulation('Только если есть заявление [1]', 'Заявление есть [1]')
    assert not validate_reformulation('Директор — Иванов Иван Иванович [1]', 'Директор — Петров Петр Петрович [1]')


@pytest.mark.asyncio
async def test_social_preserves_pending_and_raw_history(db, llm, retriever, settings):
    async def ask(text, session=None):
        return await ChatService(db,llm,retriever,settings).handle(ChatRequest(message=text,session_id=session))
    first = await ask('Кто директор института?')
    thanks = await ask('Спасибо!',first.session_id)
    assert thanks.usage['route'] == 'social'
    result = await ask('цифровых технологий',first.session_id)
    assert result.sources and result.usage['unit_keys'] == ['137']
    await ask('Спасибо',first.session_id)
    result = await ask('А какая у него почта?',first.session_id)
    assert result.sources and 'ict@ncsa.ru' in result.answer
    rows=(await db.scalars(select(Message).join(Conversation).where(
        Conversation.session_id==first.session_id,Message.role==MessageRole.USER).order_by(Message.id))).all()
    assert rows[1].content == 'Спасибо!'
    assert rows[1].usage['dialogue_neutral'] is True


@pytest.mark.asyncio
async def test_many_neutral_turns_do_not_evict_topic(db,llm,retriever,settings):
    async def ask(text,session=None):
        return await ChatService(db,llm,retriever,settings).handle(ChatRequest(message=text,session_id=session))
    first=await ask('Кто директор института цифровых технологий?')
    for _ in range(6):await ask('Спасибо!',first.session_id)
    result=await ask('А какая у него почта?',first.session_id)
    assert 'ict@ncsa.ru' in result.answer and result.sources


@pytest.mark.asyncio
async def test_writing_model_outage_is_friendly(db,retriever,settings):
    from src.core.rag.exceptions import LLMError
    class OfflineLLM(RecordingLLM):
        async def generate(self,*args,**kwargs):raise LLMError('offline')
    result=await ChatService(db,OfflineLLM(''),retriever,settings).handle(ChatRequest(message='Переведи: hello'))
    assert 'попробуйте' in result.answer.casefold() and result.usage['fallback_type']=='model_unavailable'


@pytest.mark.asyncio
@pytest.mark.parametrize('question,answer,label',[
    ('Напиши письмо преподавателю: попроси консультацию', 'Здравствуйте, [Имя Отчество]! Можно записаться на консультацию? С уважением, [ваше имя].','Черновик письма'),
    ('Переведи на английский: Добрый день!', 'Good afternoon!','Перевод'),
    ('Составь план подготовки к экзамену по математике на 7 дней', 'День 1: проверьте темы. Дни 2–6: решайте задания. День 7: повторите ошибки.','Примерный план'),
])
async def test_writing_uses_local_llm_without_retrieval(db,retriever,settings,question,answer,label):
    llm=RecordingLLM(answer)
    result=await ChatService(db,llm,retriever,settings).handle(ChatRequest(message=question))
    assert result.answer.startswith(label) and not result.sources
    assert result.usage['route']=='general_assistance' and len(llm.calls)==1
    assert llm.calls[0][-1]['content']==question
    assert 'не поиск официальных сведений' in llm.calls[0][0]['content']


@pytest.mark.asyncio
async def test_pending_translation_and_topic_change(db,retriever,settings):
    llm=RecordingLLM('Good morning!')
    async def ask(text,session=None):
        return await ChatService(db,llm,retriever,settings).handle(ChatRequest(message=text,session_id=session))
    first=await ask('Переведи на английский')
    assert first.usage['route']=='assistance_clarification' and not llm.calls
    await ask('спасибо',first.session_id)
    second=await ask('Доброе утро!',first.session_id)
    # A greeting alone stays social; explicit text follows without losing the task.
    assert second.usage['route']=='social'
    third=await ask('Текст: До встречи завтра',first.session_id)
    assert third.usage['task']=='translation'
    assert 'Переведи на английский' in llm.calls[-1][-1]['content']
    first=await ask('Помоги написать письмо преподавателю')
    official=await ask('Кто директор института цифровых технологий?',first.session_id)
    assert official.sources and official.usage['route']=='source_database'


@pytest.mark.asyncio
async def test_rephrase_retains_sources_and_rejects_new_citations(db,retriever,settings):
    llm=RecordingLLM('Придуманный директор [2]')
    async def ask(text,session=None):
        return await ChatService(db,llm,retriever,settings).handle(ChatRequest(message=text,session_id=session))
    first=await ask('Кто директор института цифровых технологий?')
    await ask('Спасибо',first.session_id)
    second=await ask('объясни проще',first.session_id)
    assert second.usage['route']=='conversation_rephrase'
    assert [s.document_id for s in second.sources]==[s.document_id for s in first.sources]
    assert first.answer in second.answer and '[2]' not in second.answer


@pytest.mark.asyncio
async def test_letter_does_not_invent_teacher_contacts(db,retriever,settings):
    llm=RecordingLLM('Здравствуйте, [Имя Отчество]! Прошу консультацию.')
    first=await ChatService(db,llm,retriever,settings).handle(ChatRequest(message='Напиши письмо преподавателю: попроси консультацию'))
    second=await ChatService(db,llm,retriever,settings).handle(ChatRequest(message='А какая у него почта?',session_id=first.session_id))
    assert 'Чьи контакты' in second.answer and not second.sources and len(llm.calls)==1
