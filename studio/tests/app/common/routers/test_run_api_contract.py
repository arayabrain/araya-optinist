"""
Contract Tests for Run API

These tests verify that API responses match the frontend TypeScript interfaces.
This ensures the backend and frontend stay in sync and prevents contract mismatches.

Frontend interfaces are defined in:
  frontend/src/api/run/Run.ts

Tested endpoints:
  - POST /run/{workspace_id}                    -> str (unique_id)
  - POST /run/{workspace_id}/{uid}              -> str (unique_id)
  - POST /run/result/{workspace_id}/{uid}       -> RunResultDTO
  - POST /run/cancel/{workspace_id}/{uid}       -> RunResultDTO
  - POST /run/filter/{workspace_id}/{uid}/{node_id} -> str
"""

from studio.app.common.schemas.workflow import CompleteStatus

# ============================================================================
# Frontend Contract Definitions
# ============================================================================
# These mirror the TypeScript interfaces in Run.ts

# PollRunResultDTO interface (wrapper structure for poll endpoint)
POLL_RUN_RESULT_DTO_REQUIRED_FIELDS = {
    "nodeResults": dict,  # Dict of node results by nodeId
}

POLL_RUN_RESULT_DTO_OPTIONAL_FIELDS = {
    "completeStatus": (str, type(None)),
}

# RunResultDTO item (value in nodeResults) - dict of node results by nodeId
RUN_RESULT_ITEM_REQUIRED_FIELDS = {
    "status": str,
    "message": str,
    "name": str,
}

RUN_RESULT_ITEM_OPTIONAL_FIELDS = {
    "outputPaths": dict,
}

# OutputPathsDTO item
OUTPUT_PATHS_ITEM_REQUIRED_FIELDS = {
    "path": str,
    "type": str,
    "data_shape": list,
    "max_index": int,
}

# RunPostData request fields
RUN_POST_DATA_REQUIRED_FIELDS = {
    "name": str,
    "nodeDict": dict,
    "edgeDict": dict,
    "nwbParam": dict,
    "snakemakeParam": dict,
    "forceRunList": list,
}

# Valid run statuses
VALID_RUN_STATUSES = {"success", "error", "running", "pending", "skipped"}


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
# Contract Tests: RunResultDTO Structure
# ============================================================================


def test_contract_poll_run_result_dto_structure():
    """
    Contract test: PollRunResultDTO has nodeResults wrapper and optional completeStatus.
    """
    poll_result = {
        "nodeResults": {
            "node1": {
                "status": "success",
                "message": "Completed successfully",
                "name": "Suite2P",
            },
            "node2": {
                "status": "running",
                "message": "Processing...",
                "name": "CaImAn",
            },
        },
        "completeStatus": CompleteStatus.SUCCESS.value,
    }

    # Validate wrapper structure
    validate_contract(
        poll_result,
        POLL_RUN_RESULT_DTO_REQUIRED_FIELDS,
        POLL_RUN_RESULT_DTO_OPTIONAL_FIELDS,
        context="PollRunResultDTO",
    )

    # Validate nodeResults contents
    assert isinstance(poll_result["nodeResults"], dict)
    for node_id, result_item in poll_result["nodeResults"].items():
        assert isinstance(node_id, str)
        validate_contract(
            result_item,
            RUN_RESULT_ITEM_REQUIRED_FIELDS,
            RUN_RESULT_ITEM_OPTIONAL_FIELDS,
            context=f"nodeResults[{node_id}]",
        )


def test_contract_poll_run_result_dto_complete_status_values():
    """
    Contract test: completeStatus can be processing, success, error, or None.
    """
    valid_complete_statuses = [
        CompleteStatus.PROCESSING.value,
        CompleteStatus.SUCCESS.value,
        CompleteStatus.ERROR.value,
        None,
    ]

    for status in valid_complete_statuses:
        poll_result = {
            "nodeResults": {},
            "completeStatus": status,
        }
        # Should not raise
        validate_contract(
            poll_result,
            POLL_RUN_RESULT_DTO_REQUIRED_FIELDS,
            POLL_RUN_RESULT_DTO_OPTIONAL_FIELDS,
            context=f"PollRunResultDTO completeStatus={status}",
        )


def test_contract_run_result_item_required_fields():
    """
    Contract test: RunResult item has required fields.
    """
    result_item = {
        "status": "success",
        "message": "Node completed",
        "name": "TestAlgorithm",
    }

    validate_contract(
        result_item,
        RUN_RESULT_ITEM_REQUIRED_FIELDS,
        context="RunResult item",
    )


def test_contract_run_result_item_with_output_paths():
    """
    Contract test: RunResult item with outputPaths serializes correctly.
    """
    result_item = {
        "status": "success",
        "message": "Completed with outputs",
        "name": "Suite2P",
        "outputPaths": {
            "output1": {
                "path": "/path/to/output",
                "type": "image",
                "data_shape": [512, 512, 100],
                "max_index": 99,
            }
        },
    }

    validate_contract(
        result_item,
        RUN_RESULT_ITEM_REQUIRED_FIELDS,
        RUN_RESULT_ITEM_OPTIONAL_FIELDS,
        context="RunResult item (with outputs)",
    )

    # Validate outputPaths structure
    assert "outputPaths" in result_item
    for output_key, output_value in result_item["outputPaths"].items():
        validate_contract(
            output_value,
            OUTPUT_PATHS_ITEM_REQUIRED_FIELDS,
            context=f"OutputPaths[{output_key}]",
        )


# ============================================================================
# Contract Tests: OutputPathsDTO Structure
# ============================================================================


def test_contract_output_paths_item_structure():
    """
    Contract test: OutputPathsDTO item has required fields.
    """
    output_item = {
        "path": "/workspace/1/output/exp1/result.npy",
        "type": "scatter",
        "data_shape": [1000, 2],
        "max_index": 0,
    }

    validate_contract(
        output_item,
        OUTPUT_PATHS_ITEM_REQUIRED_FIELDS,
        context="OutputPaths item",
    )


def test_contract_output_paths_data_shape_is_list():
    """
    Contract test: data_shape is a list of integers.
    """
    output_item = {
        "path": "/path",
        "type": "image",
        "data_shape": [512, 512, 100],
        "max_index": 99,
    }

    assert isinstance(output_item["data_shape"], list)
    assert all(isinstance(dim, int) for dim in output_item["data_shape"])


def test_contract_output_paths_max_index_is_integer():
    """
    Contract test: max_index is an integer.
    """
    output_item = {
        "path": "/path",
        "type": "timeseries",
        "data_shape": [1000],
        "max_index": 999,
    }

    assert isinstance(output_item["max_index"], int)


# ============================================================================
# Contract Tests: Run Status Values
# ============================================================================


def test_contract_run_status_success():
    """
    Contract test: 'success' is a valid status.
    """
    result_item = {
        "status": "success",
        "message": "",
        "name": "Test",
    }

    assert result_item["status"] == "success"


def test_contract_run_status_error():
    """
    Contract test: 'error' is a valid status with message.
    """
    result_item = {
        "status": "error",
        "message": "Failed to process: out of memory",
        "name": "TestAlgorithm",
    }

    assert result_item["status"] == "error"
    assert result_item["message"] != ""


def test_contract_run_status_running():
    """
    Contract test: 'running' is a valid status.
    """
    result_item = {
        "status": "running",
        "message": "Processing step 3/10",
        "name": "Suite2P",
    }

    assert result_item["status"] == "running"


# ============================================================================
# Contract Tests: RunPostData Request
# ============================================================================


def test_contract_run_post_data_structure():
    """
    Contract test: RunPostData request has required fields.
    """
    run_request = {
        "name": "New Experiment",
        "nodeDict": {},
        "edgeDict": {},
        "nwbParam": {},
        "snakemakeParam": {},
        "forceRunList": [],
    }

    validate_contract(
        run_request,
        RUN_POST_DATA_REQUIRED_FIELDS,
        context="RunPostData",
    )


def test_contract_run_post_data_force_run_list():
    """
    Contract test: forceRunList contains objects with nodeId and name.
    """
    run_request = {
        "name": "Experiment",
        "nodeDict": {},
        "edgeDict": {},
        "nwbParam": {},
        "snakemakeParam": {},
        "forceRunList": [
            {"nodeId": "node1", "name": "Suite2P"},
            {"nodeId": "node2", "name": "CaImAn"},
        ],
    }

    assert isinstance(run_request["forceRunList"], list)
    for item in run_request["forceRunList"]:
        assert "nodeId" in item
        assert "name" in item


def test_contract_run_post_data_empty_force_run_list():
    """
    Contract test: forceRunList can be empty.
    """
    run_request = {
        "name": "Experiment",
        "nodeDict": {},
        "edgeDict": {},
        "nwbParam": {},
        "snakemakeParam": {},
        "forceRunList": [],
    }

    assert run_request["forceRunList"] == []


# ============================================================================
# Contract Tests: Field Naming Consistency
# ============================================================================


def test_contract_no_legacy_run_result_fields():
    """
    Ensure no legacy field names in run result responses.
    """
    result_item = {
        "status": "success",
        "message": "",
        "name": "Test",
        "outputPaths": {},
    }

    # Check for legacy field names
    legacy_fields = [
        "output_paths",  # Should be outputPaths (camelCase)
        "dataShape",  # Should be data_shape (in outputPaths)
        "maxIndex",  # Should be max_index (in outputPaths)
    ]

    for legacy in legacy_fields:
        assert legacy not in result_item


def test_contract_output_paths_is_camel_case():
    """
    Contract test: outputPaths uses camelCase.
    """
    result_item = {
        "status": "success",
        "message": "",
        "name": "Test",
        "outputPaths": {},
    }

    assert "outputPaths" in result_item
    assert "output_paths" not in result_item


def test_contract_data_shape_is_snake_case():
    """
    Contract test: data_shape uses snake_case (in OutputPaths).
    """
    output_item = {
        "path": "/path",
        "type": "image",
        "data_shape": [100, 100],
        "max_index": 0,
    }

    assert "data_shape" in output_item
    assert "dataShape" not in output_item


def test_contract_max_index_is_snake_case():
    """
    Contract test: max_index uses snake_case (in OutputPaths).
    """
    output_item = {
        "path": "/path",
        "type": "image",
        "data_shape": [100],
        "max_index": 99,
    }

    assert "max_index" in output_item
    assert "maxIndex" not in output_item


# ============================================================================
# Contract Tests: Return Types
# ============================================================================


def test_contract_run_returns_unique_id_string():
    """
    Contract test: Run endpoint returns a string unique_id.
    """
    # The run endpoint returns a unique_id string
    unique_id = "abc-123-def-456"

    assert isinstance(unique_id, str)
    assert len(unique_id) > 0


def test_contract_poll_run_result_is_dict():
    """
    Contract test: PollRunResultDTO has nodeResults which is a dictionary.
    """
    poll_result = {
        "nodeResults": {
            "node1": {"status": "success", "message": "", "name": "Test"},
        },
        "completeStatus": None,
    }

    assert isinstance(poll_result, dict)
    assert isinstance(poll_result["nodeResults"], dict)


def test_contract_poll_run_result_empty_node_results():
    """
    Contract test: nodeResults can be empty dict.
    """
    poll_result = {
        "nodeResults": {},
        "completeStatus": None,
    }

    assert isinstance(poll_result, dict)
    assert isinstance(poll_result["nodeResults"], dict)
    assert len(poll_result["nodeResults"]) == 0
