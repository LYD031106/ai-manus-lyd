"""Deleting one session must not close the fixed sandbox shared by others."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.domain.models.session import Session
from app.domain.services.agent_domain_service import AgentDomainService
from app.infrastructure.external.sandbox import docker_sandbox as module


class HttpClient:
    def __init__(self):
        self.closed = False
        self.aclose = AsyncMock(side_effect=self._close)

    async def _close(self):
        self.closed = True

    async def post(self, *args, **kwargs):
        assert not self.closed, "A deleted session closed another session's client"
        return SimpleNamespace(json=lambda: {"success": True, "data": {"content": "still available"}})


def make_domain(sandbox_cls):
    domain = AgentDomainService(object(), object(), sandbox_cls, object(), object(), object(), object())
    domain._get_task = AsyncMock(return_value=SimpleNamespace(cancel=AsyncMock()))
    return domain


@pytest.mark.parametrize("lookup", ["create", "get"])
async def test_fixed_sandbox_survives_session_cleanup_and_cached_reuse(monkeypatch, lookup):
    module.DockerSandbox.get.cache_clear()
    clients = []

    def create_client(**kwargs):
        client = HttpClient()
        clients.append(client)
        return client

    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(sandbox_address="fixed-host"))
    monkeypatch.setattr(module.httpx, "AsyncClient", create_client)
    monkeypatch.setattr(module.DockerSandbox, "_resolve_hostname_to_ip", AsyncMock(return_value="127.0.0.1"))
    docker = Mock(side_effect=AssertionError("Fixed sandbox must not access Docker ownership APIs"))
    monkeypatch.setattr(module.docker, "from_env", docker)
    try:
        sandbox = await module.DockerSandbox.create() if lookup == "create" else await module.DockerSandbox.get("dev-sandbox")
        assert sandbox.id == "dev-sandbox"
        # Runner teardown is also safe for an externally owned instance.
        assert await sandbox.destroy()
        domain = make_domain(module.DockerSandbox)
        await domain.destroy_session_resources(Session(id="first", agent_id="agent", user_id="owner", sandbox_id=sandbox.id))
        domain._get_task.return_value.cancel.assert_awaited_once()
        reused = await module.DockerSandbox.get("dev-sandbox")
        assert reused is await module.DockerSandbox.get("dev-sandbox")
        if lookup == "get":
            assert reused is sandbox
        assert (await reused.file_read("/home/ubuntu/example.txt")).success
        for client in clients:
            client.aclose.assert_not_awaited()
        docker.assert_not_called()
    finally:
        module.DockerSandbox.get.cache_clear()


async def test_owned_sandbox_cleanup_closes_client_and_removes_its_container(monkeypatch):
    client = HttpClient()
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: client)
    container = SimpleNamespace(remove=Mock())
    sdk = SimpleNamespace(containers=SimpleNamespace(get=Mock(return_value=container)))
    monkeypatch.setattr(module.docker, "from_env", lambda: sdk)
    sandbox = module.DockerSandbox(ip="127.0.0.1", container_name="owned-container")
    gateway = SimpleNamespace(get=AsyncMock(return_value=sandbox))
    domain = make_domain(gateway)
    await domain.destroy_session_resources(Session(id="first", agent_id="agent", user_id="owner", sandbox_id=sandbox.id))
    domain._get_task.return_value.cancel.assert_awaited_once()
    client.aclose.assert_awaited_once()
    sdk.containers.get.assert_called_once_with("owned-container")
    container.remove.assert_called_once_with(force=True)
