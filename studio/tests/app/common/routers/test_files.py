from studio.app.common.routers.files import (
    DirTreeGetter,
    get_hdf5_structure_dict,
    get_mat_structure_dict,
)
from studio.app.common.schemas.files import SyncStatus, TreeNode

workspace_id = "1"


def test_create_files(client):
    response = client.get(f"/files/{workspace_id}?file_type=image")
    data = response.json()

    assert response.status_code == 200
    assert isinstance(data, list)
    assert len(data) > 0


def test_DirTreeGetter_tif():
    output = DirTreeGetter.get_tree(
        workspace_id, [".tif", ".tiff", ".TIF", ".TIFF"], "files"
    )
    assert len(output) == 4
    assert isinstance(output[0], TreeNode)


def test_get_files_merged(client):
    """Test the merged endpoint returns files with sync status."""
    response = client.get(f"/files/{workspace_id}/merged?file_type=image")
    data = response.json()

    assert response.status_code == 200
    assert isinstance(data, list)

    # If there are files, verify they have the expected structure
    if len(data) > 0:
        for node in data:
            assert "path" in node
            assert "name" in node
            assert "isdir" in node
            assert "sync_status" in node
            # sync_status should be one of the valid values
            assert node["sync_status"] in ["local", "synced", "remote"]


def test_sync_status_enum():
    """Test SyncStatus enum values."""
    assert SyncStatus.LOCAL == "local"
    assert SyncStatus.SYNCED == "synced"
    assert SyncStatus.REMOTE == "remote"


def test_hdf5_structure_caching():
    """Test HDF5 structure caching functions."""
    # Check that get_hdf5_structure_dict returns empty dict when no cache exists
    # Using a non-existent workspace to ensure no cache
    result = get_hdf5_structure_dict("non_existent_workspace_12345")
    assert result == {}


def test_mat_structure_caching():
    """Test MATLAB structure caching functions."""
    # Check that get_mat_structure_dict returns empty dict when no cache exists
    # Using a non-existent workspace to ensure no cache
    result = get_mat_structure_dict("non_existent_workspace_12345")
    assert result == {}
