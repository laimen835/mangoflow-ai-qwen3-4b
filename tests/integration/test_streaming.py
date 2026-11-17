import pytest

from src.core.api.schemas import ChatRequest
from src.core.services.chat_service import ChatService


@pytest.mark.asyncio
async def test_stream_finishes_and_persists_no_source_refusal(db, llm, retriever, settings):
    service = ChatService(db, llm, retriever, settings)
    events = [e async for e in service.stream(ChatRequest(message="Нет такого источника"))]
    assert events[0]["type"] == "status"
    assert events[-1]["type"] == "done"
    assert events[-1]["data"]["message_id"] > 0
    assert "не нашёл" in events[-1]["data"]["answer"]
    assert events[-1]["data"]["sources"] == []
