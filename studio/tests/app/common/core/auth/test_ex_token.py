from datetime import timedelta
from types import SimpleNamespace

import pytest

from studio.app.common.core.auth import auth as auth_module
from studio.app.common.core.auth.auth_config import AUTH_CONFIG
from studio.app.common.core.auth.security import validate_access_token
from studio.app.common.core.utils.datetime_utils import get_current_datetime
from studio.tests.app.common.core.auth import test_deactivated_user_login as harness

db = harness.db
firebase_accepts_the_password = harness.firebase_accepts_the_password


@pytest.mark.asyncio
async def test_no_ex_token_when_firebase_verifies(
    db, firebase_accepts_the_password, monkeypatch
):
    monkeypatch.setattr(AUTH_CONFIG, "USE_FIREBASE_TOKEN", True)
    harness.seed(db, active=True)

    token, _ = await auth_module.authenticate_user(db, harness.CREDENTIALS)

    assert token.ex_token is None


@pytest.mark.asyncio
async def test_no_ex_token_on_proxy_login_when_firebase_verifies(
    db, firebase_accepts_the_password, monkeypatch
):
    monkeypatch.setattr(AUTH_CONFIG, "USE_FIREBASE_TOKEN", True)
    harness.seed(db, active=True)
    pyrebase_auth = firebase_accepts_the_password.auth.return_value
    pyrebase_auth.sign_in_with_custom_token.return_value = {
        "idToken": "id-token",
        "refreshToken": "refresh-token",
    }

    token = await auth_module.login_with_uid(
        db, harness.UID, SimpleNamespace(is_admin=True)
    )

    assert token.ex_token is None


@pytest.mark.asyncio
async def test_ex_token_lifetime_follows_the_access_token_setting(
    db, firebase_accepts_the_password, monkeypatch
):
    monkeypatch.setattr(AUTH_CONFIG, "USE_FIREBASE_TOKEN", False)
    # Unequal lifetimes, so the token's exp shows which setting it took
    monkeypatch.setattr(AUTH_CONFIG, "ACCESS_TOKEN_EXPIRE_MINUTES", 5)
    monkeypatch.setattr(AUTH_CONFIG, "REFRESH_TOKEN_EXPIRE_MINUTES", 60 * 24)
    harness.seed(db, active=True)

    token, user = await auth_module.authenticate_user(db, harness.CREDENTIALS)

    payload, err = validate_access_token(token.ex_token)
    assert err is None
    assert payload["sub"] == user.uid
    expected = get_current_datetime() + timedelta(minutes=5)
    assert abs(payload["exp"] - expected.timestamp()) < 60
