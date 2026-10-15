"""Persisted UUID anchors must not reach Redis XREAD as stream IDs."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.event import DoneEvent, ErrorEvent, MessageEvent
from app.domain.models.session import Session, SessionStatus
from app.domain.services.agent_domain_service import AgentDomainService
from app.application.services.agent_service import AgentService
from app.domain.models.file import FileInfo


def make_domain(events, status=SessionStatus.RUNNING):
    session = Session(id="session", user_id="owner", agent_id="agent", events=events, status=status)
    repository = SimpleNamespace(
        find_by_id_and_user_id=AsyncMock(return_value=session),
        update_latest_message=AsyncMock(), add_event=AsyncMock(),
        update_unread_message_count=AsyncMock(),
    )
    done = DoneEvent(id="2000-0")
    task = SimpleNamespace(
        is_done=AsyncMock(return_value=True), run=AsyncMock(),
        input_stream=SimpleNamespace(put=AsyncMock(return_value="1999-0")),
        output_stream=SimpleNamespace(get=AsyncMock(return_value=(done.id, done.model_dump_json()))),
    )
    domain = AgentDomainService(object(), repository, object(), object(), object(), object(), object())
    domain._get_task = AsyncMock(return_value=task)
    domain._create_task = AsyncMock(return_value=task)
    return domain, task, repository


async def test_running_join_after_only_persisted_user_starts_at_safe_cursor_without_execution():
    user = MessageEvent(role="user", message="original input")
    domain, task, repository = make_domain([user])
    events = [event async for event in domain.chat("session", "owner", latest_event_id=user.id)]
    assert len(events) == 1 and isinstance(events[0], DoneEvent)
    task.output_stream.get.assert_awaited_once_with(start_id="0-0", block_ms=1000)
    task.input_stream.put.assert_not_awaited()
    task.run.assert_not_awaited()
    domain._create_task.assert_not_awaited()
    repository.add_event.assert_not_awaited()


async def test_history_uuid_uses_preceding_output_cursor_not_legacy_input_id():
    output = MessageEvent(id="1000-3", role="assistant", message="previous output")
    legacy_input = MessageEvent(id="1001-0", role="user", message="older input")
    newest_input = MessageEvent(role="user", message="latest input")
    domain, task, _ = make_domain([output, legacy_input, newest_input])
    assert [event async for event in domain.chat("session", "owner", latest_event_id=newest_input.id)]
    task.output_stream.get.assert_awaited_once_with(start_id="1000-3", block_ms=1000)
    task.run.assert_not_awaited()


async def test_new_message_after_uuid_startup_error_runs_once_and_reads_valid_cursor():
    failed_input = MessageEvent(role="user", message="failed input")
    startup_error = ErrorEvent(error="sandbox failed before output existed")
    domain, task, repository = make_domain([failed_input, startup_error], SessionStatus.COMPLETED)
    events = [event async for event in domain.chat("session", "owner", message="try again", latest_event_id=startup_error.id)]
    assert len(events) == 1 and isinstance(events[0], DoneEvent)
    task.output_stream.get.assert_awaited_once_with(start_id="0-0", block_ms=1000)
    task.run.assert_awaited_once()
    task.input_stream.put.assert_awaited_once()
    domain._create_task.assert_awaited_once()
    assert repository.add_event.await_count == 1
    assert repository.add_event.await_args.args[1].message == "try again"


@pytest.mark.parametrize("cursor, expected", [
    ("1234567890-42", "1234567890-42"),
    (None, None),
    ("unknown-history-anchor", "0-0"),
    ("18446744073709551616-0", "0-0"),
    ("123-0\n", "0-0"),
])
async def test_valid_stream_cursor_preserved_and_unknown_cursor_replays_only(cursor, expected):
    domain, task, repository = make_domain([])
    assert [event async for event in domain.chat("session", "owner", latest_event_id=cursor)]
    task.output_stream.get.assert_awaited_once_with(start_id=expected, block_ms=1000)
    task.input_stream.put.assert_not_awaited()
    task.run.assert_not_awaited()
    repository.add_event.assert_not_awaited()


@pytest.mark.parametrize("attachment_only", [False, True])
async def test_service_allows_none_message_for_sse_resume_and_attachments(attachment_only):
    calls = []
    attachments = [FileInfo(file_id="file", filename="report.txt")] if attachment_only else None

    async def chat(*args):
        calls.append(args)
        yield DoneEvent()

    service = object.__new__(AgentService)
    service._agent_domain_service = SimpleNamespace(chat=chat)
    events = [event async for event in service.chat(
        "session", "owner", message=None, event_id="123-0", attachments=attachments,
    )]
    assert len(events) == 1 and isinstance(events[0], DoneEvent)
    assert calls == [("session", "owner", None, None, "123-0", attachments)]
