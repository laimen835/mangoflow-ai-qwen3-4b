import pytest
from pydantic import ValidationError

from src.core.api.schemas import ChatRequest


def test_widget_button_without_message():
    assert ChatRequest(intent="contacts", channel="web").message is None


@pytest.mark.parametrize("payload", [{}, {"message": "   "}, {"intent": " "}])
def test_empty_chat_request_is_rejected(payload):
    with pytest.raises(ValidationError):
        ChatRequest(**payload)
