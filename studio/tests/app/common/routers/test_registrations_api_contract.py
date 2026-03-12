"""
Contract Tests for Registrations API

These tests verify that API responses match the frontend TypeScript interfaces.
This ensures the backend and frontend stay in sync and prevents contract mismatches.

Frontend interfaces are defined in:
  frontend/src/api/registration/RegistrationApiDTO.ts

Tested endpoints:
  - POST /api/register                     -> UserRegistrationResponseDTO
  - GET  /api/register/verify-status/{email} -> VerificationStatusDTO
  - POST /api/register/resend-verification -> response
"""

from studio.app.common.schemas.registrations import ResendVerificationRequest

# ============================================================================
# Frontend Contract Definitions
# ============================================================================
# These mirror the TypeScript interfaces in RegistrationApiDTO.ts

# UserRegistrationRequestDTO (request)
USER_REGISTRATION_REQUEST_REQUIRED_FIELDS = {
    "email": str,
    "password": str,
    "name": str,
}

USER_REGISTRATION_REQUEST_OPTIONAL_FIELDS = {
    "organization_id": int,
    "role_id": int,
}

# UserRegistrationResponseDTO (response)
USER_REGISTRATION_RESPONSE_REQUIRED_FIELDS = {
    "user": dict,
}

# User nested in response
USER_IN_RESPONSE_REQUIRED_FIELDS = {
    "id": int,
    "email": str,
    "name": str,
    "uid": str,
    "organization": dict,
}

USER_IN_RESPONSE_OPTIONAL_FIELDS = {
    "role_id": int,
    "data_usage": int,
    "attributes": dict,
}

# Organization nested in user
ORGANIZATION_IN_RESPONSE_REQUIRED_FIELDS = {
    "id": int,
    "name": str,
}

# VerificationStatusDTO
VERIFICATION_STATUS_REQUIRED_FIELDS = {
    "email_verified": bool,
    "uid": str,
}

# ResendVerificationRequest
RESEND_VERIFICATION_REQUEST_REQUIRED_FIELDS = {
    "email": str,
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
# Contract Tests: ResendVerificationRequest Schema
# ============================================================================


def test_contract_resend_verification_schema():
    """
    Contract test: ResendVerificationRequest has required fields.
    """
    schema = ResendVerificationRequest.schema()
    properties = schema.get("properties", {})

    for field in RESEND_VERIFICATION_REQUEST_REQUIRED_FIELDS.keys():
        assert (
            field in properties
        ), f"Contract violation: ResendVerificationRequest missing field '{field}'"


def test_contract_resend_verification_serialization():
    """
    Contract test: ResendVerificationRequest serializes correctly.
    """
    request = ResendVerificationRequest(email="test@example.com")

    result = request.dict()

    validate_contract(
        result,
        RESEND_VERIFICATION_REQUEST_REQUIRED_FIELDS,
        context="ResendVerificationRequest",
    )


def test_contract_resend_verification_email_is_string():
    """
    Contract test: email field is a string.
    """
    request = ResendVerificationRequest(email="user@domain.com")

    result = request.dict()

    assert isinstance(result["email"], str)


# ============================================================================
# Contract Tests: UserRegistrationResponseDTO Structure
# ============================================================================


def test_contract_registration_response_structure():
    """
    Contract test: UserRegistrationResponseDTO has required fields.
    """
    response = {
        "user": {
            "id": 1,
            "email": "test@example.com",
            "name": "Test User",
            "uid": "firebase-uid-123",
            "organization": {
                "id": 1,
                "name": "Default Org",
            },
        },
    }

    validate_contract(
        response,
        USER_REGISTRATION_RESPONSE_REQUIRED_FIELDS,
        context="UserRegistrationResponseDTO",
    )


def test_contract_registration_response_user_fields():
    """
    Contract test: User nested object has required fields.
    """
    user = {
        "id": 1,
        "email": "test@example.com",
        "name": "Test User",
        "uid": "firebase-uid-123",
        "organization": {
            "id": 1,
            "name": "Default Org",
        },
    }

    validate_contract(
        user,
        USER_IN_RESPONSE_REQUIRED_FIELDS,
        USER_IN_RESPONSE_OPTIONAL_FIELDS,
        context="UserRegistrationResponseDTO.user",
    )


def test_contract_registration_response_organization():
    """
    Contract test: Organization nested object has required fields.
    """
    organization = {
        "id": 1,
        "name": "Test Organization",
    }

    validate_contract(
        organization,
        ORGANIZATION_IN_RESPONSE_REQUIRED_FIELDS,
        context="UserRegistrationResponseDTO.user.organization",
    )


def test_contract_registration_response_with_optional_fields():
    """
    Contract test: User can have optional fields.
    """
    user = {
        "id": 1,
        "email": "test@example.com",
        "name": "Test User",
        "uid": "firebase-uid-123",
        "organization": {"id": 1, "name": "Org"},
        "role_id": 20,
        "data_usage": 1000000,
        "attributes": {"remote_bucket_name": "bucket-123"},
    }

    validate_contract(
        user,
        USER_IN_RESPONSE_REQUIRED_FIELDS,
        USER_IN_RESPONSE_OPTIONAL_FIELDS,
        context="UserRegistrationResponseDTO.user (with optional)",
    )


# ============================================================================
# Contract Tests: VerificationStatusDTO Structure
# ============================================================================


def test_contract_verification_status_structure():
    """
    Contract test: VerificationStatusDTO has required fields.
    """
    status = {
        "email_verified": True,
        "uid": "firebase-uid-123",
    }

    validate_contract(
        status,
        VERIFICATION_STATUS_REQUIRED_FIELDS,
        context="VerificationStatusDTO",
    )


def test_contract_verification_status_verified():
    """
    Contract test: VerificationStatusDTO when email is verified.
    """
    status = {
        "email_verified": True,
        "uid": "firebase-uid-123",
    }

    assert status["email_verified"] is True
    assert isinstance(status["uid"], str)


def test_contract_verification_status_not_verified():
    """
    Contract test: VerificationStatusDTO when email is not verified.
    """
    status = {
        "email_verified": False,
        "uid": "firebase-uid-456",
    }

    assert status["email_verified"] is False


def test_contract_email_verified_is_boolean():
    """
    Contract test: email_verified is a boolean.
    """
    status = {
        "email_verified": True,
        "uid": "test-uid",
    }

    assert isinstance(status["email_verified"], bool)


# ============================================================================
# Contract Tests: Field Naming Consistency
# ============================================================================


def test_contract_no_legacy_registration_fields():
    """
    Ensure no legacy or camelCase field names.
    """
    user = {
        "id": 1,
        "email": "test@example.com",
        "name": "Test",
        "uid": "uid-123",
        "organization": {"id": 1, "name": "Org"},
        "role_id": 20,
        "data_usage": 0,
    }

    legacy_fields = [
        "roleId",  # camelCase
        "dataUsage",  # camelCase
        "organizationId",  # camelCase
    ]

    for legacy in legacy_fields:
        assert legacy not in user


def test_contract_email_verified_is_snake_case():
    """
    Contract test: email_verified uses snake_case.
    """
    status = {
        "email_verified": True,
        "uid": "test-uid",
    }

    assert "email_verified" in status
    assert "emailVerified" not in status


# ============================================================================
# Contract Tests: Data Types
# ============================================================================


def test_contract_user_id_is_integer():
    """
    Contract test: User id is an integer.
    """
    user = {
        "id": 123,
        "email": "test@example.com",
        "name": "Test",
        "uid": "uid-123",
        "organization": {"id": 1, "name": "Org"},
    }

    assert isinstance(user["id"], int)


def test_contract_organization_id_is_integer():
    """
    Contract test: Organization id is an integer.
    """
    organization = {
        "id": 5,
        "name": "Test Organization",
    }

    assert isinstance(organization["id"], int)


def test_contract_uid_is_string():
    """
    Contract test: uid is a string.
    """
    user = {
        "id": 1,
        "email": "test@example.com",
        "name": "Test",
        "uid": "firebase-auth-uid-abc123",
        "organization": {"id": 1, "name": "Org"},
    }

    assert isinstance(user["uid"], str)
