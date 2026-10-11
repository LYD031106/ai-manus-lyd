"""Offline regressions for tool batching, interruption and durable recovery."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.event import DoneEvent, ErrorEvent, MessageEvent, PlanEvent, StepEvent, ToolEvent, ToolStatus, WaitEvent
from app.domain.models.memory import Memory, ToolHistoryError
from app.domain.models.message import LLMMessage, Message, Role, ToolCall
from app.domain.models.plan import ExecutionStatus, Plan, Step
from app.domain.models.session import SessionStatus
from app.domain.models.tool_result import ToolResult
from app.domain.services.agents.base import BaseAgent
from app.domain.services.agents.execution import ExecutionAgent
from app.domain.services.flows.plan_act import AgentStatus, PlanActFlow
from app.domain.services.tools.base import BaseToolkit, tool
from app.domain.services.tools.message import MessageToolkit
from app.infrastructure.models.memory_serialization import deserialize_memory, serialize_memory


def call(call_id, name="write", **args):
    return ToolCall(id=call_id, name=name, args=args)


def assistant(*calls):
    return LLMMessage.assistant("", tool_calls=list(calls))


def replies(memory):
    return {m.tool_call_id: m for m in memory.messages if m.role == Role.TOOL}


class Repository:
    """Serialize every save/read so in-memory mutations cannot fake durability."""
    def __init__(self, memory=None):
        self.data = serialize_memory(memory or Memory())
        self.fail_result = None
        self.commit_then_cancel = False

    async def get_memory(self, *_):
        return deserialize_memory(self.data)

    async def save_memory(self, agent_id, name, memory):
        if self.fail_result and self.fail_result in replies(memory):
            self.fail_result = None
            if self.commit_then_cancel:
                self.data = serialize_memory(memory)
                raise asyncio.CancelledError()
            raise RuntimeError("simulated result persistence failure")
        self.data = serialize_memory(memory)

    @property
    def memory(self):
        return deserialize_memory(self.data)


class ScriptedLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    async def ask(self, messages, **_):
        snapshot = Memory(messages=list(messages))
        snapshot.validate_tool_pairs()
        self.requests.append(snapshot)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class RecordingTools(BaseToolkit):
    name = "recording"

    def __init__(self):
        super().__init__()
        self.calls = []
        self.started = asyncio.Event()

    @tool
    async def write(self, text: str = "ok") -> ToolResult:
        """Record a side effect for this regression test."""
        self.calls.append(text)
        if text == "block":
            self.started.set()
            await asyncio.Event().wait()
        if text == "raise":
            raise RuntimeError("connection dropped after side effect")
        return ToolResult(success=True, data=text)


class Agent(BaseAgent):
    name = "test"

    def build_system_prompt(self):
        return "test"


def make_agent(llm, repository=None, tools=None, cls=Agent):
    return cls("agent", repository or Repository(), llm, tools or [])


async def collect(generator):
    return [event async for event in generator]


def test_repair_middle_gap_preserves_real_result_and_user_input():
    real = LLMMessage.tool(tool_call_id="done", name="write", content='{"success":true,"data":"real"}')
    user = LLMMessage.user("continue my task")
    memory = Memory(messages=[assistant(call("done"), call("missing")), real, user])
    audit = memory.repair_tool_pairs()
    memory.validate_tool_pairs()
    assert memory.messages[1] is real
    assert memory.messages[-1] is user
    assert audit == [{"tool_call_id": "missing", "name": "write", "status": "outcome_unknown"}]
    assert json.loads(replies(memory)["missing"].content)["success"] is False
    assert memory.repair_tool_pairs() == []


@pytest.mark.parametrize("messages", [
    [assistant(call("same"), call("same"))],
    [assistant(call(""))],
    [LLMMessage.tool(tool_call_id="orphan", name="write", content="result")],
    [assistant(call("x")), LLMMessage.tool(tool_call_id="x", name="write", content="a"),
     LLMMessage.tool(tool_call_id="x", name="write", content="b")],
    [assistant(call("gap")), LLMMessage.user("later"),
     LLMMessage.tool(tool_call_id="gap", name="write", content="out of order")],
])
def test_ambiguous_history_is_rejected_without_partial_mutation(messages):
    memory = Memory(messages=messages)
    before = memory.model_dump()
    with pytest.raises(ToolHistoryError):
        memory.repair_tool_pairs()
    assert memory.model_dump() == before


def test_legacy_question_in_middle_of_batch_receives_actual_reply():
    memory = Memory(messages=[assistant(
        call("before"), call("question", "message_ask_user", text="Which file?"), call("after"),
    )])
    memory.repair_tool_pairs(user_reply="Use report.json")
    memory.validate_tool_pairs()
    result = replies(memory)
    assert result["question"].content == "Use report.json"
    assert json.loads(result["before"].content)["status"] == "outcome_unknown"
    assert json.loads(result["after"].content)["status"] == "outcome_unknown"


def test_reply_is_not_attached_to_old_question_before_later_user_message():
    memory = Memory(messages=[assistant(call("old", "message_ask_user")), LLMMessage.user("previous input")])
    memory.repair_tool_pairs(user_reply="new unrelated answer")
    assert json.loads(replies(memory)["old"].content)["status"] == "outcome_unknown"
    assert memory.messages[-1].content == "previous input"


async def test_iteration_limit_stops_after_persisting_last_batch():
    repo, toolkit = Repository(), RecordingTools()
    llm = ScriptedLLM(assistant(call("first")), assistant(call("must_not_request")))
    agent = make_agent(llm, repo, [toolkit])
    agent.max_iterations = 1
    events = await collect(agent.execute("go"))
    assert isinstance(events[-1], ErrorEvent)
    assert len(llm.requests) == 1
    assert toolkit.calls == ["ok"]
    repo.memory.validate_tool_pairs()
    assert list(replies(repo.memory)) == ["first"]
    assert repo.memory.tool_call_states == {}


async def test_result_is_durable_before_called_event_and_remaining_calls_are_not_executed():
    repo, toolkit = Repository(), RecordingTools()
    agent = make_agent(ScriptedLLM(assistant(call("first"), call("second"))), repo, [toolkit])
    stream = agent.execute("go")
    assert (await anext(stream)).status == ToolStatus.CALLING
    assert repo.memory.tool_call_states == {"first": "pending", "second": "pending"}
    assert (await anext(stream)).status == ToolStatus.CALLED
    assert json.loads(replies(repo.memory)["first"].content)["success"] is True
    await stream.aclose()  # e.g. event storage fails or new user input arrives
    repo.memory.validate_tool_pairs()
    assert toolkit.calls == ["ok"]
    assert json.loads(replies(repo.memory)["second"].content)["status"] == "not_executed"


async def test_close_at_calling_event_never_invokes_tool():
    repo, toolkit = Repository(), RecordingTools()
    agent = make_agent(ScriptedLLM(assistant(call("first"))), repo, [toolkit])
    stream = agent.execute("go")
    await anext(stream)
    await stream.aclose()
    assert toolkit.calls == []
    assert json.loads(replies(repo.memory)["first"].content)["status"] == "not_executed"


async def test_cancel_running_tool_preserves_earlier_result_and_marks_unknown():
    repo, toolkit = Repository(), RecordingTools()
    agent = make_agent(ScriptedLLM(assistant(
        call("first"), call("running", text="block"), call("later"),
    )), repo, [toolkit])
    task = asyncio.create_task(collect(agent.execute("go")))
    await asyncio.wait_for(toolkit.started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    repo.memory.validate_tool_pairs()
    result = replies(repo.memory)
    assert json.loads(result["first"].content)["success"] is True
    assert json.loads(result["running"].content)["status"] == "outcome_unknown"
    assert json.loads(result["later"].content)["status"] == "not_executed"
    restarted = make_agent(ScriptedLLM(LLMMessage.assistant("Recovered")), repo, [toolkit])
    await collect(restarted.execute("continue"))
    assert toolkit.calls == ["ok", "block"]


@pytest.mark.parametrize("committed", [False, True])
async def test_result_save_failure_does_not_overwrite_committed_success(committed):
    repo, toolkit = Repository(), RecordingTools()
    repo.fail_result = "first"
    repo.commit_then_cancel = committed
    agent = make_agent(ScriptedLLM(assistant(call("first"))), repo, [toolkit])
    with pytest.raises(asyncio.CancelledError if committed else RuntimeError):
        await collect(agent.execute("go"))
    repo.memory.validate_tool_pairs()
    result = json.loads(replies(repo.memory)["first"].content)
    assert toolkit.calls == ["ok"]
    if committed:
        assert result["success"] is True and result["data"] == "ok"
    else:
        assert result["success"] is False and result["status"] == "outcome_unknown"


async def test_side_effect_failure_is_not_retried():
    repo, toolkit = Repository(), RecordingTools()
    agent = make_agent(ScriptedLLM(assistant(call("first", text="raise")), LLMMessage.assistant("inspect state")), repo, [toolkit])
    await collect(agent.execute("go"))
    assert toolkit.calls == ["raise"]
    assert json.loads(replies(repo.memory)["first"].content)["status"] == "outcome_unknown"


async def test_known_readonly_tool_can_retry():
    class ReadOnly(BaseToolkit):
        name = "file"
        attempts = 0

        @tool
        async def file_read(self) -> ToolResult:
            """Read test state."""
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("transient read failure")
            return ToolResult(success=True, data="found")

    toolkit = ReadOnly()
    agent = make_agent(ScriptedLLM(assistant(call("read", "file_read")), LLMMessage.assistant("done")), tools=[toolkit])
    agent.retry_interval = 0
    await collect(agent.execute("read"))
    assert toolkit.attempts == 2


async def test_empty_id_normalized_before_first_persisted_tool_event():
    repo = Repository()
    agent = make_agent(ScriptedLLM(assistant(call(""))), repo, [RecordingTools()])
    stream = agent.execute("go")
    event = await anext(stream)
    persisted_id = repo.memory.messages[-1].tool_calls[0].id
    assert persisted_id and event.tool_call_id == persisted_id
    await stream.aclose()
    repo.memory.validate_tool_pairs()


async def test_duplicate_model_ids_are_rejected_before_any_tool_executes():
    repo, toolkit = Repository(), RecordingTools()
    agent = make_agent(ScriptedLLM(assistant(call("same"), call("same"))), repo, [toolkit])
    with pytest.raises(ToolHistoryError):
        await collect(agent.execute("go"))
    assert toolkit.calls == []
    repo.memory.validate_tool_pairs()


@pytest.mark.parametrize("position", [0, 1, 2])
async def test_question_at_any_position_resumes_matching_id_without_replay(position):
    repo, toolkit = Repository(), RecordingTools()
    calls = [call("write-a", text="a"), call("write-b", text="b")]
    calls.insert(position, call("question", "message_ask_user", text="Proceed?"))
    llm = ScriptedLLM(assistant(*calls), assistant(call("done", "complete_step", success=True, result="finished")))
    agent = make_agent(llm, repo, [toolkit, MessageToolkit()], cls=ExecutionAgent)
    step = Step(description="work")
    plan = Plan(steps=[step])
    events = await collect(agent.execute_step(plan, step, Message(message="go")))
    assert isinstance(events[-1], WaitEvent)
    assert repo.memory.waiting_tool_call_id == "question"
    assert repo.memory.tool_call_states == {"question": "waiting_user"}
    assert "question" not in replies(repo.memory)
    assert toolkit.calls == ["a", "b"][:position]

    restarted = make_agent(llm, repo, [toolkit, MessageToolkit()], cls=ExecutionAgent)
    await restarted.roll_back(Message(message="yes, proceed"))
    repo.memory.validate_tool_pairs()
    assert replies(repo.memory)["question"].content == "yes, proceed"
    assert repo.memory.tool_call_states == {} and repo.memory.waiting_tool_call_id is None
    await collect(restarted.execute_step(plan, step, Message(message="yes, proceed")))
    assert toolkit.calls == ["a", "b"][:position]
    assert step.status == ExecutionStatus.COMPLETED


async def test_second_question_in_batch_is_not_falsely_answered():
    repo = Repository()
    agent = make_agent(ScriptedLLM(assistant(
        call("q1", "message_ask_user", text="first?"),
        call("q2", "message_ask_user", text="second?"),
    )), repo, [MessageToolkit()], cls=ExecutionAgent)
    await collect(agent.execute_step(Plan(), Step(description="ask"), Message(message="go")))
    await agent.roll_back(Message(message="answer first"))
    assert replies(repo.memory)["q1"].content == "answer first"
    assert json.loads(replies(repo.memory)["q2"].content)["status"] == "not_executed"


async def test_invalid_question_gets_error_reply_instead_of_waiting_forever():
    repo = Repository()
    agent = make_agent(ScriptedLLM(
        assistant(call("invalid-question", "message_ask_user")),
        assistant(call("done", "complete_step", success=True, result="finished")),
    ), repo, [MessageToolkit()], cls=ExecutionAgent)
    step = Step(description="work")
    events = await collect(agent.execute_step(Plan(), step, Message(message="go")))
    assert not any(isinstance(event, WaitEvent) for event in events)
    assert "Invalid arguments" in replies(repo.memory)["invalid-question"].content
    assert step.status == ExecutionStatus.COMPLETED


async def test_existing_mid_history_gap_repaired_before_request():
    repo = Repository(Memory(messages=[assistant(call("old")), LLMMessage.user("prior continuation")]))
    llm = ScriptedLLM(LLMMessage.assistant("done"))
    await collect(make_agent(llm, repo).execute("current continuation"))
    request = llm.requests[0]
    assert [m.content for m in request.messages if m.role == Role.USER] == ["prior continuation", "current continuation"]
    assert json.loads(replies(request)["old"].content)["status"] == "outcome_unknown"


def test_compaction_never_turns_unknown_failure_into_success():
    memory = Memory(messages=[assistant(call("old"))])
    memory.repair_tool_pairs()
    before = replies(memory)["old"].content
    memory.compact(keep_recent=0)
    assert replies(memory)["old"].content == before


def flow_with_executor(executor, plan):
    flow = object.__new__(PlanActFlow)
    flow._agent_id, flow._session_id = "agent", "session"
    flow.status = AgentStatus.EXECUTING
    flow.plan = plan
    flow.executor = executor
    flow.planner = SimpleNamespace(update_plan=AsyncMock(side_effect=AssertionError("must not replan")))
    session = SimpleNamespace(project_id=None, status=SessionStatus.PENDING, get_last_plan=lambda: plan)
    flow._session_repository = SimpleNamespace(find_by_id=AsyncMock(return_value=session), update_status=AsyncMock())
    flow._apply_project_instruction = AsyncMock()
    return flow


async def test_terminal_iteration_error_keeps_step_and_plan_failed():
    executor = make_agent(ScriptedLLM(assistant(call("first"))), tools=[RecordingTools()], cls=ExecutionAgent)
    executor.max_iterations = 1
    step = Step(description="work")
    plan = Plan(steps=[step])
    flow = flow_with_executor(executor, plan)
    events = await collect(flow.run(Message(message="go")))
    assert step.status == ExecutionStatus.FAILED and step.success is False
    assert plan.status == ExecutionStatus.FAILED and plan.error
    assert isinstance(events[-1], ErrorEvent)
    assert any(isinstance(e, PlanEvent) and e.plan.status == ExecutionStatus.FAILED for e in events)
    assert flow.status == AgentStatus.IDLE
    flow.planner.update_plan.assert_not_called()


async def test_flow_close_propagates_to_pending_base_agent_immediately():
    repo, toolkit = Repository(), RecordingTools()
    executor = make_agent(ScriptedLLM(assistant(call("first"))), repo, [toolkit], cls=ExecutionAgent)
    flow = flow_with_executor(executor, Plan(steps=[Step(description="work")]))
    stream = flow.run(Message(message="go"))
    assert isinstance(await anext(stream), StepEvent)
    assert isinstance(await anext(stream), ToolEvent)
    await stream.aclose()
    repo.memory.validate_tool_pairs()
    assert json.loads(replies(repo.memory)["first"].content)["status"] == "not_executed"
    assert toolkit.calls == []


async def test_model_exception_is_a_terminal_step_failure():
    executor = make_agent(ScriptedLLM(RuntimeError("provider failure")), cls=ExecutionAgent)
    step = Step(description="work")
    flow = flow_with_executor(executor, Plan(steps=[step]))
    events = await collect(flow.run(Message(message="go")))
    assert isinstance(events[-1], ErrorEvent)
    assert step.status == ExecutionStatus.FAILED
    assert flow.plan.status == ExecutionStatus.FAILED


async def test_summarize_question_waits_without_completing_plan_and_resumes():
    repo = Repository()
    llm = ScriptedLLM(
        assistant(call("summary-question", "message_ask_user", text="Which output format?")),
        assistant(call("final", "deliver_result", message="Finished report", attachments=[])),
    )
    executor = make_agent(llm, repo, [MessageToolkit()], cls=ExecutionAgent)
    plan = Plan(steps=[Step(description="work", status=ExecutionStatus.COMPLETED)])
    flow = flow_with_executor(executor, plan)
    flow.planner.roll_back = AsyncMock()
    events = await collect(flow.run(Message(message="summarize")))
    assert isinstance(events[-1], WaitEvent)
    assert any(isinstance(event, MessageEvent) and event.message == "Which output format?" for event in events)
    assert not any(isinstance(event, DoneEvent) for event in events)
    assert plan.status == ExecutionStatus.RUNNING
    assert flow.status == AgentStatus.SUMMARIZING
    assert repo.memory.waiting_tool_call_id == "summary-question"
    assert "summary-question" not in replies(repo.memory)

    session = await flow._session_repository.find_by_id("session")
    session.status = SessionStatus.WAITING
    resumed_events = await collect(flow.run(Message(message="JSON please")))
    assert replies(repo.memory)["summary-question"].content == "JSON please"
    repo.memory.validate_tool_pairs()
    assert plan.status == ExecutionStatus.COMPLETED
    assert any(isinstance(event, MessageEvent) and event.message == "Finished report" for event in resumed_events)
    assert isinstance(resumed_events[-1], DoneEvent)
