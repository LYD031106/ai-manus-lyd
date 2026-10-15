"""Local accounts keep the source branch's JWT login and refresh protocol."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from app.application.errors.exceptions import BadRequestError, UnauthorizedError
from app.application.services import auth_service as auth_module
from app.application.services import token_service as token_module
from app.domain.models.user import UserRole
from app.interfaces import dependencies


@pytest.fixture
def auth(monkeypatch):
    settings = SimpleNamespace(
        auth_provider="local", password_salt="test-only", password_hash_rounds=10,
        local_auth_email="admin@example.com", local_auth_password="test-admin",
        jwt_secret_key="test-only-jwt-secret-never-used-in-production",
        jwt_algorithm="HS256", jwt_access_token_expire_minutes=30,
        jwt_refresh_token_expire_days=7,
    )
    monkeypatch.setattr(auth_module, "get_settings", lambda: settings)
    monkeypatch.setattr(token_module, "get_settings", lambda: settings)
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    users = {}

    async def save_user(user):
        users[user.id] = user
        return user

    repository = SimpleNamespace(
        create_user=AsyncMock(side_effect=save_user), update_user=AsyncMock(side_effect=save_user),
        get_user_by_id=AsyncMock(side_effect=lambda uid: users.get(uid)),
        get_user_by_email=AsyncMock(side_effect=lambda email: next(
            (user for user in users.values() if user.email == email), None)),
        email_exists=AsyncMock(side_effect=lambda email: any(
            user.email == email for user in users.values())),
    )
    return auth_module.AuthService(repository, token_module.TokenService())


@pytest.mark.parametrize("provider", ["local", "password"])
async def test_registered_user_login_bearer_identity_and_refresh(auth, caplog, provider):
    auth.settings.auth_provider = provider
    caplog.set_level("INFO", logger=auth_module.__name__)
    user = await auth.register_user("Registered User", "test-password", "user@example.com")
    login = await auth.login_with_tokens(user.email, "test-password")
    assert auth.token_service.verify_token(login.access_token)["type"] == "access"
    assert auth.token_service.verify_token(login.refresh_token)["type"] == "refresh"
    # Existing access tokens resolve the current account, including profile updates.
    user.fullname = "Updated User"
    current = await dependencies.get_current_user(
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=login.access_token), auth,
    )
    assert current.id == user.id and current.fullname == "Updated User"
    refreshed = await auth.refresh_access_token(login.refresh_token)
    assert (await auth.verify_token(refreshed.access_token)).id == user.id
    assert "test-password" not in caplog.text
    assert user.password_hash not in caplog.text


async def test_local_admin_refresh_uses_configured_identity_without_database_record(auth):
    login = await auth.login_with_tokens("admin@example.com", "test-admin")
    current = await auth.verify_token(login.access_token)
    assert current.id == "local_admin" and current.role == UserRole.ADMIN
    refreshed = await auth.refresh_access_token(login.refresh_token)
    current = await auth.verify_token(refreshed.access_token)
    assert current.id == "local_admin" and current.email == "admin@example.com"
    auth.user_repository.get_user_by_id.assert_not_awaited()


async def test_deactivated_registered_user_cannot_reuse_existing_jwt(auth):
    user = await auth.register_user("Registered User", "test-password", "user@example.com")
    login = await auth.login_with_tokens(user.email, "test-password")
    user.deactivate()
    assert await auth.authenticate_user(user.email, "test-password") is None
    assert await auth.verify_token(login.access_token) is None
    with pytest.raises(UnauthorizedError):
        await auth.refresh_access_token(login.refresh_token)


async def test_deleted_registered_user_is_not_reconstructed_from_jwt_claims(auth):
    user = await auth.register_user("Registered User", "test-password", "user@example.com")
    login = await auth.login_with_tokens(user.email, "test-password")
    auth.user_repository.get_user_by_id.side_effect = lambda _: None
    assert await auth.verify_token(login.access_token) is None
    with pytest.raises(UnauthorizedError):
        await auth.refresh_access_token(login.refresh_token)


async def test_access_and_refresh_token_roles_remain_separate(auth):
    user = await auth.register_user("Registered User", "test-password", "user@example.com")
    login = await auth.login_with_tokens(user.email, "test-password")
    assert await auth.verify_token(login.refresh_token) is None
    with pytest.raises(UnauthorizedError):
        await auth.refresh_access_token(login.access_token)


async def test_registration_stays_disabled_for_none_provider(auth):
    auth.settings.auth_provider = "none"
    with pytest.raises(BadRequestError):
        await auth.register_user("Denied User", "test-password", "denied@example.com")
