"""
Contract Tests for Auth API

These tests verify that API responses match the frontend TypeScript interfaces.
This ensures the backend and frontend stay in sync and prevents contract mismatches.

Frontend interfaces are defined in:
  frontend/src/api/auth/Auth.ts

Tested endpoints:
  - POST /auth/login    -> TokenDTO
  - POST /auth/refresh  -> AccessTokenDTO
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from studio.app.common.schemas.auth import AccessToken, Token

# ============================================================================
# Frontend Contract Definitions
# ============================================================================
# These mirror the TypeScript interfaces in Auth.ts

# TokenDTO interface (login response)
TOKEN_DTO_REQUIRED_FIELDS = {
    "access_token": str,
    "token_type": str,
}

TOKEN_DTO_OPTIONAL_FIELDS = {
    "refresh_token": str,
    "ex_token": str,
}

# AccessTokenDTO interface (refresh response)
ACCESS_TOKEN_DTO_REQUIRED_FIELDS = {
    "access_token": str,
}


# ============================================================================
# Contract Validation Helpers
# ============================================================================


def validate_contract(
    result: dict,
    required_fields: dict,
    optional_fields: dict = None,
    context: str = "",
) -> None:
    """
    Validate that a response matches the frontend contract.
    """
    for field, expected_type in required_fields.items():
        assert field in result, (
            f"Contract violation ({context}): Missing required field '{field}'. "
            f"Response has: {list(result.keys())}"
        )
        assert isinstance(result[field], expected_type), (
            f"Contract violation ({context}): Field '{field}' has wrong type. "
            f"Expected {expected_type}, got {type(result[field])}"
        )

    if optional_fields:
        for field, expected_type in optional_fields.items():
            if field in result and result[field] is not None:
                assert isinstance(result[field], expected_type), (
                    f"Contract violation ({context}): "
                    f"Optional field '{field}' has wrong type. "
                    f"Expected {expected_type}, got {type(result[field])}"
                )


def validate_pydantic_model_contract(
    model_class,
    required_fields: dict,
    optional_fields: dict = None,
    context: str = "",
) -> None:
    """
    Validate that a Pydantic model's schema matches the frontend contract.
    """
    schema = model_class.schema()
    properties = schema.get("properties", {})

    # Check all frontend required fields are in model
    for field in required_fields.keys():
        assert field in properties, (
            f"Contract violation ({context}): "
            f"Frontend requires field '{field}' but model doesn't have it. "
            f"Model has: {list(properties.keys())}"
        )

    # Check optional fields exist if specified
    if optional_fields:
        for field in optional_fields.keys():
            assert field in properties, (
                f"Contract violation ({context}): "
                f"Frontend expects optional field '{field}' but model doesn't have it."
            )


# ============================================================================
# Contract Tests: Token Schema Validation
# ============================================================================


def test_contract_token_schema_matches_frontend():
    """
    Contract test: Token Pydantic model has all fields frontend expects.
    """
    validate_pydantic_model_contract(
        Token,
        TOKEN_DTO_REQUIRED_FIELDS,
        TOKEN_DTO_OPTIONAL_FIELDS,
        context="Token schema",
    )


def test_contract_access_token_schema_matches_frontend():
    """
    Contract test: AccessToken Pydantic model has all fields frontend expects.
    """
    validate_pydantic_model_contract(
        AccessToken,
        ACCESS_TOKEN_DTO_REQUIRED_FIELDS,
        context="AccessToken schema",
    )


# ============================================================================
# Contract Tests: Token Instance Validation
# ============================================================================


def test_contract_token_dto_serialization():
    """
    Contract test: Token serializes to dict with correct field names.
    """
    token = Token(
        access_token="test_access_token",
        token_type="bearer",
        refresh_token="test_refresh_token",
        ex_token="test_ex_token",
    )

    result = token.dict()

    validate_contract(
        result,
        TOKEN_DTO_REQUIRED_FIELDS,
        TOKEN_DTO_OPTIONAL_FIELDS,
        context="TokenDTO",
    )

    # Verify exact field names match frontend expectations
    assert "access_token" in result, "Frontend expects 'access_token' (snake_case)"
    assert "token_type" in result, "Frontend expects 'token_type' (snake_case)"
    assert "refresh_token" in result, "Frontend expects 'refresh_token' (snake_case)"
    assert "ex_token" in result, "Frontend expects 'ex_token' (not 'extra_token')"


def test_contract_token_dto_with_optional_fields_none():
    """
    Contract test: Token with None optional fields still matches contract.
    """
    token = Token(
        access_token="test_access_token",
        token_type="bearer",
        refresh_token=None,
        ex_token=None,
    )

    result = token.dict()

    # Required fields must be present
    validate_contract(
        result,
        TOKEN_DTO_REQUIRED_FIELDS,
        context="TokenDTO (optional None)",
    )

    # Optional fields can be None
    assert result.get("refresh_token") is None
    assert result.get("ex_token") is None


def test_contract_access_token_dto_serialization():
    """
    Contract test: AccessToken serializes to dict with correct field names.
    """
    access_token = AccessToken(access_token="refreshed_access_token")

    result = access_token.dict()

    validate_contract(
        result,
        ACCESS_TOKEN_DTO_REQUIRED_FIELDS,
        context="AccessTokenDTO",
    )


# ============================================================================
# Contract Tests: Login Endpoint Response
# ============================================================================


@pytest.mark.asyncio
async def test_contract_login_response_format():
    """
    Contract test: Login endpoint returns TokenDTO format.
    """
    with patch("studio.app.common.routers.auth.auth") as mock_auth:
        with patch(
            "studio.app.common.routers.auth.calculate_limit_warning"
        ) as mock_warning:
            # Mock successful authentication
            mock_token = Token(
                access_token="jwt_token_here",
                token_type="bearer",
                refresh_token="refresh_token_here",
                ex_token="ex_token_here",
            )
            mock_user = Mock()
            mock_user.id = 1
            mock_user.email = "test@example.com"

            mock_auth.authenticate_user = AsyncMock(
                return_value=(mock_token, mock_user)
            )
            mock_warning.return_value = None

            from studio.app.common.routers.auth import login
            from studio.app.common.schemas.auth import UserAuth

            user_data = UserAuth(email="test@example.com", password="Password123!")
            mock_db = Mock()

            result = await login(user_data=user_data, db=mock_db)

            # Result should be Token model, convert to dict for validation
            result_dict = result.dict()

            validate_contract(
                result_dict,
                TOKEN_DTO_REQUIRED_FIELDS,
                TOKEN_DTO_OPTIONAL_FIELDS,
                context="Login response",
            )


@pytest.mark.asyncio
async def test_contract_refresh_response_format():
    """
    Contract test: Refresh endpoint returns AccessTokenDTO format.
    """
    with patch("studio.app.common.routers.auth.auth") as mock_auth:
        mock_access_token = AccessToken(access_token="new_jwt_token")
        mock_auth.refresh_current_user_token = AsyncMock(return_value=mock_access_token)

        from studio.app.common.routers.auth import refresh
        from studio.app.common.schemas.auth import RefreshToken

        refresh_token_data = RefreshToken(refresh_token="old_refresh_token")

        result = await refresh(refresh_token=refresh_token_data)

        # Result should be AccessToken model
        result_dict = result.dict()

        validate_contract(
            result_dict,
            ACCESS_TOKEN_DTO_REQUIRED_FIELDS,
            context="Refresh response",
        )


# ============================================================================
# Legacy Field Detection Tests
# ============================================================================


def test_no_legacy_token_fields():
    """
    Ensure no legacy or incorrectly named fields exist.
    """
    token = Token(
        access_token="test",
        token_type="bearer",
        refresh_token="test",
        ex_token="test",
    )

    result = token.dict()

    # Check for potential legacy field names
    legacy_fields = [
        "accessToken",  # camelCase variant
        "tokenType",  # camelCase variant
        "refreshToken",  # camelCase variant
        "exToken",  # camelCase variant
        "extra_token",  # Wrong name for ex_token
        "jwt",  # Alternative name
        "bearer_token",  # Alternative name
    ]

    for legacy in legacy_fields:
        assert legacy not in result, (
            f"Legacy field '{legacy}' found in Token response. "
            f"Frontend expects snake_case field names."
        )


def test_token_type_value():
    """
    Verify token_type has expected value.
    """
    # Most OAuth implementations use "bearer" or "Bearer"
    token = Token(
        access_token="test",
        token_type="bearer",
        refresh_token="test",
        ex_token="test",
    )

    assert (
        token.token_type.lower() == "bearer"
    ), "token_type should be 'bearer' for standard OAuth2"


# ============================================================================
# Contract Tests: Login Clears logged_out_at (Case 58/62)
# ============================================================================


@pytest.mark.asyncio
async def test_contract_login_clears_logged_out_status():
    """
    Contract test: Login endpoint clears logged_out_at for free users.

    This prevents the cleanup job from deleting a user's data after they
    re-login. See ALERT_FIX_PLAN Case 58/62.
    """
    with patch("studio.app.common.routers.auth.auth") as mock_auth:
        with patch(
            "studio.app.common.routers.auth.calculate_limit_warning"
        ) as mock_warning:
            with patch(
                "studio.app.common.routers.auth.clear_logged_out_status"
            ) as mock_clear_status:
                with patch(
                    "studio.app.common.routers.auth.clear_free_user_logged_out_at"
                ) as mock_clear_logged_out_at:
                    mock_token = Token(
                        access_token="jwt_token_here",
                        token_type="bearer",
                        refresh_token="refresh_token_here",
                        ex_token="ex_token_here",
                    )
                    mock_user = Mock()
                    mock_user.id = 42
                    mock_user.email = "test@example.com"

                    mock_auth.authenticate_user = AsyncMock(
                        return_value=(mock_token, mock_user)
                    )
                    mock_warning.return_value = None

                    from studio.app.common.routers.auth import login
                    from studio.app.common.schemas.auth import UserAuth

                    user_data = UserAuth(
                        email="test@example.com", password="Password123!"
                    )
                    mock_db = Mock()

                    await login(user_data=user_data, db=mock_db)

                    # Verify both clear functions were called with user.id
                    mock_clear_status.assert_called_once_with(42)
                    mock_clear_logged_out_at.assert_called_once_with(42)


@pytest.mark.asyncio
async def test_contract_login_continues_on_clear_failure():
    """
    Contract test: Login should succeed even if clearing logged_out_at fails.

    The clear operation is best-effort - login should not fail if it errors.
    """
    with patch("studio.app.common.routers.auth.auth") as mock_auth:
        with patch(
            "studio.app.common.routers.auth.calculate_limit_warning"
        ) as mock_warning:
            with patch(
                "studio.app.common.routers.auth.clear_logged_out_status"
            ) as mock_clear_status:
                with patch(
                    "studio.app.common.routers.auth.clear_free_user_logged_out_at"
                ) as mock_clear_logged_out_at:
                    mock_token = Token(
                        access_token="jwt_token_here",
                        token_type="bearer",
                        refresh_token="refresh_token_here",
                        ex_token="ex_token_here",
                    )
                    mock_user = Mock()
                    mock_user.id = 42
                    mock_user.email = "test@example.com"

                    mock_auth.authenticate_user = AsyncMock(
                        return_value=(mock_token, mock_user)
                    )
                    mock_warning.return_value = None

                    # Make clear function raise exception
                    mock_clear_status.side_effect = Exception("DB connection failed")
                    mock_clear_logged_out_at.side_effect = Exception(
                        "DB connection failed"
                    )

                    from studio.app.common.routers.auth import login
                    from studio.app.common.schemas.auth import UserAuth

                    user_data = UserAuth(
                        email="test@example.com", password="Password123!"
                    )
                    mock_db = Mock()

                    # Should not raise - login should continue
                    result = await login(user_data=user_data, db=mock_db)

                    # Login should still succeed
                    assert result.access_token == "jwt_token_here"
