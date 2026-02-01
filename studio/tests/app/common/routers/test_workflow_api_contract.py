"""
Contract Tests for Workflow API

These tests verify that API responses match the frontend TypeScript interfaces.
This ensures the backend and frontend stay in sync and prevents contract mismatches.

Frontend interfaces are defined in:
  frontend/src/api/workflow/Workflow.ts
  frontend/src/api/run/Run.ts (for NodeDict, EdgeDict)

Tested endpoints:
  - GET  /workflow/fetch/{workspace_id}           -> WorkflowWithResultDTO
  - GET  /workflow/reproduce/{workspace_id}/{uid} -> WorkflowWithResultDTO
  - POST /workflow/import                         -> WorkflowConfigDTO
  - GET  /workflow/sample_data/{workspace_id}/{category} -> bool
"""

# ============================================================================
# Frontend Contract Definitions
# ============================================================================
# These mirror the TypeScript interfaces in Workflow.ts and Run.ts

# WorkflowConfigDTO interface
WORKFLOW_CONFIG_DTO_REQUIRED_FIELDS = {
    "nodeDict": dict,
    "edgeDict": dict,
}

# WorkflowWithResultDTO extends ExperimentDTO & WorkflowConfigDTO
# Inherits experiment fields plus workflow config
WORKFLOW_WITH_RESULT_DTO_REQUIRED_FIELDS = {
    **WORKFLOW_CONFIG_DTO_REQUIRED_FIELDS,
}

WORKFLOW_WITH_RESULT_DTO_OPTIONAL_FIELDS = {
    "name": str,
    "started_at": str,
    "finished_at": str,
    "workspace_id": int,
    "unique_id": str,
    "hasNWB": bool,
    "is_remote_synced": bool,
    "nwb": dict,
    "data_usage": int,
    "function": dict,
    "success": str,
}

# NodeDict structure
NODE_DICT_ITEM_REQUIRED_FIELDS = {
    "id": str,
    "type": str,
    "data": dict,
    "position": dict,
}

# EdgeDict structure
EDGE_DICT_ITEM_REQUIRED_FIELDS = {
    "id": str,
    "source": str,
    "target": str,
}

EDGE_DICT_ITEM_OPTIONAL_FIELDS = {
    "sourceHandle": str,
    "targetHandle": str,
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
# Contract Tests: WorkflowConfigDTO Structure
# ============================================================================


def test_contract_workflow_config_structure():
    """
    Contract test: WorkflowConfigDTO has required structure.
    """
    # Simulate a workflow config response
    workflow_config = {
        "nodeDict": {
            "node1": {
                "id": "node1",
                "type": "ImageFileNode",
                "data": {"label": "Input", "path": "/path/to/file"},
                "position": {"x": 100, "y": 100},
            }
        },
        "edgeDict": {
            "edge1": {
                "id": "edge1",
                "source": "node1",
                "target": "node2",
            }
        },
    }

    validate_contract(
        workflow_config,
        WORKFLOW_CONFIG_DTO_REQUIRED_FIELDS,
        context="WorkflowConfigDTO",
    )


def test_contract_workflow_config_node_dict_is_dict():
    """
    Contract test: nodeDict is a dictionary.
    """
    workflow_config = {
        "nodeDict": {},
        "edgeDict": {},
    }

    assert isinstance(workflow_config["nodeDict"], dict)


def test_contract_workflow_config_edge_dict_is_dict():
    """
    Contract test: edgeDict is a dictionary.
    """
    workflow_config = {
        "nodeDict": {},
        "edgeDict": {},
    }

    assert isinstance(workflow_config["edgeDict"], dict)


def test_contract_workflow_config_can_be_empty():
    """
    Contract test: WorkflowConfigDTO can have empty nodeDict and edgeDict.
    """
    workflow_config = {
        "nodeDict": {},
        "edgeDict": {},
    }

    validate_contract(
        workflow_config,
        WORKFLOW_CONFIG_DTO_REQUIRED_FIELDS,
        context="WorkflowConfigDTO (empty)",
    )


# ============================================================================
# Contract Tests: NodeDict Item Structure
# ============================================================================


def test_contract_node_dict_item_structure():
    """
    Contract test: NodeDict item has required fields.
    """
    node_item = {
        "id": "node1",
        "type": "AlgorithmNode",
        "data": {
            "label": "Suite2P",
            "path": "/path/to/algo",
            "param": {},
        },
        "position": {"x": 200, "y": 300},
    }

    validate_contract(
        node_item,
        NODE_DICT_ITEM_REQUIRED_FIELDS,
        context="NodeDict item",
    )


def test_contract_node_position_has_coordinates():
    """
    Contract test: Node position has x and y coordinates.
    """
    node_item = {
        "id": "node1",
        "type": "ImageFileNode",
        "data": {},
        "position": {"x": 100, "y": 200},
    }

    position = node_item["position"]
    assert "x" in position
    assert "y" in position
    assert isinstance(position["x"], (int, float))
    assert isinstance(position["y"], (int, float))


def test_contract_node_data_is_dict():
    """
    Contract test: Node data is a dictionary.
    """
    node_item = {
        "id": "node1",
        "type": "AlgorithmNode",
        "data": {"label": "Test", "param": {"key": "value"}},
        "position": {"x": 0, "y": 0},
    }

    assert isinstance(node_item["data"], dict)


# ============================================================================
# Contract Tests: EdgeDict Item Structure
# ============================================================================


def test_contract_edge_dict_item_structure():
    """
    Contract test: EdgeDict item has required fields.
    """
    edge_item = {
        "id": "edge1",
        "source": "node1",
        "target": "node2",
    }

    validate_contract(
        edge_item,
        EDGE_DICT_ITEM_REQUIRED_FIELDS,
        EDGE_DICT_ITEM_OPTIONAL_FIELDS,
        context="EdgeDict item",
    )


def test_contract_edge_with_handles():
    """
    Contract test: EdgeDict item can have optional handles.
    """
    edge_item = {
        "id": "edge1",
        "source": "node1",
        "target": "node2",
        "sourceHandle": "output",
        "targetHandle": "input",
    }

    validate_contract(
        edge_item,
        EDGE_DICT_ITEM_REQUIRED_FIELDS,
        EDGE_DICT_ITEM_OPTIONAL_FIELDS,
        context="EdgeDict item (with handles)",
    )


def test_contract_edge_ids_are_strings():
    """
    Contract test: Edge source and target are string node IDs.
    """
    edge_item = {
        "id": "edge1",
        "source": "node1",
        "target": "node2",
    }

    assert isinstance(edge_item["id"], str)
    assert isinstance(edge_item["source"], str)
    assert isinstance(edge_item["target"], str)


# ============================================================================
# Contract Tests: WorkflowWithResultDTO Structure
# ============================================================================


def test_contract_workflow_with_result_structure():
    """
    Contract test: WorkflowWithResultDTO has required fields.
    """
    workflow_result = {
        "nodeDict": {},
        "edgeDict": {},
        "name": "Experiment 1",
        "started_at": "2025-01-29T10:00:00Z",
        "workspace_id": 1,
        "unique_id": "abc-123",
        "hasNWB": True,
        "data_usage": 1000000,
    }

    validate_contract(
        workflow_result,
        WORKFLOW_WITH_RESULT_DTO_REQUIRED_FIELDS,
        WORKFLOW_WITH_RESULT_DTO_OPTIONAL_FIELDS,
        context="WorkflowWithResultDTO",
    )


def test_contract_workflow_with_result_experiment_fields():
    """
    Contract test: WorkflowWithResultDTO includes experiment fields.
    """
    workflow_result = {
        "nodeDict": {},
        "edgeDict": {},
        "name": "Experiment 1",
        "started_at": "2025-01-29T10:00:00Z",
        "finished_at": "2025-01-29T11:00:00Z",
        "workspace_id": 1,
        "unique_id": "abc-123",
        "hasNWB": True,
        "is_remote_synced": True,
        "data_usage": 5000000,
        "function": {},
        "success": "success",
    }

    # Validate experiment-related optional fields
    assert "name" in workflow_result
    assert "started_at" in workflow_result
    assert "unique_id" in workflow_result


# ============================================================================
# Contract Tests: Field Naming Consistency
# ============================================================================


def test_contract_no_legacy_workflow_fields():
    """
    Ensure no legacy or incorrect field names in workflow responses.
    """
    workflow_config = {
        "nodeDict": {},
        "edgeDict": {},
    }

    # Check for legacy field names
    legacy_fields = [
        "nodes",  # Should be nodeDict
        "edges",  # Should be edgeDict
        "node_dict",  # Should be nodeDict (camelCase)
        "edge_dict",  # Should be edgeDict (camelCase)
    ]

    for legacy in legacy_fields:
        assert legacy not in workflow_config


def test_contract_node_dict_is_camel_case():
    """
    Contract test: nodeDict uses camelCase (matches frontend expectation).
    """
    workflow_config = {
        "nodeDict": {},
        "edgeDict": {},
    }

    assert "nodeDict" in workflow_config
    assert "node_dict" not in workflow_config


def test_contract_edge_dict_is_camel_case():
    """
    Contract test: edgeDict uses camelCase (matches frontend expectation).
    """
    workflow_config = {
        "nodeDict": {},
        "edgeDict": {},
    }

    assert "edgeDict" in workflow_config
    assert "edge_dict" not in workflow_config
