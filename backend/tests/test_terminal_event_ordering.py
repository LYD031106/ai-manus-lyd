"""Terminal stream events must observe durable state and completed cleanup."""
import asyncio
import json

import pytest

from app.domain.models.event import DoneEvent, ErrorEvent, MessageEvent, WaitEvent
from app.domain.models.session import SessionStatus
from test_task_runner_lifecycle import InputQueue, _runner_and_task


@pytest.mark.parametrize("terminal, status", [
    (DoneEvent(), SessionStatus.COMPLETED),
    (WaitEvent(), SessionStatus.WAITING),
    (ErrorEvent(error="flow failed"), SessionStatus.COMPLETED),
])
async def test_terminal_publication_waits_for_state_and_prior_history(terminal, status):
    history = []
    published = []
    closed = False
    current_status = SessionStatus.RUNNING
    state_write_started = asyncio.Event()
    allow_state_write = asyncio.Event()

    async def flow(message):
        nonlocal closed
        try:
            yield MessageEvent(message="durable answer")
            yield terminal.model_copy(deep=True)
        finally:
            await asyncio.sleep(0)
            closed = True

    async def save(_, event):
        history.append(event.model_copy(deep=True))

    async def set_status(_, value):
        nonlocal current_status
        assert closed
        state_write_started.set()
        await allow_state_write.wait()
        current_status = value

    async def publish(payload):
        event = json.loads(payload)
        if event["type"] in ("done", "wait", "error"):
            assert closed and current_status == status
            assert history[0].message == "durable answer"
            assert history[0].id == "100-0"
        published.append(event["type"])
        return f"{len(published) * 100}-0"

    runner, task = _runner_and_task(flow, InputQueue("request"))
    runner._session_repository.add_event.side_effect = save
    runner._session_repository.update_status.side_effect = set_status
    task.output_stream.put.side_effect = publish
    running = asyncio.create_task(runner.run(task))
    await asyncio.wait_for(state_write_started.wait(), timeout=1)
    assert published == ["message"]  # No end event during an unfinished DB write.
    allow_state_write.set()
    await asyncio.wait_for(running, timeout=1)
    assert published == ["message", terminal.type]
    assert history[-1].id == "200-0"  # Preserve persisted Redis cursor identities.


@pytest.mark.parametrize("mode", ["cancel", "exception"])
async def test_cancel_and_exception_write_terminal_state_before_output(monkeypatch, mode):
    monkeypatch.delenv("ENABLE_DEBUG_BREAK", raising=False)
    monkeypatch.setattr("app.domain.services.agent_task_runner.debugpy.is_client_connected", lambda: False)
    started = asyncio.Event()
    closed = False
    state = SessionStatus.RUNNING
    terminal_types = []

    async def flow(message):
        nonlocal closed
        try:
            yield MessageEvent(message="working")
        finally:
            await asyncio.sleep(0)
            closed = True

    async def set_status(_, value):
        nonlocal state
        assert closed
        state = value

    async def publish(payload):
        kind = json.loads(payload)["type"]
        if kind == "message":
            started.set()
            if mode == "exception":
                raise RuntimeError("injected delivery failure")
            await asyncio.Event().wait()
        assert state == SessionStatus.COMPLETED and closed
        terminal_types.append(kind)
        return "200-0"

    runner, task = _runner_and_task(flow, InputQueue("request"))
    runner._session_repository.update_status.side_effect = set_status
    task.output_stream.put.side_effect = publish
    running = asyncio.create_task(runner.run(task))
    await asyncio.wait_for(started.wait(), timeout=1)
    if mode == "cancel":
        running.cancel()
    await asyncio.wait_for(running, timeout=1)
    assert terminal_types == (["done"] if mode == "cancel" else ["error"])


async def test_queued_input_is_processed_before_global_done():
    inputs = InputQueue("first")
    states = []
    closed = []

    async def flow(message):
        try:
            if message.message == "first":
                inputs.messages.append("second")
                yield DoneEvent()
            else:
                assert closed == ["first"]
                assert SessionStatus.COMPLETED not in states
                yield DoneEvent()
        finally:
            closed.append(message.message)

    async def set_status(_, status):
        states.append(status)

    runner, task = _runner_and_task(flow, inputs)
    runner._session_repository.update_status.side_effect = set_status
    await runner.run(task)
    assert closed == ["first", "second"]
    assert [json.loads(call.args[0])["type"] for call in task.output_stream.put.await_args_list] == ["done"]
    assert states[-1] == SessionStatus.COMPLETED


async def test_wait_still_pauses_with_queued_input():
    inputs = InputQueue("first")

    async def flow(message):
        inputs.messages.append("second")
        yield WaitEvent()
        pytest.fail("Wait must close the flow and return")

    runner, task = _runner_and_task(flow, inputs)
    await runner.run(task)
    assert inputs.messages == ["second"]
    assert [json.loads(call.args[0])["type"] for call in task.output_stream.put.await_args_list] == ["wait"]
    runner._session_repository.update_status.assert_awaited_once_with("session", SessionStatus.WAITING)


async def test_error_remains_visible_when_new_input_is_queued():
    inputs = InputQueue("first")

    async def flow(message):
        if message.message == "first":
            inputs.messages.append("second")
            yield ErrorEvent(error="previous request failed")
        else:
            yield DoneEvent()

    runner, task = _runner_and_task(flow, inputs)
    await runner.run(task)
    assert [json.loads(call.args[0])["type"] for call in task.output_stream.put.await_args_list] == ["error", "done"]
    assert any(isinstance(call.args[1], ErrorEvent) for call in runner._session_repository.add_event.await_args_list)
