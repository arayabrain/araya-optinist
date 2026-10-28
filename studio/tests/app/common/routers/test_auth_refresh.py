"""Behaviour tests for the /auth/refresh token-refresh contract.

A missing or blank refresh_token must produce an actionable 401
("re-login required") instead of a bare 422 from request validation, while a
present-but-invalid token keeps the existing 400 and a valid token returns the
refreshed access token.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException, status

from studio.app.common.core.auth.auth import refresh_current_user_token
from studio.app.common.schemas.auth import AccessToken, RefreshToken, Token


def test_refresh_token_schema_allows_missing_or_null():
    assert RefreshToken().refresh_token is None
    assert RefreshToken(refresh_token=None).refresh_token is None
    assert RefreshToken(refresh_token="abc").refresh_token == "abc"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [None, ""])
async def test_refresh_missing_token_returns_actionable_401(missing):
    with pytest.raises(HTTPException) as exc_info:
        await refresh_current_user_token(missing)
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == "re-login required"


@pytest.mark.asyncio
async def test_refresh_present_but_invalid_token_returns_400():
    with patch(
        "studio.app.common.core.auth.auth.validate_refresh_token",
        return_value=(None, "invalid or expired"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await refresh_current_user_token("present-but-invalid")
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_refresh_valid_token_returns_access_token():
    with patch(
        "studio.app.common.core.auth.auth.validate_refresh_token",
        return_value=({"sub": "firebase-refresh-token"}, None),
    ), patch("studio.app.common.core.auth.auth.pyrebase_app") as mock_pyrebase:
        mock_pyrebase.auth.return_value.refresh.return_value = {
            "idToken": "new-access-token"
        }
        result = await refresh_current_user_token("present-and-valid")

    assert isinstance(result, AccessToken)
    assert result.access_token == "new-access-token"


def test_refresh_endpoint_missing_token_returns_401(client):
    response = client.post("/auth/refresh", json={})
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"] == "re-login required"


AUTH = "studio.app.common.routers.auth"


OPT_IN = {"x-refresh-cookie": "1"}


def _cookie_header(response):
    return response.headers.get("set-cookie", "")


def _login(client, headers):
    token = Token(
        access_token="id", token_type="bearer", refresh_token="wrapped", ex_token=None
    )
    user = SimpleNamespace(id=1, email="a@example.com")
    with patch(f"{AUTH}.auth.authenticate_user", return_value=(token, user)), patch(
        f"{AUTH}.clear_logged_out_status"
    ), patch(f"{AUTH}.clear_free_user_logged_out_at"), patch(
        f"{AUTH}.ensure_user_bucket_exists", return_value=None
    ), patch(
        f"{AUTH}.calculate_limit_warning", return_value=None
    ):
        response = client.post(
            "/auth/login",
            json={"email": "a@example.com", "password": "pw"},
            headers=headers,
        )
    client.cookies.clear()
    return response


def test_login_sets_an_http_only_refresh_cookie(client):
    response = _login(client, {**OPT_IN, "x-forwarded-proto": "https"})

    assert response.status_code == 200
    assert response.json()["refresh_token"] == "wrapped"
    cookie = _cookie_header(response).lower()
    assert cookie.startswith("refresh_token=wrapped;")
    for attr in ("httponly", "samesite=strict", "path=/auth", "secure"):
        assert attr in cookie


@pytest.mark.parametrize(
    "headers",
    [{}, {**OPT_IN, "origin": "https://sibling.example"}, {**OPT_IN, "origin": "null"}],
)
def test_login_sets_no_cookie_unless_its_own_origin_opts_in(client, headers):
    response = _login(client, headers)

    assert response.status_code == 200
    assert "refresh_token=" not in _cookie_header(response)


def test_refresh_never_copies_a_body_token_into_the_cookie(client):
    with patch(
        f"{AUTH}.auth.refresh_current_user_token",
        return_value=AccessToken(access_token="new"),
    ) as refresh_token:
        response = client.post(
            "/auth/refresh", json={"refresh_token": "legacy"}, headers=OPT_IN
        )
    client.cookies.clear()

    assert response.json() == {"access_token": "new"}
    refresh_token.assert_called_once_with("legacy")
    assert "refresh_token=" not in _cookie_header(response)


def test_refresh_with_an_invalid_body_token_sets_no_cookie(client):
    with patch(
        f"{AUTH}.auth.refresh_current_user_token",
        side_effect=HTTPException(status_code=400),
    ):
        response = client.post(
            "/auth/refresh", json={"refresh_token": "bad"}, headers=OPT_IN
        )

    assert response.status_code == 400
    assert "refresh_token=" not in _cookie_header(response)


@pytest.mark.parametrize(
    "origin, expected",
    [
        (None, "from-cookie"),
        ("http://testserver", "from-cookie"),
        ("https://evil.example", None),
    ],
)
def test_refresh_reads_the_cookie_only_from_its_own_origin(client, origin, expected):
    headers = {"origin": origin} if origin else {}
    client.cookies.set("refresh_token", "from-cookie", path="/auth")
    with patch(
        f"{AUTH}.auth.refresh_current_user_token",
        return_value=AccessToken(access_token="new"),
    ) as refresh_token:
        client.post("/auth/refresh", json={}, headers=headers)
    client.cookies.clear()

    refresh_token.assert_called_once_with(expected)


def test_logout_clears_the_refresh_cookie(client):
    response = client.post("/auth/logout", headers={"x-forwarded-proto": "https"})

    assert response.status_code == 204
    cookie = _cookie_header(response).lower()
    assert cookie.startswith('refresh_token="";') or "max-age=0" in cookie
    for attr in ("httponly", "samesite=strict", "path=/auth", "secure"):
        assert attr in cookie
