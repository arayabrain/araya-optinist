from datetime import timedelta

import pytest
from jose import jwt

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
async def test_ex_token_expires_with_the_refresh_token(
    db, firebase_accepts_the_password, monkeypatch
):
    monkeypatch.setattr(AUTH_CONFIG, "USE_FIREBASE_TOKEN", False)
    harness.seed(db, active=True)

    token, user = await auth_module.authenticate_user(db, harness.CREDENTIALS)

    payload, err = validate_access_token(token.ex_token)
    assert err is None
    assert payload["sub"] == user.uid
    expected = get_current_datetime() + timedelta(
        minutes=AUTH_CONFIG.REFRESH_TOKEN_EXPIRE_MINUTES
    )
    assert abs(payload["exp"] - expected.timestamp()) < 60
    refresh = jwt.get_unverified_claims(token.refresh_token)
    assert abs(payload["exp"] - refresh["exp"]) < 60
