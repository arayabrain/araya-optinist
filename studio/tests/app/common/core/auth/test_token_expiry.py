"""Locally issued tokens always expire.

The access token (ExToken) used to be issued without an "exp" claim and the
validator did not require one, so a leaked token stayed valid forever.
"""

from datetime import timedelta

from jose import jwt

from studio.app.common.core.auth import security
from studio.app.common.core.auth.auth_config import AUTH_CONFIG


def _claims(token: str) -> dict:
    return jwt.get_unverified_claims(token)


def test_access_token_carries_exp_by_default():
    token = security.create_access_token(subject="uid-1")

    assert "exp" in _claims(token)
    payload, err = security.validate_access_token(token)
    assert err is None
    assert payload["sub"] == "uid-1"


def test_access_token_lifetime_follows_config(monkeypatch):
    monkeypatch.setattr(AUTH_CONFIG, "ACCESS_TOKEN_EXPIRE_MINUTES", 5)

    claims = _claims(security.create_access_token(subject="uid-1"))
    lifetime = claims["exp"] - int(security.get_current_datetime().timestamp())

    assert 0 < lifetime <= 5 * 60


def test_token_without_exp_is_rejected():
    token = jwt.encode(
        {"sub": "uid-1", "token_type": "access_token"},
        AUTH_CONFIG.SECRET_KEY,
        algorithm=AUTH_CONFIG.ALGORITHM,
    )

    _, err = security.validate_access_token(token)

    assert err is not None


def test_expired_access_token_is_rejected():
    token = security.create_access_token(
        subject="uid-1", expires_delta=timedelta(seconds=-1)
    )

    _, err = security.validate_access_token(token)

    assert err == "Credentials has expired"


def test_beacon_token_still_validates():
    token = security.create_beacon_token("uid-1")

    assert security.validate_beacon_token(token) == "uid-1"
