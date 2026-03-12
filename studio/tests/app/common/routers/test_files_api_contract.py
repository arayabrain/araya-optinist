"""
Contract Tests for Files API

These tests verify that API responses match the frontend TypeScript interfaces.
This ensures the backend and frontend stay in sync and prevents contract mismatches.

Frontend interfaces are defined in:
  frontend/src/api/files/Files.ts

Tested endpoints:
  - GET  /files/{workspace_id}                    -> TreeNodeTypeDTO[]
  - GET  /files/{workspace_id}/merged             -> TreeNodeWithSyncDTO[]
  - POST /files/{workspace_id}/sync/{filename}    -> FilePath
  - POST /files/{workspace_id}/upload/{filename}  -> FilePath
  - DELETE /files/{workspace_id}/delete/{filename} -> bool
  - POST /files/{workspace_id}/download           -> { file_name: string }
  - GET  /files/{workspace_id}/download/status    -> GetStatusViaUrl
"""

from studio.app.common.schemas.files import (
    DownloadStatus,
    FilePath,
    SyncStatus,
    TreeNode,
    TreeNodeWithSync,
)

# ============================================================================
# Frontend Contract Definitions
# ============================================================================
# These mirror the TypeScript interfaces in Files.ts

# NodeBaseDTO fields (shared by TreeNode and TreeNodeWithSync)
NODE_BASE_REQUIRED_FIELDS = {
    "path": str,
    "name": str,
    "isdir": bool,
}

NODE_BASE_OPTIONAL_FIELDS = {
    "shape": list,
    "nodes": list,
}

# TreeNodeWithSync additional fields
TREE_NODE_WITH_SYNC_REQUIRED_FIELDS = {
    **NODE_BASE_REQUIRED_FIELDS,
    "sync_status": str,
}

TREE_NODE_WITH_SYNC_OPTIONAL_FIELDS = {
    **NODE_BASE_OPTIONAL_FIELDS,
    "size": int,
}

# FilePath response
FILE_PATH_REQUIRED_FIELDS = {
    "file_path": str,
}

# GetStatusViaUrl / DownloadStatus
DOWNLOAD_STATUS_REQUIRED_FIELDS = {
    "total": int,
    "current": int,
}

DOWNLOAD_STATUS_OPTIONAL_FIELDS = {
    "error": str,
}

# Valid sync status values
VALID_SYNC_STATUSES = {"local", "synced", "remote"}


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
# Contract Tests: SyncStatus Enum
# ============================================================================


def test_contract_sync_status_values():
    """
    Contract test: SyncStatus enum values match frontend expectations.
    """
    backend_values = {s.value for s in SyncStatus}

    assert backend_values == VALID_SYNC_STATUSES, (
        f"SyncStatus values don't match frontend. "
        f"Backend: {backend_values}, Frontend expects: {VALID_SYNC_STATUSES}"
    )


def test_contract_sync_status_local():
    """
    Contract test: LOCAL sync status value is 'local'.
    """
    assert SyncStatus.LOCAL.value == "local"


def test_contract_sync_status_synced():
    """
    Contract test: SYNCED sync status value is 'synced'.
    """
    assert SyncStatus.SYNCED.value == "synced"


def test_contract_sync_status_remote():
    """
    Contract test: REMOTE sync status value is 'remote'.
    """
    assert SyncStatus.REMOTE.value == "remote"


# ============================================================================
# Contract Tests: TreeNode Schema
# ============================================================================


def test_contract_tree_node_file():
    """
    Contract test: TreeNode for a file serializes correctly.
    """
    node = TreeNode(
        path="/workspace/1/input/data.tif",
        name="data.tif",
        isdir=False,
        nodes=[],
        shape=[512, 512, 100],
    )

    # pydantic dataclass uses __dict__
    result = node.__dict__

    validate_contract(
        result,
        NODE_BASE_REQUIRED_FIELDS,
        NODE_BASE_OPTIONAL_FIELDS,
        context="TreeNode (file)",
    )

    assert result["isdir"] is False
    assert result["nodes"] == []


def test_contract_tree_node_directory():
    """
    Contract test: TreeNode for a directory serializes correctly.
    """
    child_node = TreeNode(
        path="/workspace/1/input/subdir/file.tif",
        name="file.tif",
        isdir=False,
        nodes=[],
    )

    parent_node = TreeNode(
        path="/workspace/1/input/subdir",
        name="subdir",
        isdir=True,
        nodes=[child_node],
    )

    result = parent_node.__dict__

    validate_contract(
        result,
        NODE_BASE_REQUIRED_FIELDS,
        NODE_BASE_OPTIONAL_FIELDS,
        context="TreeNode (directory)",
    )

    assert result["isdir"] is True
    assert len(result["nodes"]) == 1


def test_contract_tree_node_shape_optional():
    """
    Contract test: TreeNode shape can be None.
    """
    node = TreeNode(
        path="/workspace/1/input/data.mat",
        name="data.mat",
        isdir=False,
        nodes=[],
        shape=None,
    )

    result = node.__dict__

    assert result.get("shape") is None


# ============================================================================
# Contract Tests: TreeNodeWithSync Schema
# ============================================================================


def test_contract_tree_node_with_sync_local():
    """
    Contract test: TreeNodeWithSync with LOCAL status serializes correctly.
    """
    node = TreeNodeWithSync(
        path="/workspace/1/input/local_file.tif",
        name="local_file.tif",
        isdir=False,
        nodes=[],
        sync_status=SyncStatus.LOCAL,
        size=1024000,
    )

    result = node.__dict__

    validate_contract(
        result,
        TREE_NODE_WITH_SYNC_REQUIRED_FIELDS,
        TREE_NODE_WITH_SYNC_OPTIONAL_FIELDS,
        context="TreeNodeWithSync (local)",
    )

    assert result["sync_status"] == "local"


def test_contract_tree_node_with_sync_synced():
    """
    Contract test: TreeNodeWithSync with SYNCED status serializes correctly.
    """
    node = TreeNodeWithSync(
        path="/workspace/1/input/synced_file.tif",
        name="synced_file.tif",
        isdir=False,
        nodes=[],
        sync_status=SyncStatus.SYNCED,
        size=2048000,
    )

    result = node.__dict__

    assert result["sync_status"] == "synced"


def test_contract_tree_node_with_sync_remote():
    """
    Contract test: TreeNodeWithSync with REMOTE status serializes correctly.
    """
    node = TreeNodeWithSync(
        path="/workspace/1/input/remote_file.tif",
        name="remote_file.tif",
        isdir=False,
        nodes=[],
        sync_status=SyncStatus.REMOTE,
        size=3072000,
    )

    result = node.__dict__

    assert result["sync_status"] == "remote"


def test_contract_tree_node_with_sync_size_optional():
    """
    Contract test: TreeNodeWithSync size can be None.
    """
    node = TreeNodeWithSync(
        path="/workspace/1/input/file.tif",
        name="file.tif",
        isdir=False,
        nodes=[],
        sync_status=SyncStatus.SYNCED,
        size=None,
    )

    result = node.__dict__

    assert result.get("size") is None


# ============================================================================
# Contract Tests: FilePath Response
# ============================================================================


def test_contract_file_path_response():
    """
    Contract test: FilePath response serializes correctly.
    """
    file_path = FilePath(file_path="/workspace/1/input/uploaded_file.tif")

    result = file_path.__dict__

    validate_contract(
        result,
        FILE_PATH_REQUIRED_FIELDS,
        context="FilePath",
    )


def test_contract_file_path_is_string():
    """
    Contract test: file_path is a string.
    """
    file_path = FilePath(file_path="/path/to/file")

    result = file_path.__dict__

    assert isinstance(result["file_path"], str)


# ============================================================================
# Contract Tests: DownloadStatus Response
# ============================================================================


def test_contract_download_status_response():
    """
    Contract test: DownloadStatus response serializes correctly.
    """
    status = DownloadStatus(
        total=1000000,
        current=500000,
        error=None,
    )

    result = status.__dict__

    validate_contract(
        result,
        DOWNLOAD_STATUS_REQUIRED_FIELDS,
        DOWNLOAD_STATUS_OPTIONAL_FIELDS,
        context="DownloadStatus",
    )


def test_contract_download_status_with_error():
    """
    Contract test: DownloadStatus with error serializes correctly.
    """
    status = DownloadStatus(
        total=1000000,
        current=100000,
        error="Download failed: connection timeout",
    )

    result = status.__dict__

    assert result["error"] is not None
    assert isinstance(result["error"], str)


def test_contract_download_status_progress():
    """
    Contract test: DownloadStatus total and current are integers.
    """
    status = DownloadStatus(
        total=5000000,
        current=2500000,
    )

    result = status.__dict__

    assert isinstance(result["total"], int)
    assert isinstance(result["current"], int)


# ============================================================================
# Contract Tests: Field Naming Consistency
# ============================================================================


def test_contract_no_legacy_file_fields():
    """
    Ensure no legacy or camelCase field names in file responses.
    """
    node = TreeNodeWithSync(
        path="/test/path",
        name="test.tif",
        isdir=False,
        nodes=[],
        sync_status=SyncStatus.SYNCED,
    )

    result = node.__dict__

    # Note: isdir is camelCase but this is intentional to match frontend
    legacy_fields = [
        "syncStatus",  # camelCase (should be sync_status)
        "filePath",  # camelCase (should be file_path in FilePath)
    ]

    for legacy in legacy_fields:
        assert legacy not in result


def test_contract_isdir_is_camel_case():
    """
    Contract test: isdir remains camelCase (matches frontend expectation).
    """
    node = TreeNode(
        path="/test/path",
        name="test",
        isdir=True,
        nodes=[],
    )

    result = node.__dict__

    # isdir should be camelCase
    assert "isdir" in result
    # is_dir should NOT be present
    assert "is_dir" not in result
