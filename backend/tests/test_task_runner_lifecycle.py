"""Offline tests for prompt cleanup of nested task-runner generators."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.event import DoneEvent, MessageEvent, WaitEvent
from app.domain.models.session import SessionStatus, TaskMode
from app.domain.services.agent_task_runner import AgentTaskRunner


class InputQueue:
    def __init__(self, *messages):
        self.messages = list(messages)

    async def is_empty(self):
        return not self.messages

    async def pop(self):
        message = self.messages.pop(0)
        return "input-id", MessageEvent(role="user", message=message).model_dump_json()


def _runner_and_task(flow_factory, inputs):
    runner = object.__new__(AgentTaskRunner)
    runner._session_id = "session"
    runner._agent_id = "agent"
    runner._flow = SimpleNamespace(run=flow_factory)
    runner._sandbox = SimpleNamespace(ensure_sandbox=AsyncMock())
    runner._mcp_repository = SimpleNamespace(get_mcp_config=AsyncMock(return_value={}))
    runner._mcp_tool = SimpleNamespace(initialized=AsyncMock())
    runner._session_repository = SimpleNamespace(
        find_by_id=AsyncMock(return_value=SimpleNamespace(task_mode=TaskMode.AGENT)),
        add_event=AsyncMock(),
        update_status=AsyncMock(),
        update_latest_message=AsyncMock(),
        increment_unread_message_count=AsyncMock(),
    )
    runner._sync_message_attachments_to_sandbox = AsyncMock()
    runner._sync_message_attachments_to_storage = AsyncMock()
    task = SimpleNamespace(input_stream=inputs, output_stream=SimpleNamespace(put=AsyncMock(return_value="redis-id")))
    return runner, task


async def test_new_input_closes_previous_flow_before_next_message():
    timeline = []
    inputs = InputQueue("first")

    async def flow(message):
        timeline.append("start:" + message.message)
        try:
            if message.message == "first":
                inputs.messages.append("second")
                yield MessageEvent(message="working")
                pytest.fail("Interrupted flow resumed after a new input arrived")
            assert "close:first" in timeline
            yield DoneEvent()
        finally:
            await asyncio.sleep(0)  # Cleanup must be awaited, not scheduled.
            timeline.append("close:" + message.message)

    runner, task = _runner_and_task(flow, inputs)
    await runner.run(task)

    assert timeline == ["start:first", "close:first", "start:second", "close:second"]
    assert [json.loads(call.args[0])["type"] for call in task.output_stream.put.await_args_list] == ["message", "done"]
    assert all(call.args[1].id == "redis-id" for call in runner._session_repository.add_event.await_args_list)


async def test_wait_closes_flow_before_return_and_next_message():
    timeline = []
    inputs = InputQueue("first")

    async def flow(message):
        timeline.append("start:" + message.message)
        try:
            if message.message == "first":
                yield WaitEvent()
                pytest.fail("Waiting flow resumed instead of closing")
            assert "close:first" in timeline
            yield DoneEvent()
        finally:
            await asyncio.sleep(0)
            timeline.append("close:" + message.message)

    runner, task = _runner_and_task(flow, inputs)
    await runner.run(task)

    assert timeline == ["start:first", "close:first"]
    runner._session_repository.update_status.assert_awaited_once_with("session", SessionStatus.WAITING)
    inputs.messages.append("second")
    await runner.run(task)
    assert timeline == ["start:first", "close:first", "start:second", "close:second"]


async def test_cancellation_closes_nested_flow_before_done_event():
    timeline = []
    publishing = asyncio.Event()

    async def flow(message):
        try:
            yield MessageEvent(message="working")
        finally:
            await asyncio.sleep(0)
            timeline.append("closed")

    async def publish(payload):
        event_type = json.loads(payload)["type"]
        if event_type == "message":
            publishing.set()
            await asyncio.Event().wait()
        timeline.append("publish:" + event_type)
        return "redis-id"

    runner, task = _runner_and_task(flow, InputQueue("first"))
    task.output_stream.put.side_effect = publish
    running = asyncio.create_task(runner.run(task))
    await asyncio.wait_for(publishing.wait(), timeout=1)
    running.cancel()
    await asyncio.wait_for(running, timeout=1)

    assert timeline == ["closed", "publish:done"]
    runner._session_repository.update_status.assert_awaited_once_with("session", SessionStatus.COMPLETED)


async def test_exception_closes_nested_flow_before_error_event(monkeypatch):
    timeline = []
    monkeypatch.delenv("ENABLE_DEBUG_BREAK", raising=False)
    monkeypatch.setattr("app.domain.services.agent_task_runner.debugpy.is_client_connected", lambda: False)

    async def flow(message):
        try:
            yield MessageEvent(message="working")
        finally:
            await asyncio.sleep(0)
            timeline.append("closed")

    async def publish(payload):
        event_type = json.loads(payload)["type"]
        if event_type == "message":
            raise RuntimeError("event publishing failed")
        timeline.append("publish:" + event_type)
        return "redis-id"

    runner, task = _runner_and_task(flow, InputQueue("first"))
    task.output_stream.put.side_effect = publish
    await runner.run(task)

    assert timeline == ["closed", "publish:error"]
    runner._session_repository.update_status.assert_awaited_once_with("session", SessionStatus.COMPLETED)
