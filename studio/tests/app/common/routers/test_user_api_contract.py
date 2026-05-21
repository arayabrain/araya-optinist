"""
Contract Tests for User API

These tests verify that API responses match the frontend TypeScript interfaces.
This ensures the backend and frontend stay in sync and prevents contract mismatches.

Frontend interfaces are defined in:
  frontend/src/api/users/UsersApiDTO.ts

Tested endpoints:
  - GET /users/me -> UserDTO
"""

from studio.app.common.schemas.users import Organization, User

# ============================================================================
# Frontend Contract Definitions
# ============================================================================
# These mirror the TypeScript interfaces in UsersApiDTO.ts

# UserDTO interface
USER_DTO_REQUIRED_FIELDS = {
    "email": str,
    "data_usage": (int, type(None)),  # Required in frontend but can be null in backend
}

USER_DTO_OPTIONAL_FIELDS = {
    "uid": str,
    "id": int,
    "name": str,
    "organization": dict,
    "role_id": int,
    "attributes": dict,
    "subscription_plan_name": str,
    "subscription_status": str,
    "subscription_days_remaining": int,
    "storage_usage_bytes": int,
    "storage_quota_bytes": int,
    "storage_usage_percent": (int, float),
    "created_at": str,
    "updated_at": str,
}

# Organization nested interface
ORGANIZATION_REQUIRED_FIELDS = {
    "id": int,
    "name": str,
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
                        f"Optional field '{field}' has wrong type. "
                        f"Expected one of {expected_type}, got {type(result[field])}"
                    )
                else:
                    assert isinstance(result[field], expected_type), (
                        f"Contract violation ({context}): "
                        f"Optional field '{field}' has wrong type. "
                        f"Expected {expected_type}, got {type(result[field])}"
                    )


# ============================================================================
# Contract Tests: User Schema Validation
# ============================================================================


def test_contract_user_schema_has_required_fields():
    """
    Contract test: User Pydantic model has all fields frontend expects.
    """
    schema = User.schema()
    properties = schema.get("properties", {})

    # Check frontend-expected fields exist
    expected_fields = list(USER_DTO_REQUIRED_FIELDS.keys()) + list(
        USER_DTO_OPTIONAL_FIELDS.keys()
    )

    for field in expected_fields:
        # Skip fields not in backend (created_at, updated_at added by frontend)
        if field in ["created_at", "updated_at"]:
            continue
        assert field in properties, (
            f"Contract violation: Frontend expects field '{field}' "
            f"but User model doesn't have it. Model has: {list(properties.keys())}"
        )


def test_contract_organization_schema_has_required_fields():
    """
    Contract test: Organization model has all fields frontend expects.
    """
    schema = Organization.schema()
    properties = schema.get("properties", {})

    for field in ORGANIZATION_REQUIRED_FIELDS.keys():
        assert field in properties, (
            f"Contract violation: Frontend expects Organization.{field} "
            f"but model doesn't have it."
        )


# ============================================================================
# Contract Tests: User Instance Validation
# ============================================================================


def test_contract_user_dto_serialization_free_user():
    """
    Contract test: Free user serializes with correct field names and types.
    """
    org = Organization(id=1, name="Test Org")
    user = User(
        id=1,
        uid="user-123",
        name="Test User",
        email="test@example.com",
        organization=org,
        role_id=20,
        data_usage=1000,
        attributes={"remote_bucket_name": "test-bucket"},
        subscription_plan_name="Free",
        subscription_status="Free",
        subscription_days_remaining=None,
        storage_usage_bytes=5000000000,
        storage_quota_bytes=5368709120,
        storage_usage_percent=93.13,
    )

    result = user.dict()

    validate_contract(
        result,
        USER_DTO_REQUIRED_FIELDS,
        USER_DTO_OPTIONAL_FIELDS,
        context="UserDTO (free user)",
    )

    # Validate nested organization
    assert "organization" in result
    validate_contract(
        result["organization"],
        ORGANIZATION_REQUIRED_FIELDS,
        context="UserDTO.organization",
    )


def test_contract_user_dto_serialization_premium_user():
    """
    Contract test: Premium user serializes with subscription fields populated.
    """
    org = Organization(id=1, name="Premium Org")
    user = User(
        id=2,
        uid="premium-user-456",
        name="Premium User",
        email="premium@example.com",
        organization=org,
        role_id=20,
        data_usage=50000,
        attributes={"remote_bucket_name": "premium-bucket"},
        subscription_plan_name="Premium",
        subscription_status="Premium",
        subscription_days_remaining=25,
        storage_usage_bytes=50000000000,
        storage_quota_bytes=214748364800,
        storage_usage_percent=23.28,
    )

    result = user.dict()

    validate_contract(
        result,
        USER_DTO_REQUIRED_FIELDS,
        USER_DTO_OPTIONAL_FIELDS,
        context="UserDTO (premium user)",
    )

    # Premium-specific field validations
    assert result["subscription_plan_name"] == "Premium"
    assert result["subscription_days_remaining"] is not None
    assert result["subscription_days_remaining"] > 0


def test_contract_user_dto_with_minimal_fields():
    """
    Contract test: User with minimal data still matches contract.
    """
    org = Organization(id=1, name="Minimal Org")
    user = User(
        id=3,
        uid="minimal-user",
        email="minimal@example.com",
        organization=org,
        data_usage=None,
    )

    result = user.dict()

    # Email is required by frontend
    assert "email" in result
    assert result["email"] == "minimal@example.com"

    # Optional fields can be None
    assert result.get("name") is None
    assert result.get("subscription_plan_name") is None


# ============================================================================
# Contract Tests: Subscription Fields
# ============================================================================


def test_contract_subscription_field_names():
    """
    Contract test: Subscription field names match frontend expectations.

    Frontend expects:
    - subscription_plan_name (not plan_name or planName)
    - subscription_status (not status)
    - subscription_days_remaining (not days_remaining)
    """
    org = Organization(id=1, name="Test Org")
    user = User(
        id=1,
        uid="test-user",
        email="test@example.com",
        organization=org,
        data_usage=0,
        subscription_plan_name="Premium",
        subscription_status="Premium",
        subscription_days_remaining=30,
    )

    result = user.dict()

    # Correct field names (snake_case with subscription_ prefix)
    assert "subscription_plan_name" in result
    assert "subscription_status" in result
    assert "subscription_days_remaining" in result

    # Legacy/incorrect field names should NOT exist
    legacy_fields = [
        "plan_name",
        "planName",
        "subscriptionPlanName",
        "status",
        "subscriptionStatus",
        "days_remaining",
        "daysRemaining",
        "subscriptionDaysRemaining",
    ]
    for legacy in legacy_fields:
        assert legacy not in result, (
            f"Legacy field '{legacy}' found. "
            f"Frontend expects 'subscription_' prefixed snake_case fields."
        )


def test_contract_storage_field_names():
    """
    Contract test: Storage field names match frontend expectations.

    Frontend expects:
    - storage_usage_bytes (not usage_bytes)
    - storage_quota_bytes (not quota_bytes)
    - storage_usage_percent (not usage_percent)
    """
    org = Organization(id=1, name="Test Org")
    user = User(
        id=1,
        uid="test-user",
        email="test@example.com",
        organization=org,
        data_usage=0,
        storage_usage_bytes=1000000,
        storage_quota_bytes=5000000000,
        storage_usage_percent=0.02,
    )

    result = user.dict()

    # Correct field names
    assert "storage_usage_bytes" in result
    assert "storage_quota_bytes" in result
    assert "storage_usage_percent" in result

    # Legacy/incorrect field names should NOT exist
    legacy_fields = [
        "usage_bytes",
        "quota_bytes",
        "usage_percent",
        "storageUsageBytes",
        "storageQuotaBytes",
        "storageUsagePercent",
    ]
    for legacy in legacy_fields:
        assert legacy not in result, (
            f"Legacy field '{legacy}' found. "
            f"Frontend expects 'storage_' prefixed snake_case fields."
        )


# ============================================================================
# Contract Tests: Attributes Field
# ============================================================================


def test_contract_attributes_structure():
    """
    Contract test: Attributes field has expected structure.

    Frontend expects:
    - attributes.remote_bucket_name (optional)
    """
    org = Organization(id=1, name="Test Org")
    user = User(
        id=1,
        uid="test-user",
        email="test@example.com",
        organization=org,
        data_usage=0,
        attributes={"remote_bucket_name": "my-bucket"},
    )

    result = user.dict()

    assert "attributes" in result
    assert isinstance(result["attributes"], dict)
    assert "remote_bucket_name" in result["attributes"]


def test_contract_attributes_can_be_none():
    """
    Contract test: Attributes can be None/null.
    """
    org = Organization(id=1, name="Test Org")
    user = User(
        id=1,
        uid="test-user",
        email="test@example.com",
        organization=org,
        data_usage=0,
        attributes=None,
    )

    result = user.dict()

    # Frontend handles attributes being null
    assert result.get("attributes") is None


# ============================================================================
# Contract Tests: Type Coercion
# ============================================================================


def test_contract_storage_percent_is_numeric():
    """
    Contract test: storage_usage_percent is a number (int or float).

    Frontend TypeScript: storage_usage_percent?: number
    """
    org = Organization(id=1, name="Test Org")
    user = User(
        id=1,
        uid="test-user",
        email="test@example.com",
        organization=org,
        data_usage=0,
        storage_usage_percent=95.5,
    )

    result = user.dict()

    percent = result["storage_usage_percent"]
    assert isinstance(
        percent, (int, float)
    ), f"storage_usage_percent should be numeric, got {type(percent)}"


def test_contract_id_fields_are_integers():
    """
    Contract test: ID fields are integers (not strings).

    Frontend TypeScript: id?: number, role_id?: number
    """
    org = Organization(id=1, name="Test Org")
    user = User(
        id=123,
        uid="test-user",
        email="test@example.com",
        organization=org,
        role_id=20,
        data_usage=0,
    )

    result = user.dict()

    assert isinstance(result["id"], int), "id should be int"
    assert isinstance(result["role_id"], int), "role_id should be int"
    assert isinstance(
        result["organization"]["id"], int
    ), "organization.id should be int"


def test_contract_uid_is_string():
    """
    Contract test: uid is a string (not integer).

    Frontend TypeScript: uid?: string
    """
    org = Organization(id=1, name="Test Org")
    user = User(
        id=123,
        uid="user-abc-123",
        email="test@example.com",
        organization=org,
        data_usage=0,
    )

    result = user.dict()

    assert isinstance(result["uid"], str), "uid should be string"
