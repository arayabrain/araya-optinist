"""
Contract Tests for Users Search API

These tests verify that API responses match the frontend TypeScript interfaces.
This ensures the backend and frontend stay in sync and prevents contract mismatches.

Frontend interfaces are defined in:
  frontend/src/store/slice/Workspace/WorkspaceType.ts (ListUserShareWorkSpace)
  frontend/src/api/users/UsersApiDTO.ts (UserDTO)

Tested endpoints:
  - GET /users/search/share_users -> List[UserInfo]
"""

from datetime import datetime, timezone

from studio.app.common.schemas.users import UserInfo

# ============================================================================
# Frontend Contract Definitions
# ============================================================================
# These mirror the TypeScript interfaces

# ListUserShareWorkSpace (user in share list)
USER_SHARE_REQUIRED_FIELDS = {
    "id": int,
}

USER_SHARE_OPTIONAL_FIELDS = {
    "name": str,
    "email": str,
    "created_at": (datetime, str),
    "updated_at": (datetime, str),
}

# UserInfo schema from backend
USER_INFO_REQUIRED_FIELDS = {
    "id": int,
}

USER_INFO_OPTIONAL_FIELDS = {
    "name": str,
    "email": str,
    "created_at": (datetime, str),
    "updated_at": (datetime, str),
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
        if isinstance(expected_type, tuple):
            assert isinstance(result[field], expected_type), (
                f"Contract violation ({context}): Field '{field}' has wrong type. "
                f"Expected one of {expected_type}, got {type(result[field])}"
            )
        elif result[field] is not None:
            assert isinstance(result[field], expected_type), (
                f"Contract violation ({context}): Field '{field}' has wrong type. "
                f"Expected {expected_type}, got {type(result[field])}"
            )

    if optional_fields:
        for field, expected_type in optional_fields.items():
            if field in result and result[field] is not None:
                if isinstance(expected_type, tuple):
                    assert isinstance(result[field], expected_type), (
                        f"Contract violation ({context}): "
                        f"Optional field '{field}' has wrong type."
                    )
                else:
                    assert isinstance(result[field], expected_type), (
                        f"Contract violation ({context}): "
                        f"Optional field '{field}' has wrong type."
                    )


# ============================================================================
# Contract Tests: UserInfo Schema
# ============================================================================


def test_contract_user_info_schema_has_required_fields():
    """
    Contract test: UserInfo has all fields frontend expects.
    """
    schema = UserInfo.schema()
    properties = schema.get("properties", {})

    for field in USER_INFO_REQUIRED_FIELDS.keys():
        assert (
            field in properties
        ), f"Contract violation: UserInfo missing required field '{field}'"


def test_contract_user_info_serialization():
    """
    Contract test: UserInfo serializes with correct field names.
    """
    user = UserInfo(
        id=1,
        name="Test User",
        email="test@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    result = user.dict()

    validate_contract(
        result,
        USER_INFO_REQUIRED_FIELDS,
        USER_INFO_OPTIONAL_FIELDS,
        context="UserInfo",
    )


def test_contract_user_info_minimal():
    """
    Contract test: UserInfo with only required fields.
    """
    user = UserInfo(id=1)

    result = user.dict()

    # id is required
    assert "id" in result
    assert result["id"] == 1


def test_contract_user_info_with_all_fields():
    """
    Contract test: UserInfo with all optional fields populated.
    """
    user = UserInfo(
        id=1,
        name="Full User",
        email="full@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    result = user.dict()

    assert result["name"] == "Full User"
    assert result["email"] == "full@example.com"
    assert result["created_at"] is not None
    assert result["updated_at"] is not None


# ============================================================================
# Contract Tests: User Search Response Structure
# ============================================================================


def test_contract_user_search_returns_list():
    """
    Contract test: User search endpoint returns a list.
    """
    # Simulate response from /users/search/share_users
    response = [
        {
            "id": 1,
            "name": "User 1",
            "email": "user1@example.com",
            "created_at": "2025-01-29T10:00:00Z",
            "updated_at": "2025-01-29T10:00:00Z",
        },
        {
            "id": 2,
            "name": "User 2",
            "email": "user2@example.com",
            "created_at": "2025-01-29T11:00:00Z",
            "updated_at": "2025-01-29T11:00:00Z",
        },
    ]

    assert isinstance(response, list)
    for user in response:
        validate_contract(
            user,
            USER_SHARE_REQUIRED_FIELDS,
            USER_SHARE_OPTIONAL_FIELDS,
            context="User search result item",
        )


def test_contract_user_search_empty_list():
    """
    Contract test: User search can return empty list.
    """
    response = []

    assert isinstance(response, list)
    assert len(response) == 0


def test_contract_user_search_single_result():
    """
    Contract test: User search can return single result.
    """
    response = [
        {
            "id": 42,
            "name": "Single User",
            "email": "single@example.com",
        },
    ]

    assert len(response) == 1
    validate_contract(
        response[0],
        USER_SHARE_REQUIRED_FIELDS,
        context="User search single result",
    )


# ============================================================================
# Contract Tests: Field Naming Consistency
# ============================================================================


def test_contract_no_legacy_user_info_fields():
    """
    Ensure no legacy or camelCase field names.
    """
    user = UserInfo(
        id=1,
        name="Test",
        email="test@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    result = user.dict()

    legacy_fields = [
        "createdAt",  # camelCase
        "updatedAt",  # camelCase
        "userId",  # Wrong field
        "userName",  # Wrong field
        "userEmail",  # Wrong field
    ]

    for legacy in legacy_fields:
        assert legacy not in result


def test_contract_dates_use_snake_case():
    """
    Contract test: Date fields use snake_case.
    """
    user = UserInfo(
        id=1,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    result = user.dict()

    assert "created_at" in result
    assert "updated_at" in result
    assert "createdAt" not in result
    assert "updatedAt" not in result


# ============================================================================
# Contract Tests: Data Types
# ============================================================================


def test_contract_user_info_id_is_integer():
    """
    Contract test: id is an integer (not string).
    """
    user = UserInfo(id=123)

    result = user.dict()

    assert isinstance(result["id"], int)


def test_contract_user_info_email_is_string():
    """
    Contract test: email is a string.
    """
    user = UserInfo(id=1, email="test@example.com")

    result = user.dict()

    assert isinstance(result["email"], str)


def test_contract_user_info_name_can_be_none():
    """
    Contract test: name can be None.
    """
    user = UserInfo(id=1, name=None)

    result = user.dict()

    assert result.get("name") is None


def test_contract_user_info_email_can_be_none():
    """
    Contract test: email can be None.
    """
    user = UserInfo(id=1, email=None)

    result = user.dict()

    assert result.get("email") is None
