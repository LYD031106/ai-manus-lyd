import logging
import asyncio
import uuid
import json
from abc import ABC
from typing import Any, List, Literal, Optional, AsyncGenerator
from app.domain.models.message import Message, LLMMessage, Role, ToolCall
from app.domain.models.memory import ToolHistoryError
from app.domain.services.tools.base import BaseToolkit, OutputTool, Tool, ValidationError
from app.domain.models.event import (
    BaseEvent,
    ToolEvent,
    ToolStatus,
    ErrorEvent,
    MessageEvent,
)
from app.domain.repositories.agent_repository import AgentRepository
from app.domain.external.llm import LLM


logger = logging.getLogger(__name__)


class StructuredOutputEvent(BaseEvent):
    """Internal event carrying validated structured output.

    Emitted when the model submits its result through an :class:`OutputTool`.
    Consumed by the concrete agents; never part of the public
    ``AgentEvent`` union streamed to clients.
    """

    type: Literal["structured_output"] = "structured_output"
    output: Any


class BaseAgent(ABC):
    """
    Base agent class, defining the basic behavior of the agent
    """

    name: str = ""
    max_iterations: int = 100
    max_retries: int = 3
    retry_interval: float = 1.0
    # Arbitrary shell/MCP/write operations may take effect before raising. Only
    # these known read-only operations can be retried without an idempotency key.
    retry_safe_tools = frozenset({
        ("file", "file_read"),
        ("file", "file_find_in_content"),
        ("file", "file_find_by_name"),
        ("shell", "shell_view"),
    })
    tool_choice: Optional[str] = None
    # Context engineering budgets: tool results are truncated at ingestion,
    # and memory is compacted before each model call when over budget.
    max_tool_result_chars: int = 16000
    max_context_tokens: int = 100000

    def __init__(
        self,
        agent_id: str,
        agent_repository: AgentRepository,
        llm: LLM,
        tools: List[BaseToolkit] = []
    ):
        self._agent_id = agent_id
        self._repository = agent_repository
        self._llm = llm
        self.toolkits = tools
        self.memory = None
        self._output_tool: Optional[OutputTool] = None
        self._project_instruction: Optional[str] = None
        self._execution_token = None

    def set_project_instruction(self, instruction: Optional[str]) -> None:
        """Bind project-level guidance used when assembling the system prompt."""
        text = (instruction or "").strip()
        self._project_instruction = text or None

    def build_system_prompt(self) -> str:
        """Assemble the system prompt for this agent; overridden by subclasses."""
        return ""

    async def sync_system_prompt(self) -> None:
        """Insert or refresh the leading system message so project edits apply."""
        await self._ensure_memory()
        prompt = self.build_system_prompt()
        if self.memory.empty:
            self.memory.add_message(LLMMessage.system(prompt))
            await self._repository.save_memory(self._agent_id, self.name, self.memory)
            return
        first = self.memory.messages[0]
        if first.role == Role.SYSTEM and first.content != prompt:
            first.content = prompt
            await self._repository.save_memory(self._agent_id, self.name, self.memory)

    def get_tool(self, name: str) -> Optional[Tool]:
        """Get specified tool"""
        for toolkit in self.toolkits:
            tool = toolkit.get_tool(name)
            if tool:
                return tool
        return None

    def get_tool_schemas(self) -> List[dict]:
        """Get OpenAI function schemas for all available tools.

        Includes the active output tool, if any, so the model can submit
        structured results through native function calling.
        """
        schemas = [schema for toolkit in self.toolkits for schema in toolkit.get_tool_schemas()]
        if self._output_tool:
            schemas.append(self._output_tool.to_openai_schema())
        return schemas

    def _truncate_tool_result(self, content: str) -> str:
        """Cap a tool result before it enters memory, to bound context growth."""
        if len(content) <= self.max_tool_result_chars:
            return content
        omitted = len(content) - self.max_tool_result_chars
        return (
            content[: self.max_tool_result_chars]
            + f"... [truncated {omitted} chars to save context]"
        )

    async def invoke_tool(self, tool: Tool, tool_call: ToolCall) -> LLMMessage:
        """Retry only known read-only tools; side effects can have unknown outcomes."""
        retries = 0
        last_error = ""
        retry_limit = self.max_retries if (tool.toolkit.name, tool.name) in self.retry_safe_tools else 0
        while retries <= retry_limit:
            try:
                raw_result = await tool.invoke(tool_call.args)
                content = (
                    raw_result.model_dump_json()
                    if hasattr(raw_result, "model_dump_json")
                    else str(raw_result)
                )
                return LLMMessage.tool(
                    tool_call_id=tool_call.id,
                    name=tool.name,
                    content=self._truncate_tool_result(content),
                    artifact=raw_result,
                )
            except Exception as e:
                last_error = str(e)
                retries += 1
                if retries <= retry_limit:
                    await asyncio.sleep(self.retry_interval)
                else:
                    logger.exception("Tool execution failed: %s (%s)", tool_call.name, tool_call.id)
                    break

        return LLMMessage.tool(
            tool_call_id=tool_call.id, name=tool.name,
            content=json.dumps({
                "success": False,
                "status": "outcome_unknown",
                "message": f"Tool raised an error: {self._truncate_tool_result(last_error)}. Its effects may already have occurred; inspect the current state before retrying.",
            }),
        )

    def _handle_output_call(self, tool_call: ToolCall) -> tuple[LLMMessage, Optional[Any]]:
        """Validate a structured-output tool call.

        Returns the tool response message to append to memory and, on
        success, the validated output model. On validation failure the
        response carries the error so the model can self-repair on the next
        iteration.
        """
        try:
            output = self._output_tool.validate(tool_call.args)
            response = LLMMessage.tool(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content='{"success": true}',
            )
            return response, output
        except ValidationError as e:
            logger.warning(f"Structured output validation failed for {tool_call.name}: {e}")
            response = LLMMessage.tool(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=f"Invalid arguments, please correct and call {tool_call.name} again: {e}",
            )
            return response, None

    async def execute(
        self,
        request: str,
        output_tool: Optional[OutputTool] = None,
    ) -> AsyncGenerator[BaseEvent, None]:
        """Run the agent loop.

        The model works with native tool calling. When ``output_tool`` is
        provided, the loop finishes when the model calls it with valid
        arguments, yielding a :class:`StructuredOutputEvent`. Otherwise a
        plain assistant message ends the loop with a :class:`MessageEvent`.
        """
        token = object()
        self._execution_token = token
        self._output_tool = output_tool
        waiting = False
        try:
            next_messages = [LLMMessage.user(request)]
            for _ in range(self.max_iterations):
                message = await self.ask_with_messages(next_messages)
                next_messages = []
                if not message.tool_calls:
                    # Plain message: final answer for unstructured runs; for
                    # structured runs, nudge the model to use the output tool.
                    if not output_tool:
                        yield MessageEvent(message=message.content)
                        return
                    next_messages = [LLMMessage.user(
                        f"Submit your result by calling the `{output_tool.name}` tool."
                    )]
                    continue

                structured_output: Optional[Any] = None
                for tool_call in message.tool_calls:
                    function_name = tool_call.name
                    tool_call_id = tool_call.id
                    function_args = tool_call.args

                    if output_tool and function_name == output_tool.name:
                        response, output = self._handle_output_call(tool_call)
                        await self._add_to_memory([response])
                        if output is not None:
                            structured_output = output
                        continue

                    tool = self.get_tool(function_name)
                    if not tool:
                        # A model may occasionally hallucinate a tool name (or
                        # try to use an execution tool while the planner is
                        # only allowed to submit structured output). Keep this
                        # inside the model correction loop. Streaming an
                        # ErrorEvent here is terminal for the SSE consumer and
                        # disconnects the UI even though the agent can recover
                        # on its next turn.
                        logger.warning(
                            "Agent %s requested unavailable tool %s; asking the model to retry",
                            self.name,
                            function_name,
                        )
                        await self._add_to_memory([LLMMessage.tool(
                            tool_call_id=tool_call_id,
                            name=function_name,
                            content=(
                                f"Unknown tool: {function_name}. It is not available in the current phase. "
                                "Use one of the tools provided in the current request."
                            ),
                        )])
                        continue

                    if function_name == "message_ask_user":
                        question = function_args.get("text")
                        if not isinstance(question, str) or not question.strip():
                            await self._add_to_memory([LLMMessage.tool(
                                tool_call_id=tool_call_id, name=function_name,
                                content="Invalid arguments: message_ask_user requires a non-empty text question.",
                            )])
                            continue
                        # Store the actual question ID before exposing it. Calls
                        # after the question are explicitly not executed. The
                        # question itself is answered only by the user's reply.
                        await self._set_tool_state(tool_call_id, "waiting_user")
                        await self._repair_memory(preserve_waiting=True)
                        waiting = True
                        yield ToolEvent(
                            status=ToolStatus.CALLING, tool_call_id=tool_call_id,
                            tool_name=tool.toolkit.name, function_name=function_name,
                            function_args=function_args,
                        )
                        yield ToolEvent(
                            status=ToolStatus.CALLED, tool_call_id=tool_call_id,
                            tool_name=tool.toolkit.name, function_name=function_name,
                            function_args=function_args,
                        )
                        return

                    # Generate event before tool call
                    yield ToolEvent(
                        status=ToolStatus.CALLING,
                        tool_call_id=tool_call_id,
                        tool_name=tool.toolkit.name,
                        function_name=function_name,
                        function_args=function_args
                    )

                    await self._set_tool_state(tool_call_id, "running")
                    tool_result = await self.invoke_tool(tool, tool_call)
                    # Results must survive failures/interruptions in event
                    # rendering and delivery, and in later tools in this batch.
                    await self._add_to_memory([tool_result])

                    # Generate event after tool call
                    yield ToolEvent(
                        status=ToolStatus.CALLED,
                        tool_call_id=tool_call_id,
                        tool_name=tool.toolkit.name,
                        function_name=function_name,
                        function_args=function_args,
                        function_result=tool_result.artifact
                    )

                if structured_output is not None:
                    yield StructuredOutputEvent(output=structured_output)
                    return

            # The last batch is already closed. Do not request another assistant
            # turn that cannot be executed within this iteration budget.
            yield ErrorEvent(error="Maximum iteration count reached, failed to complete the task")
        finally:
            if self._execution_token is token:
                self._output_tool = None
                if not waiting:
                    try:
                        # A cancelled database write may have committed. Reload
                        # durable results before filling gaps, never overwrite
                        # them with an in-memory unknown-outcome placeholder.
                        await self._repair_memory(refresh=True)
                    except Exception:
                        logger.exception("Unable to close interrupted tool batch for agent %s", self._agent_id)

    async def _ensure_memory(self):
        if not self.memory:
            self.memory = await self._repository.get_memory(self._agent_id, self.name)
    
    async def _add_to_memory(self, messages: List[LLMMessage]) -> None:
        """Update memory and save to repository"""
        await self._ensure_memory()
        candidate = self.memory.model_copy(update={
            "messages": list(self.memory.messages),
            "tool_call_states": dict(self.memory.tool_call_states),
        })
        if candidate.empty:
            candidate.add_message(LLMMessage.system(self.build_system_prompt()))
        candidate.add_messages(messages)
        for message in messages:
            if message.role == Role.ASSISTANT:
                for call in message.tool_calls:
                    candidate.tool_call_states[call.id] = "pending"
            elif message.role == Role.TOOL:
                candidate.tool_call_states.pop(message.tool_call_id, None)
                if candidate.waiting_tool_call_id == message.tool_call_id:
                    candidate.waiting_tool_call_id = None
        await self._repository.save_memory(self._agent_id, self.name, candidate)
        self.memory = candidate

    async def _set_tool_state(self, call_id: str, state: str) -> None:
        candidate = self.memory.model_copy(update={
            "tool_call_states": {**self.memory.tool_call_states, call_id: state},
        })
        if state == "waiting_user":
            candidate.waiting_tool_call_id = call_id
        await self._repository.save_memory(self._agent_id, self.name, candidate)
        self.memory = candidate

    async def _repair_memory(
        self, user_reply: Optional[str] = None, *,
        preserve_waiting: bool = False, refresh: bool = False,
    ) -> None:
        if refresh:
            self.memory = await self._repository.get_memory(self._agent_id, self.name)
        else:
            await self._ensure_memory()
        candidate = self.memory.model_copy(update={
            "messages": list(self.memory.messages),
            "tool_call_states": dict(self.memory.tool_call_states),
        })
        audit = candidate.repair_tool_pairs(user_reply, preserve_waiting=preserve_waiting)
        if candidate != self.memory:
            await self._repository.save_memory(self._agent_id, self.name, candidate)
            self.memory = candidate
        if audit:
            logger.warning("Reconciled interrupted tool calls for agent %s: %s", self._agent_id, audit)
    
    async def _roll_back_memory(self) -> None:
        await self._ensure_memory()
        self.memory.roll_back()
        await self._repository.save_memory(self._agent_id, self.name, self.memory)

    async def ask_with_messages(self, messages: List[LLMMessage]) -> LLMMessage:
        # Repair earlier gaps before adding another user turn. Tool results
        # supplied by an existing caller must be applied before reconciliation.
        if messages and all(message.role == Role.TOOL for message in messages):
            await self._add_to_memory(messages)
            messages = []
        await self._repair_memory()
        if messages or self.memory.empty:
            await self._add_to_memory(messages)

        # Token-aware guard: reclaim budget from old tool results before the
        # context is sent to the model.
        if self.memory.estimate_tokens() > self.max_context_tokens:
            self.memory.compact(max_tokens=self.max_context_tokens)
            await self._repository.save_memory(self._agent_id, self.name, self.memory)

        self.memory.validate_tool_pairs()
        context = list(self.memory.get_messages())
        message = await self._llm.ask(
            messages=context,
            tools=self.get_tool_schemas(),
            tool_choice=self.tool_choice,
        )
        logger.debug(f"Response from model: {message}")

        seen_ids = {
            call.id for previous in self.memory.messages for call in previous.tool_calls
        }
        for call in message.tool_calls:
            if not call.id:
                call.id = str(uuid.uuid4())
            if call.id in seen_ids:
                raise ToolHistoryError(f"Model returned a duplicate tool call ID: {call.id}")
            seen_ids.add(call.id)
        await self._add_to_memory([message])
        return message

    async def ask(self, request: str) -> LLMMessage:
        return await self.ask_with_messages([
            LLMMessage.user(request)
        ])
    
    async def roll_back(self, message: Message):
        # Kept under the existing public name for flow compatibility. Recovery
        # now preserves real results instead of deleting an assistant turn.
        await self._repair_memory(user_reply=message.message, refresh=True)
    
    async def compact_memory(self) -> None:
        await self._ensure_memory()
        self.memory.compact()
        await self._repository.save_memory(self._agent_id, self.name, self.memory)
