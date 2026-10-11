"""Main-branch live computer updates remain compatible with durable recovery."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.event import FileUpdateEvent, MessageEvent, TerminalUpdateEvent, ToolEvent, ToolStatus
from app.domain.models.message import LLMMessage
from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseToolkit, tool
from test_agent_recovery import Repository, ScriptedLLM, assistant, call, collect, make_agent, replies
from test_task_runner_lifecycle import InputQueue, _runner_and_task


class LiveShell(BaseToolkit):
    name = "shell"

    def __init__(self):
        super().__init__()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()
        self.cancelled = False
        self.sandbox = SimpleNamespace(view_shell=AsyncMock(return_value=ToolResult(success=True, data={"console": ["live output"]})))

    @tool
    async def shell_exec(self, id: str) -> ToolResult:
        """Run the local regression fixture until released."""
        try:
            await self.release.wait()
            return ToolResult(success=True, data="real-result")
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        finally:
            self.finished.set()


@pytest.mark.parametrize("finish", ["normal", "close_running", "close_completed"])
async def test_shell_live_updates_keep_result_or_unknown_and_close_task(finish):
    shell = LiveShell()
    repository = Repository()
    agent = make_agent(ScriptedLLM(assistant(call("shell-call", "shell_exec", id="shell-session")), LLMMessage.assistant("done")), repository, [shell])
    generator = agent.execute("task")
    calling = await anext(generator)
    assert isinstance(calling, ToolEvent) and calling.status == ToolStatus.CALLING
    live = await asyncio.wait_for(anext(generator), timeout=3)
    assert isinstance(live, TerminalUpdateEvent)
    assert live.output == ["live output"]
    if finish != "close_running":
        shell.release.set()
        await asyncio.wait_for(shell.finished.wait(), timeout=1)
    if finish == "normal":
        remaining = await collect(generator)
        assert any(isinstance(event, ToolEvent) and event.status == ToolStatus.CALLED for event in remaining)
    else:
        await generator.aclose()
    assert shell.finished.is_set()
    repository.memory.validate_tool_pairs()
    result = json.loads(replies(repository.memory)["shell-call"].content)
    assert shell.cancelled is (finish == "close_running")
    if finish == "close_running":
        assert result["status"] == "outcome_unknown"
    else:
        assert result["data"] == "real-result"
    assert len([message for message in repository.memory.messages if message.tool_call_id == "shell-call"]) == 1


async def test_live_events_remain_stream_only():
    async def flow(message):
        yield TerminalUpdateEvent(shell_id="shell", output=["live"])
        yield FileUpdateEvent(path="/tmp/file", content="live")
        yield MessageEvent(message="done")

    runner, task = _runner_and_task(flow, InputQueue("task"))
    await runner.run(task)
    assert [json.loads(call.args[0])["type"] for call in task.output_stream.put.await_args_list] == ["terminal_update", "file_update", "message"]
    assert len(runner._session_repository.add_event.await_args_list) == 1
    assert isinstance(runner._session_repository.add_event.await_args.args[1], MessageEvent)
