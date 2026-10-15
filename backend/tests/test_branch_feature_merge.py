"""Offline contracts spanning main features and lightweight deployment features."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.config import Settings
from app.domain.models.event import (
    BrowserToolContent, ErrorEvent, FileUpdateEvent, MessageEvent,
    TerminalUpdateEvent, ToolEvent, ToolStatus,
)
from app.domain.models.file import FileInfo
from app.domain.models.session import Session, TaskMode
from app.domain.models.user import User
from app.domain.services.agent_domain_service import AgentDomainService
from app.domain.services.prompts import system as system_prompt
from app.domain.services.tools.parse_regulation import ParseRegulationToolkit
from app.infrastructure.external.sandbox import docker_sandbox
from app.interfaces import dependencies
from app.interfaces.api import config_routes, session_routes
from app.interfaces.schemas.event import EventMapper


@pytest.fixture
def signed_urls(monkeypatch):
    service = SimpleNamespace(create_signed_url=AsyncMock(side_effect=lambda value: f"signed:{value}"))
    monkeypatch.setattr(dependencies, "get_file_service", lambda: service)
    return service


async def test_history_uses_lazy_attachment_urls_and_keeps_main_session_metadata(signed_urls):
    attachment = FileInfo(file_id="file-1", filename="report.txt")
    session = Session(
        id="session", agent_id="agent", user_id="owner", is_pinned=True,
        is_favorite=True, project_id="project", task_mode=TaskMode.CHAT,
        events=[MessageEvent(id="history-id", role="user", message="attached", attachments=[attachment])],
    )
    service = SimpleNamespace(get_session_history=AsyncMock(return_value=session))
    result = await session_routes.get_session("session", User(id="owner", fullname="Owner", email="owner@example.com"), service)
    service.get_session_history.assert_awaited_once_with("session", "owner")
    assert result.data.is_pinned and result.data.is_favorite
    assert result.data.project_id == "project" and result.data.task_mode == TaskMode.CHAT
    assert result.data.events[0].data.event_id == "history-id"
    assert result.data.events[0].data.attachments[0].file_url is None
    signed_urls.create_signed_url.assert_not_awaited()


async def test_stream_mapper_keeps_signed_files_browser_and_live_updates(signed_urls):
    attachment = FileInfo(file_id="file-1", filename="report.txt")
    events = [
        MessageEvent(message="done", attachments=[attachment]),
        TerminalUpdateEvent(shell_id="shell", output=["console"]),
        FileUpdateEvent(path="/tmp/report", content="new", old_content="old", file=attachment),
        ToolEvent(tool_call_id="browser-call", tool_name="browser", function_name="browser_view",
                  function_args={}, status=ToolStatus.CALLED, tool_content=BrowserToolContent(screenshot="shot")),
    ]
    mapped = await EventMapper.events_to_stream_events(events)
    assert [event.event for event in mapped] == ["message", "terminal_update", "file_update", "tool"]
    assert mapped[0].data.attachments[0].file_url == "signed:file-1"
    assert mapped[1].data.output == ["console"]
    assert mapped[2].data.old_content == "old" and mapped[2].data.file.file_url == "signed:file-1"
    assert mapped[3].data.content.screenshot == "signed:shot"


@pytest.mark.parametrize("enabled", [True, False])
async def test_frontend_config_exposes_browser_and_claw_capabilities(monkeypatch, enabled):
    settings = Settings(_env_file=None, api_key="test-only", browser_enabled=enabled, claw_enabled=enabled)
    monkeypatch.setattr(config_routes, "get_settings", lambda: settings)
    result = await config_routes.get_frontend_config()
    assert result.data.browser_enabled is enabled
    assert result.data.claw_enabled is enabled


@pytest.mark.parametrize("enabled", [True, False])
def test_prompt_matches_sandbox_mode_and_keeps_project_instructions(monkeypatch, enabled):
    monkeypatch.setattr(system_prompt, "get_settings", lambda: SimpleNamespace(browser_enabled=enabled))
    prompt = system_prompt.build_system_prompt(project_instruction="Project instruction")
    assert ("Node.js 20" in prompt) is enabled
    assert ("heavyweight runtimes are not installed" in prompt) is (not enabled)
    assert "Project instruction" in prompt


async def test_regulation_tool_rejects_documents_without_downloading_or_using_model():
    sandbox = SimpleNamespace(file_download=AsyncMock())
    toolkit = ParseRegulationToolkit(sandbox)
    tool = toolkit.get_tool("parse_regulation")
    result = await tool.invoke({"files": ["/home/ubuntu/input.pdf"], "output": "/home/ubuntu/result.md"})
    assert result.success is False
    assert "parse_office.py" in result.message
    sandbox.file_download.assert_not_awaited()


@pytest.mark.parametrize("limited", [False, True])
@pytest.mark.parametrize("browser_enabled", [False, True])
def test_sandbox_limits_are_opt_in_and_forwarded_when_configured(monkeypatch, limited, browser_enabled):
    for name in ("SANDBOX_MEM_LIMIT", "SANDBOX_NANO_CPUS", "SANDBOX_PIDS_LIMIT"):
        monkeypatch.delenv(name, raising=False)
    options = {"sandbox_mem_limit": "256m", "sandbox_nano_cpus": 500_000_000, "sandbox_pids_limit": 64} if limited else {}
    settings = Settings(_env_file=None, api_key="test-only", browser_enabled=browser_enabled, **options)
    monkeypatch.setattr(docker_sandbox, "get_settings", lambda: settings)
    container = SimpleNamespace(attrs={"NetworkSettings": {"IPAddress": "127.0.0.1"}}, reload=Mock())
    client = SimpleNamespace(containers=SimpleNamespace(run=Mock(return_value=container)))
    monkeypatch.setattr(docker_sandbox.docker, "from_env", lambda: client)
    monkeypatch.setattr(docker_sandbox.httpx, "AsyncClient", Mock(return_value=SimpleNamespace()))
    sandbox = docker_sandbox.DockerSandbox._create_task()
    assert sandbox.ip == "127.0.0.1"
    kwargs = client.containers.run.call_args.kwargs
    for key in ("mem_limit", "nano_cpus", "pids_limit"):
        if limited:
            assert kwargs[key] == options[f"sandbox_{key}"]
        else:
            assert key not in kwargs
    if browser_enabled:
        assert "cap_drop" not in kwargs and "security_opt" not in kwargs
    else:
        assert kwargs["cap_drop"] == ["ALL"]
        assert kwargs["security_opt"] == ["no-new-privileges:true"]


def make_domain(repository):
    return AgentDomainService(
        agent_repository=object(), session_repository=repository,
        sandbox_cls=SimpleNamespace(get=AsyncMock()), task_cls=object(),
        file_storage=object(), mcp_repository=object(), llm=object(),
    )


async def test_attachment_only_input_is_saved_before_sandbox_startup_failure():
    session = Session(id="session", user_id="owner", agent_id="agent")
    saved = []
    repository = SimpleNamespace(
        find_by_id_and_user_id=AsyncMock(return_value=session),
        update_latest_message=AsyncMock(), update_unread_message_count=AsyncMock(),
        add_event=AsyncMock(side_effect=lambda sid, event: saved.append(event.model_copy(deep=True))),
    )
    domain = make_domain(repository)
    domain._get_task = AsyncMock(return_value=None)

    async def failed_start(_):
        assert len(saved) == 1 and isinstance(saved[0], MessageEvent)
        raise RuntimeError("sandbox unavailable")

    domain._create_task = AsyncMock(side_effect=failed_start)
    output = [event async for event in domain.chat("session", "owner", attachments=[FileInfo(file_id="file-1", filename="report.txt")])]
    assert len(output) == 1 and isinstance(output[0], ErrorEvent)
    assert saved[0].role == "user" and saved[0].message
    assert saved[0].attachments[0].file_id == "file-1"


async def test_session_resource_cleanup_cancels_task_and_destroys_owned_sandbox():
    domain = make_domain(object())
    task = SimpleNamespace(cancel=AsyncMock())
    sandbox = SimpleNamespace(destroy=AsyncMock(return_value=True))
    domain._get_task = AsyncMock(return_value=task)
    domain._sandbox_cls.get.return_value = sandbox
    session = Session(id="session", user_id="owner", agent_id="agent", sandbox_id="owned")
    await domain.destroy_session_resources(session)
    task.cancel.assert_awaited_once()
    domain._sandbox_cls.get.assert_awaited_once_with("owned")
    sandbox.destroy.assert_awaited_once()
