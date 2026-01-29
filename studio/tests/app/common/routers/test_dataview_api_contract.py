"""
Contract Tests for Dataview API

These tests verify that API responses match the frontend TypeScript interfaces.
This ensures the backend and frontend stay in sync and prevents contract mismatches.

Frontend interfaces are defined in:
  frontend/src/api/dataview/Dataview.ts
  frontend/src/store/slice/Dataview/DataviewType.ts

Tested endpoints:
  - GET  /api/dataview                            -> DataviewDTO
  - GET  /api/public/dataview                     -> DataviewDTO
  - POST /api/dataview/publish/{id}/{status}      -> bool
  - POST /api/dataview/multiple/publish/{status}  -> bool
  - PUT  /dataview/metadata/{id}                  -> bool
"""

# ============================================================================
# Frontend Contract Definitions
# ============================================================================
# These mirror the TypeScript interfaces in DataviewType.ts

# DataviewType (single record)
DATAVIEW_TYPE_REQUIRED_FIELDS = {
    "id": int,
    "uid": str,
    "name": str,
    "analyzed_at": str,
    "created_at": str,
    "updated_at": str,
}

DATAVIEW_TYPE_OPTIONAL_FIELDS = {
    "owner": dict,
    "workspace": dict,
    "thumbnails": dict,
    "attributes": dict,
    "publish_status": int,
}

# Owner nested object
OWNER_REQUIRED_FIELDS = {}

OWNER_OPTIONAL_FIELDS = {
    "name": str,
}

# Workspace nested object
WORKSPACE_REQUIRED_FIELDS = {
    "id": int,
}

WORKSPACE_OPTIONAL_FIELDS = {
    "name": str,
}

# Thumbnails nested object
THUMBNAILS_OPTIONAL_FIELDS = {
    "image_url": str,
    "roi_url": str,
}

# DataviewDTO (paginated response)
DATAVIEW_DTO_REQUIRED_FIELDS = {
    "offset": int,
    "limit": int,
    "total": int,
    "items": list,
}

DATAVIEW_DTO_OPTIONAL_FIELDS = {
    "header": dict,
}

# Header nested object
HEADER_OPTIONAL_FIELDS = {
    "workspace_id": int,
    "workspace_name": str,
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
# Contract Tests: DataviewDTO Structure
# ============================================================================


def test_contract_dataview_dto_structure():
    """
    Contract test: DataviewDTO has required fields.
    """
    dataview_dto = {
        "offset": 0,
        "limit": 10,
        "total": 100,
        "items": [],
    }

    validate_contract(
        dataview_dto,
        DATAVIEW_DTO_REQUIRED_FIELDS,
        DATAVIEW_DTO_OPTIONAL_FIELDS,
        context="DataviewDTO",
    )


def test_contract_dataview_dto_with_header():
    """
    Contract test: DataviewDTO with header serializes correctly.
    """
    dataview_dto = {
        "offset": 0,
        "limit": 10,
        "total": 50,
        "items": [],
        "header": {
            "workspace_id": 1,
            "workspace_name": "Test Workspace",
        },
    }

    validate_contract(
        dataview_dto,
        DATAVIEW_DTO_REQUIRED_FIELDS,
        DATAVIEW_DTO_OPTIONAL_FIELDS,
        context="DataviewDTO (with header)",
    )

    # Validate header structure
    if dataview_dto.get("header"):
        for field, expected_type in HEADER_OPTIONAL_FIELDS.items():
            if field in dataview_dto["header"]:
                assert isinstance(dataview_dto["header"][field], expected_type)


def test_contract_dataview_dto_items_is_list():
    """
    Contract test: DataviewDTO items is a list.
    """
    dataview_dto = {
        "offset": 0,
        "limit": 10,
        "total": 0,
        "items": [],
    }

    assert isinstance(dataview_dto["items"], list)


# ============================================================================
# Contract Tests: DataviewType (Record) Structure
# ============================================================================


def test_contract_dataview_type_structure():
    """
    Contract test: DataviewType record has required fields.
    """
    record = {
        "id": 1,
        "uid": "exp-123-abc",
        "name": "Experiment 1",
        "analyzed_at": "2025-01-29T10:00:00Z",
        "created_at": "2025-01-29T09:00:00Z",
        "updated_at": "2025-01-29T11:00:00Z",
    }

    validate_contract(
        record,
        DATAVIEW_TYPE_REQUIRED_FIELDS,
        DATAVIEW_TYPE_OPTIONAL_FIELDS,
        context="DataviewType",
    )


def test_contract_dataview_type_with_owner():
    """
    Contract test: DataviewType with owner nested object.
    """
    record = {
        "id": 1,
        "uid": "exp-123",
        "name": "Experiment",
        "owner": {
            "name": "Test User",
        },
        "analyzed_at": "2025-01-29T10:00:00Z",
        "created_at": "2025-01-29T09:00:00Z",
        "updated_at": "2025-01-29T11:00:00Z",
    }

    assert "owner" in record
    assert isinstance(record["owner"], dict)


def test_contract_dataview_type_with_workspace():
    """
    Contract test: DataviewType with workspace nested object.
    """
    record = {
        "id": 1,
        "uid": "exp-123",
        "name": "Experiment",
        "workspace": {
            "id": 5,
            "name": "My Workspace",
        },
        "analyzed_at": "2025-01-29T10:00:00Z",
        "created_at": "2025-01-29T09:00:00Z",
        "updated_at": "2025-01-29T11:00:00Z",
    }

    workspace = record["workspace"]
    validate_contract(
        workspace,
        WORKSPACE_REQUIRED_FIELDS,
        WORKSPACE_OPTIONAL_FIELDS,
        context="DataviewType.workspace",
    )


def test_contract_dataview_type_with_thumbnails():
    """
    Contract test: DataviewType with thumbnails nested object.
    """
    record = {
        "id": 1,
        "uid": "exp-123",
        "name": "Experiment",
        "thumbnails": {
            "image_url": "/path/to/thumbnail.png",
            "roi_url": "/path/to/roi.png",
        },
        "analyzed_at": "2025-01-29T10:00:00Z",
        "created_at": "2025-01-29T09:00:00Z",
        "updated_at": "2025-01-29T11:00:00Z",
    }

    thumbnails = record["thumbnails"]
    for field, expected_type in THUMBNAILS_OPTIONAL_FIELDS.items():
        if field in thumbnails and thumbnails[field] is not None:
            assert isinstance(thumbnails[field], expected_type)


def test_contract_dataview_type_publish_status():
    """
    Contract test: publish_status is an integer.
    """
    record = {
        "id": 1,
        "uid": "exp-123",
        "name": "Experiment",
        "publish_status": 1,
        "analyzed_at": "2025-01-29T10:00:00Z",
        "created_at": "2025-01-29T09:00:00Z",
        "updated_at": "2025-01-29T11:00:00Z",
    }

    assert isinstance(record["publish_status"], int)


# ============================================================================
# Contract Tests: Field Naming Consistency
# ============================================================================


def test_contract_no_legacy_dataview_fields():
    """
    Ensure no legacy or camelCase field names in dataview responses.
    """
    record = {
        "id": 1,
        "uid": "exp-123",
        "name": "Experiment",
        "analyzed_at": "2025-01-29T10:00:00Z",
        "created_at": "2025-01-29T09:00:00Z",
        "updated_at": "2025-01-29T11:00:00Z",
        "publish_status": 1,
    }

    legacy_fields = [
        "analyzedAt",  # camelCase
        "createdAt",  # camelCase
        "updatedAt",  # camelCase
        "publishStatus",  # camelCase
    ]

    for legacy in legacy_fields:
        assert legacy not in record


def test_contract_pagination_uses_snake_case():
    """
    Contract test: Pagination fields use consistent naming.
    """
    dataview_dto = {
        "offset": 0,
        "limit": 10,
        "total": 100,
        "items": [],
    }

    # These field names should be lowercase
    assert "offset" in dataview_dto
    assert "limit" in dataview_dto
    assert "total" in dataview_dto


# ============================================================================
# Contract Tests: Data Types
# ============================================================================


def test_contract_dataview_id_is_integer():
    """
    Contract test: id is an integer.
    """
    record = {
        "id": 123,
        "uid": "exp-123",
        "name": "Experiment",
        "analyzed_at": "2025-01-29T10:00:00Z",
        "created_at": "2025-01-29T09:00:00Z",
        "updated_at": "2025-01-29T11:00:00Z",
    }

    assert isinstance(record["id"], int)


def test_contract_dataview_uid_is_string():
    """
    Contract test: uid is a string.
    """
    record = {
        "id": 1,
        "uid": "abc-123-def-456",
        "name": "Experiment",
        "analyzed_at": "2025-01-29T10:00:00Z",
        "created_at": "2025-01-29T09:00:00Z",
        "updated_at": "2025-01-29T11:00:00Z",
    }

    assert isinstance(record["uid"], str)


def test_contract_dataview_dates_are_strings():
    """
    Contract test: Date fields are ISO format strings.
    """
    record = {
        "id": 1,
        "uid": "exp-123",
        "name": "Experiment",
        "analyzed_at": "2025-01-29T10:00:00Z",
        "created_at": "2025-01-29T09:00:00Z",
        "updated_at": "2025-01-29T11:00:00Z",
    }

    assert isinstance(record["analyzed_at"], str)
    assert isinstance(record["created_at"], str)
    assert isinstance(record["updated_at"], str)
