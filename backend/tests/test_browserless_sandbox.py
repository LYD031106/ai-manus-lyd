from types import SimpleNamespace

import pytest

from app.core.config import Settings, get_settings
from app.domain.services.agent_task_runner import AgentTaskRunnerFactory
from app.domain.services.flows.plan_act import PlanActFlow
from app.domain.services.tools.browser import BrowserToolkit


class FakeSandbox:
    def __init__(self) -> None:
        self.get_browser_calls = 0

    async def get_browser(self):
        self.get_browser_calls += 1
        return object()


class FakeSandboxType:
    instance = FakeSandbox()

    @classmethod
    async def get(cls, sandbox_id: str):
        assert sandbox_id == "sandbox-test"
        return cls.instance


def _make_flow(browser):
    return PlanActFlow(
        agent_id="agent-test",
        agent_repository=object(),
        session_id="session-test",
        session_repository=object(),
        sandbox=object(),
        browser=browser,
        mcp_tool=SimpleNamespace(),
        llm=object(),
    )


def test_browser_enabled_defaults_to_true(monkeypatch):
    monkeypatch.delenv("BROWSER_ENABLED", raising=False)
    assert Settings().browser_enabled is True


def test_browser_toolkit_is_omitted_without_browser():
    flow = _make_flow(browser=None)

    assert not any(
        isinstance(toolkit, BrowserToolkit)
        for toolkit in flow.executor.toolkits
    )


def test_browser_toolkit_is_retained_with_browser():
    flow = _make_flow(browser=object())

    assert any(
        isinstance(toolkit, BrowserToolkit)
        for toolkit in flow.executor.toolkits
    )


@pytest.mark.asyncio
async def test_runner_factory_does_not_request_browser_when_disabled(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "false")
    get_settings.cache_clear()
    FakeSandboxType.instance = FakeSandbox()
    factory = AgentTaskRunnerFactory(
        agent_repository=object(),
        session_repository=object(),
        sandbox_cls=FakeSandboxType,
        file_storage=object(),
        mcp_repository=object(),
        llm=object(),
    )

    runner = await factory.create_runner(
        {
            "session_id": "session-test",
            "agent_id": "agent-test",
            "user_id": "user-test",
            "sandbox_id": "sandbox-test",
        }
    )

    assert runner._browser is None
    assert FakeSandboxType.instance.get_browser_calls == 0
    get_settings.cache_clear()
