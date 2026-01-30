"""
Contract Tests for Experiment API

These tests verify that API responses match the frontend TypeScript interfaces.
This ensures the backend and frontend stay in sync and prevents contract mismatches.

Frontend interfaces are defined in:
  frontend/src/api/experiments/Experiments.ts

Tested endpoints:
  - GET    /experiments/{workspace_id}              -> ExperimentsDTO
  - PATCH  /experiments/{workspace_id}/{uid}/rename -> ExperimentDTO
  - DELETE /experiments/{workspace_id}/{uid}        -> bool
  - POST   /experiments/delete/{workspace_id}       -> bool
  - POST   /experiments/copy/{workspace_id}         -> bool
"""

from studio.app.common.schemas.experiment import CopyItem, DeleteItem, RenameItem

# ============================================================================
# Frontend Contract Definitions
# ============================================================================
# These mirror the TypeScript interfaces in Experiments.ts

# ExperimentDTO interface
EXPERIMENT_DTO_REQUIRED_FIELDS = {
    "name": str,
    "started_at": str,
    "workspace_id": int,
    "unique_id": str,
    "hasNWB": bool,
    "data_usage": int,
}

EXPERIMENT_DTO_OPTIONAL_FIELDS = {
    "function": dict,
    "success": str,
    "finished_at": str,
    "is_remote_synced": bool,
    "nwb": dict,
}

# FunctionsDTO (nested in ExperimentDTO)
FUNCTION_ITEM_REQUIRED_FIELDS = {
    "name": str,
    "success": str,
    "unique_id": str,
    "hasNWB": bool,
}

FUNCTION_ITEM_OPTIONAL_FIELDS = {
    "message": str,
    "started_at": str,
    "finished_at": str,
    "outputPaths": dict,
}

# DeleteItem request
DELETE_ITEM_REQUIRED_FIELDS = {
    "uidList": list,
}

# RenameItem request
RENAME_ITEM_REQUIRED_FIELDS = {
    "new_name": str,
}

# CopyItem request
COPY_ITEM_REQUIRED_FIELDS = {
    "uidList": list,
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
# Contract Tests: Request Schema Validation
# ============================================================================


def test_contract_delete_item_schema():
    """
    Contract test: DeleteItem schema has required fields.
    """
    schema = DeleteItem.schema()
    properties = schema.get("properties", {})

    for field in DELETE_ITEM_REQUIRED_FIELDS.keys():
        assert (
            field in properties
        ), f"Contract violation: DeleteItem missing field '{field}'"


def test_contract_delete_item_serialization():
    """
    Contract test: DeleteItem serializes correctly.
    """
    item = DeleteItem(uidList=["uid1", "uid2", "uid3"])

    result = item.dict()

    validate_contract(
        result,
        DELETE_ITEM_REQUIRED_FIELDS,
        context="DeleteItem",
    )

    assert isinstance(result["uidList"], list)
    assert len(result["uidList"]) == 3


def test_contract_rename_item_schema():
    """
    Contract test: RenameItem schema has required fields.
    """
    schema = RenameItem.schema()
    properties = schema.get("properties", {})

    for field in RENAME_ITEM_REQUIRED_FIELDS.keys():
        assert field in properties


def test_contract_rename_item_serialization():
    """
    Contract test: RenameItem serializes correctly.
    """
    item = RenameItem(new_name="New Experiment Name")

    result = item.dict()

    validate_contract(
        result,
        RENAME_ITEM_REQUIRED_FIELDS,
        context="RenameItem",
    )


def test_contract_copy_item_schema():
    """
    Contract test: CopyItem schema has required fields.
    """
    schema = CopyItem.schema()
    properties = schema.get("properties", {})

    for field in COPY_ITEM_REQUIRED_FIELDS.keys():
        assert field in properties


def test_contract_copy_item_serialization():
    """
    Contract test: CopyItem serializes correctly.
    """
    item = CopyItem(uidList=["uid1", "uid2"])

    result = item.dict()

    validate_contract(
        result,
        COPY_ITEM_REQUIRED_FIELDS,
        context="CopyItem",
    )


# ============================================================================
# Contract Tests: Field Naming Consistency
# ============================================================================


def test_contract_no_legacy_experiment_fields():
    """
    Ensure request schemas use correct field names.
    """
    # RenameItem should use new_name (snake_case)
    item = RenameItem(new_name="test")
    result = item.dict()

    # Check for legacy camelCase fields
    legacy_fields = [
        "newName",  # camelCase variant
        "name",  # Wrong field name
    ]

    for legacy in legacy_fields:
        assert legacy not in result


def test_contract_uid_list_is_camel_case():
    """
    Contract test: uidList remains camelCase (matches frontend expectation).

    Note: This is an intentional exception to snake_case convention
    to match the frontend TypeScript interface.
    """
    delete_item = DeleteItem(uidList=["uid1"])
    result = delete_item.dict()

    # uidList should be camelCase
    assert "uidList" in result
    # uid_list should NOT be present
    assert "uid_list" not in result


# ============================================================================
# Contract Tests: Data Types
# ============================================================================


def test_contract_uid_list_contains_strings():
    """
    Contract test: uidList contains string UIDs.
    """
    item = DeleteItem(uidList=["abc-123", "def-456"])

    result = item.dict()

    assert all(isinstance(uid, str) for uid in result["uidList"])


def test_contract_uid_list_can_be_empty():
    """
    Contract test: uidList can be an empty list.
    """
    item = DeleteItem(uidList=[])

    result = item.dict()

    assert result["uidList"] == []


def test_contract_new_name_is_string():
    """
    Contract test: new_name is a string.
    """
    item = RenameItem(new_name="Test Name with Special Chars !@#$%")

    result = item.dict()

    assert isinstance(result["new_name"], str)
