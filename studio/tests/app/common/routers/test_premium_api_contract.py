"""
Contract Tests for Premium Assignment API

These tests verify that API responses match the frontend TypeScript interfaces.
This ensures the backend and frontend stay in sync and prevents contract mismatches.

Frontend interfaces are defined in:
  frontend/src/api/premium/PremiumAssignmentApi.ts

Tested endpoints:
  - GET  /users/me/routing-info     -> RoutingInfo
  - POST /users/me/premium/assign   -> PremiumAssignmentResult
  - DELETE /users/me/premium/assign -> PremiumReleaseResult
  - GET  /users/me/premium/status   -> PremiumStatusResult
  - POST /users/me/premium/heartbeat -> PremiumHeartbeatResult
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from studio.app.common.core.subscription.constants import SubscriptionType

# ============================================================================
# Frontend Contract Definitions
# ============================================================================
# These mirror the TypeScript interfaces in PremiumAssignmentApi.ts

# RoutingInfo interface
ROUTING_INFO_REQUIRED_FIELDS = {
    "user_id": str,
    "user_tier": str,
    "requires_premium_routing": bool,
    "routing_headers": dict,
}

# PremiumAssignmentResult interface
PREMIUM_ASSIGNMENT_REQUIRED_FIELDS = {
    "message": str,
    "assigned": bool,
}

PREMIUM_ASSIGNMENT_OPTIONAL_FIELDS = {
    "instance_id": str,
    "instance_id_hash": str,
    "retry_after": (int, float),
    "scaling_in_progress": bool,
    "is_shared": bool,
    "assignment_source": str,
}

# PremiumReleaseResult interface
PREMIUM_RELEASE_REQUIRED_FIELDS = {
    "message": str,
    "released": bool,
}

PREMIUM_RELEASE_OPTIONAL_FIELDS = {
    "released_instance": str,
}

# PremiumStatusResult interface
PREMIUM_STATUS_REQUIRED_FIELDS = {
    "user_id": (str, int),  # Backend returns string uid
    "subscription_type": str,
    "is_premium": bool,
}

PREMIUM_STATUS_OPTIONAL_FIELDS = {
    "assignment": (dict, type(None)),
    "migration_ready": bool,
    "health_status": str,
    "error": str,
}

# PremiumAssignment (nested in PremiumStatusResult)
PREMIUM_ASSIGNMENT_NESTED_FIELDS = {
    "instance_id": str,
    "instance_id_hash": str,
    "assigned_at": str,
    "status": str,
    "is_shared": bool,
}

# PremiumHeartbeatResult interface
PREMIUM_HEARTBEAT_REQUIRED_FIELDS = {
    "message": str,
    "updated": bool,
    "user_id": (str, int),
    "user_tier": str,
    "assignment_active": bool,
}

PREMIUM_HEARTBEAT_OPTIONAL_FIELDS = {
    "activity_update": bool,
    "error": str,
}

# Valid user tier values (from frontend const/Subscription.ts)
VALID_USER_TIERS = {"free", "premium", "Free", "Premium"}


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

    Args:
        result: The API response dictionary
        required_fields: Dict of field_name -> expected_type
        optional_fields: Dict of field_name -> expected_type (optional)
        context: Description for error messages
    """
    # Check all required fields are present with correct types
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
        else:
            assert isinstance(result[field], expected_type), (
                f"Contract violation ({context}): Field '{field}' has wrong type. "
                f"Expected {expected_type}, got {type(result[field])}"
            )

    # Check optional fields have correct types if present
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
# Fixtures
# ============================================================================


@pytest.fixture
def mock_free_user():
    """Mock free tier user"""
    user = Mock()
    user.id = 1
    user.uid = "free-user-123"
    user.subscription_type = SubscriptionType.FREE.value
    return user


@pytest.fixture
def mock_premium_user():
    """Mock premium tier user"""
    user = Mock()
    user.id = 2
    user.uid = "premium-user-456"
    user.subscription_type = SubscriptionType.PREMIUM.value
    return user


# ============================================================================
# Contract Tests: GET /users/me/routing-info
# ============================================================================


@pytest.mark.asyncio
async def test_contract_routing_info_free_user(mock_free_user):
    """
    Contract test: RoutingInfo response for free user matches frontend interface.
    """
    from studio.app.common.routers.users_me import get_routing_info

    result = await get_routing_info(current_user=mock_free_user)

    validate_contract(result, ROUTING_INFO_REQUIRED_FIELDS, context="RoutingInfo")

    # Semantic validation
    assert result["requires_premium_routing"] is False
    assert result["user_tier"] in VALID_USER_TIERS


@pytest.mark.asyncio
async def test_contract_routing_info_premium_user(mock_premium_user):
    """
    Contract test: RoutingInfo response for premium user matches frontend interface.
    """
    from studio.app.common.routers.users_me import get_routing_info

    result = await get_routing_info(current_user=mock_premium_user)

    validate_contract(result, ROUTING_INFO_REQUIRED_FIELDS, context="RoutingInfo")

    # Semantic validation
    assert result["requires_premium_routing"] is True
    assert result["user_tier"] in VALID_USER_TIERS


# ============================================================================
# Contract Tests: POST /users/me/premium/assign
# ============================================================================


@pytest.mark.asyncio
async def test_contract_premium_assign_success(mock_premium_user):
    """
    Contract test: PremiumAssignmentResult on successful assignment.
    """
    with patch(
        "studio.app.common.routers.users_me.premium_assignment_service"
    ) as mock_service:
        mock_service.assign_premium_user = AsyncMock(
            return_value={
                "success": True,
                "message": "Assigned to instance i-123456",
                "instance_id": "i-123456",
                "is_shared": False,
                "assignment_source": "new_assignment",
            }
        )

        from studio.app.common.routers.users_me import assign_premium_instance

        result = await assign_premium_instance(current_user=mock_premium_user)

        validate_contract(
            result,
            PREMIUM_ASSIGNMENT_REQUIRED_FIELDS,
            PREMIUM_ASSIGNMENT_OPTIONAL_FIELDS,
            context="PremiumAssignmentResult (success)",
        )

        # Semantic validation
        assert result["assigned"] is True
        assert result["instance_id"] is not None


@pytest.mark.asyncio
async def test_contract_premium_assign_scaling_in_progress(mock_premium_user):
    """
    Contract test: PremiumAssignmentResult when scaling is in progress.
    """
    with patch(
        "studio.app.common.routers.users_me.premium_assignment_service"
    ) as mock_service:
        mock_service.assign_premium_user = AsyncMock(
            return_value={
                "success": False,
                "requires_retry": True,
                "message": "Scaling in progress, please retry",
                "retry_after": 180,
            }
        )

        from studio.app.common.routers.users_me import assign_premium_instance

        result = await assign_premium_instance(current_user=mock_premium_user)

        validate_contract(
            result,
            PREMIUM_ASSIGNMENT_REQUIRED_FIELDS,
            PREMIUM_ASSIGNMENT_OPTIONAL_FIELDS,
            context="PremiumAssignmentResult (scaling)",
        )

        # Semantic validation
        assert result["assigned"] is False
        assert result["scaling_in_progress"] is True
        assert "retry_after" in result


@pytest.mark.asyncio
async def test_contract_premium_assign_shared_instance(mock_premium_user):
    """
    Contract test: PremiumAssignmentResult with shared instance.
    """
    with patch(
        "studio.app.common.routers.users_me.premium_assignment_service"
    ) as mock_service:
        mock_service.assign_premium_user = AsyncMock(
            return_value={
                "success": True,
                "message": "Assigned to shared instance",
                "instance_id": "i-shared-789",
                "is_shared": True,
                "assignment_source": "shared_fallback",
            }
        )

        from studio.app.common.routers.users_me import assign_premium_instance

        result = await assign_premium_instance(current_user=mock_premium_user)

        validate_contract(
            result,
            PREMIUM_ASSIGNMENT_REQUIRED_FIELDS,
            PREMIUM_ASSIGNMENT_OPTIONAL_FIELDS,
            context="PremiumAssignmentResult (shared)",
        )

        # Semantic validation for shared instance
        assert result["assigned"] is True
        assert result["is_shared"] is True


# ============================================================================
# Contract Tests: DELETE /users/me/premium/assign
# ============================================================================


@pytest.mark.asyncio
async def test_contract_premium_release_success(mock_premium_user):
    """
    Contract test: PremiumReleaseResult on successful release.
    """
    with patch(
        "studio.app.common.routers.users_me.premium_assignment_service"
    ) as mock_service:
        mock_service.release_premium_user = AsyncMock(
            return_value={
                "success": True,
                "message": "Released from instance i-123456",
                "released_instance": "i-123456",
            }
        )

        from studio.app.common.routers.users_me import release_premium_instance

        result = await release_premium_instance(current_user=mock_premium_user)

        validate_contract(
            result,
            PREMIUM_RELEASE_REQUIRED_FIELDS,
            PREMIUM_RELEASE_OPTIONAL_FIELDS,
            context="PremiumReleaseResult",
        )

        # Semantic validation
        assert result["released"] is True


@pytest.mark.asyncio
async def test_contract_premium_release_not_assigned(mock_premium_user):
    """
    Contract test: PremiumReleaseResult when user wasn't assigned.
    """
    with patch(
        "studio.app.common.routers.users_me.premium_assignment_service"
    ) as mock_service:
        mock_service.release_premium_user = AsyncMock(
            return_value={
                "success": False,
                "message": "User was not assigned to any instance",
            }
        )

        from studio.app.common.routers.users_me import release_premium_instance

        result = await release_premium_instance(current_user=mock_premium_user)

        validate_contract(
            result,
            PREMIUM_RELEASE_REQUIRED_FIELDS,
            PREMIUM_RELEASE_OPTIONAL_FIELDS,
            context="PremiumReleaseResult (not assigned)",
        )

        # Should still return released=True for graceful handling
        assert result["released"] is True


# ============================================================================
# Contract Tests: GET /users/me/premium/status
# ============================================================================


@pytest.mark.asyncio
async def test_contract_premium_status_with_assignment(mock_premium_user):
    """
    Contract test: PremiumStatusResult with active assignment.
    """
    with patch(
        "studio.app.common.routers.users_me.premium_assignment_service"
    ) as mock_service:
        mock_service.get_premium_user_status = AsyncMock(
            return_value={
                "instance_id": "i-123456",
                "assigned_at": "2024-01-15T10:30:00Z",
                "status": "active",
                "is_shared": False,
            }
        )

        from studio.app.common.routers.users_me import get_premium_assignment_status

        result = await get_premium_assignment_status(current_user=mock_premium_user)

        validate_contract(
            result,
            PREMIUM_STATUS_REQUIRED_FIELDS,
            PREMIUM_STATUS_OPTIONAL_FIELDS,
            context="PremiumStatusResult",
        )

        # Validate nested assignment object
        assert result["assignment"] is not None
        validate_contract(
            result["assignment"],
            PREMIUM_ASSIGNMENT_NESTED_FIELDS,
            context="PremiumAssignment (nested)",
        )

        # Semantic validation
        assert result["is_premium"] is True


@pytest.mark.asyncio
async def test_contract_premium_status_no_assignment(mock_premium_user):
    """
    Contract test: PremiumStatusResult without assignment.
    """
    with patch(
        "studio.app.common.routers.users_me.premium_assignment_service"
    ) as mock_service:
        mock_service.get_premium_user_status = AsyncMock(return_value=None)

        from studio.app.common.routers.users_me import get_premium_assignment_status

        result = await get_premium_assignment_status(current_user=mock_premium_user)

        validate_contract(
            result,
            PREMIUM_STATUS_REQUIRED_FIELDS,
            PREMIUM_STATUS_OPTIONAL_FIELDS,
            context="PremiumStatusResult (no assignment)",
        )

        # Assignment can be None
        assert result["assignment"] is None
        assert result["is_premium"] is True


@pytest.mark.asyncio
async def test_contract_premium_status_free_user(mock_free_user):
    """
    Contract test: PremiumStatusResult for free user.
    """
    with patch(
        "studio.app.common.routers.users_me.premium_assignment_service"
    ) as mock_service:
        mock_service.get_premium_user_status = AsyncMock(return_value=None)

        from studio.app.common.routers.users_me import get_premium_assignment_status

        result = await get_premium_assignment_status(current_user=mock_free_user)

        validate_contract(
            result,
            PREMIUM_STATUS_REQUIRED_FIELDS,
            PREMIUM_STATUS_OPTIONAL_FIELDS,
            context="PremiumStatusResult (free user)",
        )

        # Semantic validation for free user
        assert result["is_premium"] is False
        assert result["assignment"] is None


# ============================================================================
# Contract Tests: POST /users/me/premium/heartbeat
# ============================================================================


@pytest.mark.asyncio
async def test_contract_premium_heartbeat_success(mock_premium_user):
    """
    Contract test: PremiumHeartbeatResult on successful heartbeat.
    """
    with patch(
        "studio.app.common.routers.users_me.premium_assignment_service"
    ) as mock_service:
        mock_service.update_user_activity = AsyncMock(
            return_value={
                "success": True,
                "message": "Activity updated",
            }
        )

        from studio.app.common.routers.users_me import send_premium_heartbeat

        result = await send_premium_heartbeat(current_user=mock_premium_user)

        validate_contract(
            result,
            PREMIUM_HEARTBEAT_REQUIRED_FIELDS,
            PREMIUM_HEARTBEAT_OPTIONAL_FIELDS,
            context="PremiumHeartbeatResult",
        )

        # Semantic validation
        assert result["updated"] is True
        assert result["assignment_active"] is True
        assert result["user_tier"] in VALID_USER_TIERS


@pytest.mark.asyncio
async def test_contract_premium_heartbeat_no_assignment(mock_premium_user):
    """
    Contract test: PremiumHeartbeatResult when no active assignment.
    """
    with patch(
        "studio.app.common.routers.users_me.premium_assignment_service"
    ) as mock_service:
        with patch(
            "studio.app.common.routers.users_me.increment_heartbeat_failures",
            return_value=1,
        ):
            mock_service.update_user_activity = AsyncMock(
                return_value={
                    "success": False,
                    "message": "No active assignment",
                }
            )

            from studio.app.common.routers.users_me import send_premium_heartbeat

            result = await send_premium_heartbeat(current_user=mock_premium_user)

        validate_contract(
            result,
            PREMIUM_HEARTBEAT_REQUIRED_FIELDS,
            PREMIUM_HEARTBEAT_OPTIONAL_FIELDS,
            context="PremiumHeartbeatResult (no assignment)",
        )

        # Semantic validation
        assert result["updated"] is False
        assert result["assignment_active"] is False


@pytest.mark.asyncio
async def test_contract_premium_heartbeat_free_user(mock_free_user):
    """
    Contract test: PremiumHeartbeatResult for free user (graceful degradation).
    """
    from studio.app.common.routers.users_me import send_premium_heartbeat

    result = await send_premium_heartbeat(current_user=mock_free_user)

    validate_contract(
        result,
        PREMIUM_HEARTBEAT_REQUIRED_FIELDS,
        PREMIUM_HEARTBEAT_OPTIONAL_FIELDS,
        context="PremiumHeartbeatResult (free user)",
    )

    # Semantic validation for free user
    assert result["updated"] is False
    assert result["assignment_active"] is False
    assert result["user_tier"] == SubscriptionType.FREE.value


# ============================================================================
# Legacy Field Detection Tests
# ============================================================================


@pytest.mark.asyncio
async def test_no_legacy_fields_in_routing_info(mock_premium_user):
    """
    Ensure no legacy or deprecated field names are returned.
    """
    from studio.app.common.routers.users_me import get_routing_info

    result = await get_routing_info(current_user=mock_premium_user)

    # Check for any unexpected fields that might indicate API drift
    expected_fields = set(ROUTING_INFO_REQUIRED_FIELDS.keys())
    actual_fields = set(result.keys())

    unexpected = actual_fields - expected_fields
    assert not unexpected, (
        f"Unexpected fields in RoutingInfo response: {unexpected}. "
        f"If these are new fields, add them to ROUTING_INFO_REQUIRED_FIELDS "
        f"or ROUTING_INFO_OPTIONAL_FIELDS and update frontend interface."
    )


# ============================================================================
# Contract Tests: POST /users/me/premium/release-beacon
# ============================================================================

# BeaconResult interface (used by sendBeacon on browser close)
BEACON_RESULT_REQUIRED_FIELDS = {
    "success": bool,
}

BEACON_RESULT_OPTIONAL_FIELDS = {
    "message": str,
}


@pytest.mark.asyncio
async def test_contract_beacon_release_success():
    """
    Contract test: Beacon release response matches expected interface.
    """
    from unittest.mock import AsyncMock, MagicMock

    mock_user = MagicMock()
    mock_user.id = 1
    mock_user.uid = "test-user-123"

    mock_request = MagicMock()
    mock_request.json = AsyncMock(return_value={"token": "signed-token"})
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = mock_user

    with patch(
        "studio.app.common.routers.users_me.premium_assignment_service"
    ) as mock_service:
        with patch("studio.app.common.routers.users_me.invalidate_activity_cache"):
            with patch("studio.app.common.routers.users_me.mark_user_logged_out"):
                with patch(
                    "studio.app.common.core.auth.security" ".validate_beacon_token",
                    return_value="test-user-123",
                ):
                    mock_service.release_premium_user = AsyncMock(
                        return_value={
                            "success": True,
                            "message": "Released",
                        }
                    )

                    from studio.app.common.routers.users_me import (
                        release_premium_beacon,
                    )

                    result = await release_premium_beacon(
                        request=mock_request, db=mock_db
                    )

                    validate_contract(
                        result,
                        BEACON_RESULT_REQUIRED_FIELDS,
                        BEACON_RESULT_OPTIONAL_FIELDS,
                        context="BeaconResult (success)",
                    )

                    assert result["success"] is True


@pytest.mark.asyncio
async def test_contract_beacon_release_missing_uid():
    """
    Contract test: Beacon release response when user_uid missing.
    """
    from unittest.mock import AsyncMock, MagicMock

    mock_request = MagicMock()
    mock_request.json = AsyncMock(return_value={})
    mock_db = MagicMock()

    from studio.app.common.routers.users_me import release_premium_beacon

    result = await release_premium_beacon(request=mock_request, db=mock_db)

    validate_contract(
        result,
        BEACON_RESULT_REQUIRED_FIELDS,
        BEACON_RESULT_OPTIONAL_FIELDS,
        context="BeaconResult (missing uid)",
    )

    assert result["success"] is False
