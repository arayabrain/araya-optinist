"""Behaviour tests for the /auth/refresh token-refresh contract.

A missing or blank refresh_token must produce an actionable 401
("re-login required") instead of a bare 422 from request validation, while a
present-but-invalid token keeps the existing 400 and a valid token returns the
refreshed access token.
"""

from unittest.mock import patch

import pytest
from fastapi import HTTPException, status

from studio.app.common.core.auth.auth import refresh_current_user_token
from studio.app.common.routers.auth import refresh
from studio.app.common.schemas.auth import AccessToken, RefreshToken


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


@pytest.mark.asyncio
async def test_refresh_endpoint_missing_token_returns_401():
    with pytest.raises(HTTPException) as exc_info:
        await refresh(refresh_token=RefreshToken(refresh_token=None))
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == "re-login required"
