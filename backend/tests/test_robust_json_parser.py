"""Offline regression tests for LangChain's tool-argument retry context."""
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda

from app.domain.models.message import LLMMessage, ToolCall
from app.infrastructure.external.llm.langchain_llm import LangchainLLM
from app.infrastructure.external.llm.robust_json_parser import RobustJsonParser, ToolCallParseError


def _invalid_response():
    return AIMessage(
        content="",
        tool_calls=[{"id": "unexecuted-valid", "name": "file_read", "args": {"file": "/valid"}}],
        invalid_tool_calls=[{"id": "unexecuted-invalid", "name": "file_write", "args": "invalid-json", "error": "invalid JSON"}],
    )


def _assert_tool_pairing(context):
    pending = set()
    for message in context:
        if isinstance(message, ToolMessage):
            assert message.tool_call_id in pending
            pending.remove(message.tool_call_id)
        else:
            assert not pending, "Assistant tool calls must be answered before the next turn"
            if isinstance(message, AIMessage):
                calls = message.tool_calls + message.invalid_tool_calls
                pending = {call["id"] for call in calls}
                assert len(pending) == len(calls)
    assert not pending


def test_retry_context_preserves_original_and_only_adds_error_feedback():
    context = [HumanMessage(content="task")]
    failed = _invalid_response()
    error = ToolCallParseError("parse failed", failed, ["file_write: invalid JSON"])

    retry = error.make_retry_context(context)

    assert len(context) == 1
    assert retry[:-1] == context
    assert isinstance(retry[-1], HumanMessage)
    assert "file_write: invalid JSON" in retry[-1].content
    assert failed not in retry
    _assert_tool_pairing(retry)


@pytest.mark.parametrize("failed_attempts", [0, 1, 2, 3])
async def test_langchain_retry_requests_preserve_tool_pairing(monkeypatch, failed_attempts):
    messages = [
        LLMMessage.system("sys"),
        LLMMessage.user("first task"),
        LLMMessage.assistant(tool_calls=[ToolCall(id="completed", name="file_read", args={"file": "/old"})]),
        LLMMessage.tool(tool_call_id="completed", name="file_read", content="done"),
        LLMMessage.user("next task"),
    ]
    original = [message.model_dump() for message in messages]
    requests = []

    async def fake_model(context):
        _assert_tool_pairing(context)
        requests.append(deepcopy(context))
        if len(requests) <= failed_attempts:
            return _invalid_response()
        return AIMessage(content="", tool_calls=[{"id": "success", "name": "file_read", "args": {"file": "/success"}}])

    # Run the real gateway retry loop and parser with a local model stub. The
    # JSON fixing stage is stubbed too, so no provider connection is created.
    gateway = object.__new__(LangchainLLM)
    gateway._model = RunnableLambda(fake_model)
    gateway._max_retries = max(3, failed_attempts + 1)
    monkeypatch.setattr(RobustJsonParser, "_stage3_output_fixing", AsyncMock(return_value=None))
    original_context = gateway._to_langchain(messages)

    result = await gateway.ask(messages)

    assert len(requests) == failed_attempts + 1
    assert result.tool_calls[0].id == "success"
    assert result.tool_calls[0].args == {"file": "/success"}
    assert requests[0] == original_context
    if failed_attempts:
        assert requests[1] == original_context
    for context in requests[2:]:
        assert context[:-1] == original_context
        assert isinstance(context[-1], HumanMessage)
        assert "file_write" in context[-1].content
        assert "invalid-json" in context[-1].content
    assert [message.model_dump() for message in messages] == original


async def test_langchain_exhaustion_preserves_parse_error(monkeypatch):
    requests = []

    async def fake_model(context):
        _assert_tool_pairing(context)
        requests.append(deepcopy(context))
        return _invalid_response()

    gateway = object.__new__(LangchainLLM)
    gateway._model = RunnableLambda(fake_model)
    gateway._max_retries = 3
    monkeypatch.setattr(RobustJsonParser, "_stage3_output_fixing", AsyncMock(return_value=None))

    with pytest.raises(ToolCallParseError, match="file_write"):
        await gateway.ask([LLMMessage.user("task")])
    assert len(requests) == 3
