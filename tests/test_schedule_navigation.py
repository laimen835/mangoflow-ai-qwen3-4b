from types import SimpleNamespace
import pytest
from src.core.api.schemas import ChatRequest
from src.core.services.chat_service import ChatService


@pytest.mark.asyncio
async def test_group_in_message_selects_its_own_pdf_without_requiring_ui_group_field():
    law = SimpleNamespace(id=1, title='Расписание Управления и права', meta={'groups': ['40.02.04-251']})
    design = SimpleNamespace(id=2, title='Расписание Проектирования', meta={'groups': ['08.02.01-251']})
    class DB:
        async def execute(self, stmt):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [law, design]))
    service = ChatService.__new__(ChatService); service._db = DB()
    answer, docs = await service._site_directory_answer(ChatRequest(message='Где скачать расписание группы 08.02.01-251?'))
    assert docs == [design] and law.title not in answer and '08.02.01-251' in answer
    answer, docs = await service._site_directory_answer(ChatRequest(message='Какой документ содержит расписание группы 40.02.04-251?'))
    assert docs == [law] and design.title not in answer
