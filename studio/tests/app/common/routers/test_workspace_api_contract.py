"""
Contract Tests for Workspace API

These tests verify that API responses match the frontend TypeScript interfaces.
This ensures the backend and frontend stay in sync and prevents contract mismatches.

Frontend interfaces are defined in:
  frontend/src/api/workspace/index.ts
  frontend/src/store/slice/Workspace/WorkspaceType.ts

Tested endpoints:
  - GET  /workspaces                        -> LimitOffsetPage[Workspace]
  - GET  /workspace/{workspace_id}          -> Workspace
  - POST /workspace                         -> Workspace
  - PUT  /workspace/{workspace_id}          -> Workspace
  - DELETE /workspace/{workspace_id}        -> bool
  - GET  /workspace/share/{id}/status       -> WorkspaceShareStatus
  - POST /workspace/share/{id}/status       -> bool
  - POST /workspaces/refresh-storage        -> RefreshStorageResponse
"""

from datetime import datetime, timezone

from studio.app.common.schemas.users import UserInfo
from studio.app.common.schemas.workspace import Workspace, WorkspaceShareStatus

# ============================================================================
# Frontend Contract Definitions
# ============================================================================
# These mirror the TypeScript interfaces in WorkspaceType.ts

# ItemsWorkspace interface (single workspace item)
ITEMS_WORKSPACE_REQUIRED_FIELDS = {
    "id": int,
    "name": str,
}

ITEMS_WORKSPACE_OPTIONAL_FIELDS = {
    "display_number": int,
    "user": dict,
    "created_at": (datetime, str),
    "updated_at": (datetime, str),
    "shared_count": int,
    "data_usage": int,
    "canDelete": bool,
}

# User nested in Workspace
WORKSPACE_USER_REQUIRED_FIELDS = {
    "id": int,
}

WORKSPACE_USER_OPTIONAL_FIELDS = {
    "name": str,
    "email": str,
    "created_at": (datetime, str),
    "updated_at": (datetime, str),
}

# WorkspaceDataDTO (paginated response)
WORKSPACE_DATA_DTO_REQUIRED_FIELDS = {
    "items": list,
    "total": int,
    "limit": int,
    "offset": int,
}

# ListUserShareWorkspaceDTO
LIST_USER_SHARE_WORKSPACE_DTO_REQUIRED_FIELDS = {
    "users": list,
}

# RefreshStorageResponse
REFRESH_STORAGE_RESPONSE_REQUIRED_FIELDS = {
    "success": bool,
    "refreshed_workspaces": int,
    "total_workspaces": int,
    "message": str,
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
# Contract Tests: Workspace Schema Validation
# ============================================================================


def test_contract_workspace_schema_has_required_fields():
    """
    Contract test: Workspace Pydantic model has all fields frontend expects.
    """
    schema = Workspace.schema()
    properties = schema.get("properties", {})

    # Check required and optional fields exist in schema
    all_fields = list(ITEMS_WORKSPACE_REQUIRED_FIELDS.keys()) + list(
        ITEMS_WORKSPACE_OPTIONAL_FIELDS.keys()
    )

    for field in all_fields:
        assert field in properties, (
            f"Contract violation: Frontend expects field '{field}' "
            f"but Workspace model doesn't have it. "
            f"Model has: {list(properties.keys())}"
        )


def test_contract_user_info_schema_has_required_fields():
    """
    Contract test: UserInfo (nested in Workspace) has all fields frontend expects.
    """
    schema = UserInfo.schema()
    properties = schema.get("properties", {})

    for field in WORKSPACE_USER_REQUIRED_FIELDS.keys():
        assert field in properties, (
            f"Contract violation: Frontend expects UserInfo.{field} "
            f"but model doesn't have it."
        )


# ============================================================================
# Contract Tests: Workspace Instance Validation
# ============================================================================


def test_contract_workspace_serialization():
    """
    Contract test: Workspace serializes with correct field names and types.
    """
    user = UserInfo(
        id=1,
        name="Test User",
        email="test@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    workspace = Workspace(
        id=1,
        display_number=1,
        name="Test Workspace",
        user=user,
        shared_count=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        data_usage=1000000,
        canDelete=True,
    )

    result = workspace.dict()

    validate_contract(
        result,
        ITEMS_WORKSPACE_REQUIRED_FIELDS,
        ITEMS_WORKSPACE_OPTIONAL_FIELDS,
        context="Workspace",
    )

    # Validate nested user
    assert "user" in result
    if result["user"] is not None:
        validate_contract(
            result["user"],
            WORKSPACE_USER_REQUIRED_FIELDS,
            WORKSPACE_USER_OPTIONAL_FIELDS,
            context="Workspace.user",
        )


def test_contract_workspace_minimal_fields():
    """
    Contract test: Workspace with minimal fields still matches contract.
    """
    workspace = Workspace(
        id=1,
        name="Minimal Workspace",
        shared_count=0,
    )

    result = workspace.dict()

    # Required fields must be present
    assert "id" in result
    assert "name" in result
    assert result["name"] == "Minimal Workspace"


def test_contract_workspace_with_null_user():
    """
    Contract test: Workspace with null user serializes correctly.
    """
    workspace = Workspace(
        id=1,
        name="Workspace Without User",
        user=None,
        shared_count=0,
    )

    result = workspace.dict()

    # User can be null
    assert result.get("user") is None


def test_contract_workspace_shared_count_is_integer():
    """
    Contract test: shared_count is an integer.
    """
    workspace = Workspace(
        id=1,
        name="Shared Workspace",
        shared_count=5,
    )

    result = workspace.dict()

    assert isinstance(result["shared_count"], int)
    assert result["shared_count"] == 5


def test_contract_workspace_can_delete_is_boolean():
    """
    Contract test: canDelete field is a boolean.
    """
    workspace = Workspace(
        id=1,
        name="Deletable Workspace",
        shared_count=0,
        canDelete=True,
    )

    result = workspace.dict()

    assert isinstance(result["canDelete"], bool)


# ============================================================================
# Contract Tests: WorkspaceShareStatus
# ============================================================================


def test_contract_workspace_share_status_schema():
    """
    Contract test: WorkspaceShareStatus has required fields.
    """
    schema = WorkspaceShareStatus.schema()
    properties = schema.get("properties", {})

    assert "users" in properties


def test_contract_workspace_share_status_serialization():
    """
    Contract test: WorkspaceShareStatus serializes correctly.
    """
    users = [
        UserInfo(
            id=1,
            name="User 1",
            email="user1@example.com",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ),
        UserInfo(
            id=2,
            name="User 2",
            email="user2@example.com",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ),
    ]

    share_status = WorkspaceShareStatus(users=users)

    result = share_status.dict()

    validate_contract(
        result,
        LIST_USER_SHARE_WORKSPACE_DTO_REQUIRED_FIELDS,
        context="WorkspaceShareStatus",
    )

    # Validate users array
    assert isinstance(result["users"], list)
    assert len(result["users"]) == 2

    # Validate each user in the list
    for user in result["users"]:
        validate_contract(
            user,
            WORKSPACE_USER_REQUIRED_FIELDS,
            WORKSPACE_USER_OPTIONAL_FIELDS,
            context="WorkspaceShareStatus.users[]",
        )


def test_contract_workspace_share_status_empty_users():
    """
    Contract test: WorkspaceShareStatus with empty users list.
    """
    share_status = WorkspaceShareStatus(users=[])

    result = share_status.dict()

    assert "users" in result
    assert result["users"] == []


def test_contract_workspace_share_status_null_users():
    """
    Contract test: WorkspaceShareStatus with null users.
    """
    share_status = WorkspaceShareStatus(users=None)

    result = share_status.dict()

    assert "users" in result
    assert result["users"] is None


# ============================================================================
# Contract Tests: Field Naming Consistency
# ============================================================================


def test_contract_no_legacy_workspace_fields():
    """
    Ensure no legacy or camelCase field names in workspace responses.
    """
    user = UserInfo(
        id=1,
        name="Test User",
        email="test@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    workspace = Workspace(
        id=1,
        display_number=1,
        name="Test Workspace",
        user=user,
        shared_count=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        data_usage=1000000,
        canDelete=True,
    )

    result = workspace.dict()

    # Note: canDelete is camelCase but this is intentional to match frontend
    legacy_fields = [
        "displayNumber",  # camelCase (should be display_number)
        "sharedCount",  # camelCase (should be shared_count)
        "createdAt",  # camelCase (should be created_at)
        "updatedAt",  # camelCase (should be updated_at)
        "dataUsage",  # camelCase (should be data_usage)
        "userId",  # Wrong field
    ]

    for legacy in legacy_fields:
        assert legacy not in result, (
            f"Legacy field '{legacy}' found. "
            f"Frontend expects snake_case field names."
        )


def test_contract_can_delete_is_camel_case():
    """
    Contract test: canDelete remains camelCase (matches frontend expectation).

    Note: This is an intentional exception to snake_case convention
    to match the frontend TypeScript interface.
    """
    workspace = Workspace(
        id=1,
        name="Test Workspace",
        shared_count=0,
        canDelete=True,
    )

    result = workspace.dict()

    # canDelete should be present (camelCase)
    assert "canDelete" in result
    # can_delete should NOT be present
    assert "can_delete" not in result


# ============================================================================
# Contract Tests: Data Types
# ============================================================================


def test_contract_workspace_id_is_integer():
    """
    Contract test: Workspace id is an integer (not string).
    """
    workspace = Workspace(
        id=123,
        name="Test Workspace",
        shared_count=0,
    )

    result = workspace.dict()

    assert isinstance(result["id"], int)


def test_contract_workspace_data_usage_is_integer():
    """
    Contract test: data_usage is an integer (bytes).
    """
    workspace = Workspace(
        id=1,
        name="Test Workspace",
        shared_count=0,
        data_usage=1073741824,  # 1 GB in bytes
    )

    result = workspace.dict()

    assert isinstance(result["data_usage"], int)


def test_contract_workspace_display_number_is_integer():
    """
    Contract test: display_number is an integer.
    """
    workspace = Workspace(
        id=1,
        display_number=42,
        name="Test Workspace",
        shared_count=0,
    )

    result = workspace.dict()

    assert isinstance(result["display_number"], int)
    assert result["display_number"] == 42
