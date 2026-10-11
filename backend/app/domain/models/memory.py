import json
import logging
from pydantic import BaseModel, Field
from typing import Dict, List, Optional
from app.domain.models.message import LLMMessage, Role, ToolCall

logger = logging.getLogger(__name__)

# Rough chars-per-token ratio; conservative for mixed prose/code/JSON.
_CHARS_PER_TOKEN = 4

# Placeholder written over elided tool results. Kept as a ToolResult-shaped
# JSON string so downstream consumers can still parse the content.
_ELIDED_CONTENT = json.dumps(
    {"status": "result_elided", "message": "(result elided to save context)", "data": None}
)


class ToolHistoryError(ValueError):
    """An ambiguous tool history that cannot be repaired without inventing facts."""


def estimate_tokens(text: str) -> int:
    """Cheap, dependency-free token estimate for context budgeting."""
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


class Memory(BaseModel):
    """Agent conversation memory with token-aware compaction.

    Messages are stored append-only; :meth:`compact` reclaims context budget
    by eliding the *content* of old tool results (oldest first) while keeping
    the message skeleton intact, so tool-call pairing required by LLM APIs is
    never broken and recent working context is preserved.
    """
    messages: List[LLMMessage] = []
    # Only unfinished calls are retained here. Historical results live in messages.
    tool_call_states: Dict[str, str] = Field(default_factory=dict)
    waiting_tool_call_id: Optional[str] = None

    def add_message(self, message: LLMMessage) -> None:
        """Add message to memory"""
        self.messages.append(message)
    
    def add_messages(self, messages: List[LLMMessage]) -> None:
        """Add messages to memory"""
        self.messages.extend(messages)

    def get_messages(self) -> List[LLMMessage]:
        """Get all message history"""
        return self.messages
    
    def get_last_message(self) -> Optional[LLMMessage]:
        """Get the last message"""
        if len(self.messages) > 0:  
            return self.messages[-1]
        return None
    
    def roll_back(self) -> None:
        """Roll back memory"""
        self.messages = self.messages[:-1]

    def _missing_tool_batches(self) -> List[tuple[int, List[ToolCall]]]:
        """Validate IDs and locate missing replies without changing this memory."""
        missing = []
        seen = set()
        pending: Dict[str, ToolCall] = {}
        batch_ids = set()
        for index, message in enumerate(self.messages):
            if message.role == Role.TOOL:
                call_id = message.tool_call_id
                if not call_id or call_id not in batch_ids:
                    raise ToolHistoryError(f"Orphan tool response at message {index}: {call_id!r}")
                if call_id not in pending:
                    raise ToolHistoryError(f"Duplicate tool response at message {index}: {call_id}")
                pending.pop(call_id)
                continue
            if pending:
                missing.append((index, list(pending.values())))
            pending = {}
            batch_ids = set()
            if message.role == Role.ASSISTANT:
                for call in message.tool_calls:
                    if not call.id or call.id in seen:
                        raise ToolHistoryError(f"Empty or duplicate tool call ID at message {index}: {call.id!r}")
                    seen.add(call.id)
                    pending[call.id] = call
                    batch_ids.add(call.id)
        if pending:
            missing.append((len(self.messages), list(pending.values())))
        return missing

    def validate_tool_pairs(self) -> None:
        """Raise before a model request if any call has no adjacent tool reply."""
        missing = self._missing_tool_batches()
        if missing:
            ids = [call.id for _, calls in missing for call in calls]
            raise ToolHistoryError(f"Missing tool responses: {', '.join(ids)}")

    def repair_tool_pairs(
        self,
        user_reply: Optional[str] = None,
        *,
        preserve_waiting: bool = False,
    ) -> List[dict]:
        """Close interrupted batches without executing tools or claiming success.

        This in-memory, IO-free migration API returns an audit entry per added
        reply. IDs and existing replies are validated before any mutation. A
        user reply can answer only a pending question in the final batch; older
        gaps receive explicit unknown-outcome results. ``preserve_waiting`` is
        used while suspending an active run, never before a model request.
        """
        batches = self._missing_tool_batches()
        insertions: Dict[int, List[LLMMessage]] = {}
        audit = []
        retained_states = {}
        retained_waiting = None
        for boundary, calls in batches:
            is_tail = boundary == len(self.messages)
            question_id = None
            if is_tail:
                questions = [call.id for call in calls if call.name == "message_ask_user"]
                if self.waiting_tool_call_id in questions:
                    question_id = self.waiting_tool_call_id
                elif user_reply is not None and questions:
                    # Legacy runs did not store the ID of the displayed question.
                    # Execution stopped at the first ask-user call in that batch.
                    question_id = questions[0]
            for call in calls:
                if call.id == question_id and user_reply is not None:
                    content = user_reply
                    status = "user_replied"
                elif call.id == question_id and preserve_waiting:
                    retained_states[call.id] = "waiting_user"
                    retained_waiting = call.id
                    continue
                else:
                    state = self.tool_call_states.get(call.id) if is_tail else None
                    status = "not_executed" if state in ("pending", "not_executed") else "outcome_unknown"
                    detail = (
                        "This call was not executed because the tool batch was interrupted."
                        if status == "not_executed" else
                        "Execution was interrupted before a result was durably recorded. "
                        "The operation may already have taken effect. Inspect the current state "
                        "before retrying an operation with side effects."
                    )
                    content = json.dumps({"success": False, "status": status, "message": detail})
                insertions.setdefault(boundary, []).append(LLMMessage.tool(
                    tool_call_id=call.id, name=call.name, content=content,
                ))
                audit.append({"tool_call_id": call.id, "name": call.name, "status": status})
        repaired = []
        for index in range(len(self.messages) + 1):
            repaired.extend(insertions.get(index, []))
            if index < len(self.messages):
                repaired.append(self.messages[index])
        self.messages = repaired
        self.tool_call_states = retained_states
        self.waiting_tool_call_id = retained_waiting
        return audit

    def estimate_tokens(self) -> int:
        """Estimate the total token footprint of the stored messages."""
        total = 0
        for message in self.messages:
            total += estimate_tokens(message.content)
            for tool_call in message.tool_calls:
                total += estimate_tokens(tool_call.name)
                total += estimate_tokens(json.dumps(tool_call.args, default=str))
        return total

    def compact(self, max_tokens: int = 0, keep_recent: int = 10) -> None:
        """Elide old tool results until the memory fits the token budget.

        Args:
            max_tokens: Target context budget. ``0`` means "compact all
                eligible tool results" (unconditional cleanup between steps).
            keep_recent: Number of most recent messages that are never
                touched, so the model keeps its working context.
        """
        if max_tokens and self.estimate_tokens() <= max_tokens:
            return

        cutoff = max(0, len(self.messages) - keep_recent)
        for message in self.messages[:cutoff]:
            if message.role != Role.TOOL:
                continue
            if message.content == _ELIDED_CONTENT:
                continue
            try:
                result = json.loads(message.content)
            except (ValueError, TypeError):
                result = None
            if isinstance(result, dict) and result.get("status") in ("outcome_unknown", "not_executed"):
                # Keep the evidence boundary that prevents accidental replay.
                continue
            message.content = _ELIDED_CONTENT
            logger.debug(f"Elided old tool result from memory: {message.name}")
            if max_tokens and self.estimate_tokens() <= max_tokens:
                return

    @property
    def empty(self) -> bool:
        """Check if memory is empty"""
        return len(self.messages) == 0
