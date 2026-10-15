"""Local self-registration must use the main branch's opaque auth sessions."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials

from app.application.errors.exceptions import BadRequestError, UnauthorizedError
from app.application.services import auth_service as auth_module
from app.domain.models.auth_session import AuthClientType
from app.domain.models.user import UserRole
from app.interfaces import dependencies


@pytest.fixture
def auth(monkeypatch):
    settings = SimpleNamespace(
        auth_provider="local", password_salt="test-only", password_hash_rounds=10,
        local_auth_email="admin@example.com", local_auth_password="test-admin",
        session_web_ttl_days=14, session_app_ttl_days=30,
        session_jwt_grace_enabled=True, session_cookie_name="session_id",
    )
    monkeypatch.setattr(auth_module, "get_settings", lambda: settings)
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    users = {}
    sessions = {}

    async def save_user(user):
        users[user.id] = user
        return user

    async def create_session(session, ttl):
        sessions[session.session_id] = session

    async def delete_session(token):
        return sessions.pop(token, None) is not None

    repository = SimpleNamespace(
        create_user=AsyncMock(side_effect=save_user),
        update_user=AsyncMock(side_effect=save_user),
        get_user_by_id=AsyncMock(side_effect=lambda uid: users.get(uid)),
        get_user_by_email=AsyncMock(side_effect=lambda email: next(
            (user for user in users.values() if user.email == email), None)),
        email_exists=AsyncMock(side_effect=lambda email: any(
            user.email == email for user in users.values())),
    )
    store = SimpleNamespace(
        create=AsyncMock(side_effect=create_session),
        get=AsyncMock(side_effect=lambda token: sessions.get(token)),
        touch=AsyncMock(), delete=AsyncMock(side_effect=delete_session),
    )
    tokens = SimpleNamespace(verify_token=Mock(return_value=None))
    service = auth_module.AuthService(repository, tokens, store)
    return service


@pytest.mark.parametrize("source", ["cookie", "bearer"])
async def test_local_register_login_current_user_and_rotating_refresh(auth, source, caplog):
    caplog.set_level("INFO", logger=auth_module.__name__)
    user = await auth.register_user("Local User", "test-password", "user@example.com")
    # Registration endpoint also immediately creates an opaque session.
    registration_session = await auth.create_auth_session(user)
    assert (await auth.verify_token(registration_session.session_id)).id == user.id
    logged_in = await auth.login_with_session(user.email, "test-password")
    assert logged_in.access_token != "test-password"
    assert logged_in.access_token == logged_in.refresh_token
    headers = [(b"cookie", f"session_id={logged_in.access_token}".encode())] if source == "cookie" else []
    request = Request({"type": "http", "method": "GET", "headers": headers})
    bearer = HTTPAuthorizationCredentials(scheme="Bearer", credentials=logged_in.access_token) if source == "bearer" else None
    current = await dependencies.get_current_user(request, bearer, auth)
    assert current.id == user.id
    assert current.role == UserRole.USER
    refreshed = await auth.refresh_access_token(cookie_session_id=logged_in.access_token, rotate=True)
    assert refreshed.access_token != logged_in.access_token
    assert await auth.verify_token(logged_in.access_token) is None
    assert (await auth.verify_token(refreshed.access_token)).id == user.id
    auth.token_service.verify_token.assert_called()
    assert "test-password" not in caplog.text
    assert user.password_hash not in caplog.text


async def test_local_admin_still_uses_configured_identity_and_refresh(auth):
    logged_in = await auth.login_with_session("admin@example.com", "test-admin", client=AuthClientType.IOS)
    current = await auth.verify_token(logged_in.access_token)
    assert current.id == "local_admin" and current.role == UserRole.ADMIN
    assert (await auth.refresh_access_token(logged_in.access_token)).access_token == logged_in.access_token
    auth.user_repository.get_user_by_id.assert_not_awaited()


async def test_deactivated_local_account_rejected_by_session_and_jwt_grace(auth):
    user = await auth.register_user("Local User", "test-password", "user@example.com")
    logged_in = await auth.login_with_session(user.email, "test-password")
    user.deactivate()
    assert await auth.authenticate_user(user.email, "test-password") is None
    assert await auth.verify_token(logged_in.access_token) is None
    with pytest.raises(UnauthorizedError):
        await auth.refresh_access_token(logged_in.access_token)
    auth.token_service.verify_token.return_value = {"sub": user.id, "type": "access", "is_active": True}
    assert await auth.verify_token("legacy-access-token") is None
    auth.token_service.verify_token.return_value = {"sub": user.id, "type": "refresh"}
    with pytest.raises(UnauthorizedError):
        await auth.refresh_access_token("legacy-refresh-token")


async def test_cookie_mutations_still_require_csrf_header(auth):
    user = await auth.register_user("Local User", "test-password", "user@example.com")
    session = await auth.create_auth_session(user)
    scope = {"type": "http", "method": "POST", "headers": [
        (b"cookie", f"session_id={session.session_id}".encode())]}
    with pytest.raises(UnauthorizedError):
        await dependencies.get_current_user(Request(scope), None, auth)
    scope["headers"].append((b"x-requested-with", b"XMLHttpRequest"))
    assert (await dependencies.get_current_user(Request(scope), None, auth)).id == user.id


async def test_password_mode_and_registration_disabled_mode_still_work(auth):
    auth.settings.auth_provider = "password"
    user = await auth.register_user("Password User", "test-password", "user@example.com")
    login = await auth.login_with_session(user.email, "test-password")
    assert (await auth.verify_token(login.access_token)).id == user.id
    auth.settings.auth_provider = "none"
    with pytest.raises(BadRequestError):
        await auth.register_user("Denied User", "test-password", "denied@example.com")
