import os
import shutil

import h5py
import numpy as np
import pytest
import scipy.io
import yaml

from studio.app.dir_path import DIRPATH

WORKSPACE_ID = "test_structured"
UNIQUE_ID = "run_001"


@pytest.fixture(scope="module", autouse=True)
def setup_structured_test_data():
    """Create temporary HDF5/MAT files and a workflow.yaml for testing."""
    input_dir = os.path.join(DIRPATH.INPUT_DIR, WORKSPACE_ID)
    output_dir = os.path.join(DIRPATH.OUTPUT_DIR, WORKSPACE_ID, UNIQUE_ID)
    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    # Create a 2D HDF5 file (should resolve to timeseries)
    hdf5_2d_path = os.path.join(input_dir, "data_2d.h5")
    with h5py.File(hdf5_2d_path, "w") as f:
        f.create_dataset("recording/traces", data=np.random.rand(100, 5))

    # Create a 3D HDF5 file (should resolve to images)
    hdf5_3d_path = os.path.join(input_dir, "data_3d.h5")
    with h5py.File(hdf5_3d_path, "w") as f:
        f.create_dataset("recording/frames", data=np.random.rand(10, 64, 64))

    # Create a 1D HDF5 file (should resolve to bar)
    hdf5_1d_path = os.path.join(input_dir, "data_1d.h5")
    with h5py.File(hdf5_1d_path, "w") as f:
        f.create_dataset("values", data=np.random.rand(50))

    # Create a MAT file with 2D data (should resolve to timeseries)
    mat_path = os.path.join(input_dir, "data.mat")
    scipy.io.savemat(mat_path, {"data": {"behavior": np.random.rand(80, 4)}})

    # Create workflow.yaml with nodes referencing these files
    workflow = {
        "nodeDict": {
            "hdf5_2d_node": {
                "data": {
                    "label": "HDF5 2D",
                    "param": {},
                    "path": "data_2d.h5",
                    "type": "input",
                    "fileType": "hdf5",
                    "hdf5Path": "recording/traces",
                    "matPath": None,
                },
                "id": "hdf5_2d_node",
                "type": "HDF5FileNode",
                "position": {"x": 0, "y": 0},
                "style": {
                    "border": None,
                    "borderRadius": None,
                    "height": None,
                    "padding": None,
                    "width": None,
                },
            },
            "hdf5_3d_node": {
                "data": {
                    "label": "HDF5 3D",
                    "param": {},
                    "path": "data_3d.h5",
                    "type": "input",
                    "fileType": "hdf5",
                    "hdf5Path": "recording/frames",
                    "matPath": None,
                },
                "id": "hdf5_3d_node",
                "type": "HDF5FileNode",
                "position": {"x": 0, "y": 0},
                "style": {
                    "border": None,
                    "borderRadius": None,
                    "height": None,
                    "padding": None,
                    "width": None,
                },
            },
            "hdf5_1d_node": {
                "data": {
                    "label": "HDF5 1D",
                    "param": {},
                    "path": "data_1d.h5",
                    "type": "input",
                    "fileType": "hdf5",
                    "hdf5Path": "values",
                    "matPath": None,
                },
                "id": "hdf5_1d_node",
                "type": "HDF5FileNode",
                "position": {"x": 0, "y": 0},
                "style": {
                    "border": None,
                    "borderRadius": None,
                    "height": None,
                    "padding": None,
                    "width": None,
                },
            },
            "mat_node": {
                "data": {
                    "label": "MAT 2D",
                    "param": {},
                    "path": "data.mat",
                    "type": "input",
                    "fileType": "matlab",
                    "hdf5Path": None,
                    "matPath": "data/behavior",
                },
                "id": "mat_node",
                "type": "MatlabFileNode",
                "position": {"x": 0, "y": 0},
                "style": {
                    "border": None,
                    "borderRadius": None,
                    "height": None,
                    "padding": None,
                    "width": None,
                },
            },
            "no_path_node": {
                "data": {
                    "label": "No Path",
                    "param": {},
                    "path": "data_2d.h5",
                    "type": "input",
                    "fileType": "hdf5",
                    "hdf5Path": None,
                    "matPath": None,
                },
                "id": "no_path_node",
                "type": "HDF5FileNode",
                "position": {"x": 0, "y": 0},
                "style": {
                    "border": None,
                    "borderRadius": None,
                    "height": None,
                    "padding": None,
                    "width": None,
                },
            },
            "missing_file_node": {
                "data": {
                    "label": "Missing File",
                    "param": {},
                    "path": "nonexistent.h5",
                    "type": "input",
                    "fileType": "hdf5",
                    "hdf5Path": "data",
                    "matPath": None,
                },
                "id": "missing_file_node",
                "type": "HDF5FileNode",
                "position": {"x": 0, "y": 0},
                "style": {
                    "border": None,
                    "borderRadius": None,
                    "height": None,
                    "padding": None,
                    "width": None,
                },
            },
            "bad_dataset_node": {
                "data": {
                    "label": "Bad Dataset",
                    "param": {},
                    "path": "data_2d.h5",
                    "type": "input",
                    "fileType": "hdf5",
                    "hdf5Path": "nonexistent/path",
                    "matPath": None,
                },
                "id": "bad_dataset_node",
                "type": "HDF5FileNode",
                "position": {"x": 0, "y": 0},
                "style": {
                    "border": None,
                    "borderRadius": None,
                    "height": None,
                    "padding": None,
                    "width": None,
                },
            },
        },
        "edgeDict": {},
    }

    workflow_path = os.path.join(output_dir, "workflow.yaml")
    with open(workflow_path, "w") as f:
        yaml.dump(workflow, f)

    yield

    # Cleanup
    shutil.rmtree(input_dir, ignore_errors=True)
    shutil.rmtree(os.path.join(DIRPATH.OUTPUT_DIR, WORKSPACE_ID), ignore_errors=True)


def test_structured_hdf5_2d_returns_timeseries(client):
    response = client.get(
        f"/outputs/structured/{WORKSPACE_ID}/{UNIQUE_ID}/hdf5_2d_node"
    )
    data = response.json()

    assert response.status_code == 200
    assert data["data_type"] == "timeseries"
    assert isinstance(data["data"], list)
    assert len(data["data"]) == 100
    assert len(data["data"][0]) == 5
    assert isinstance(data["columns"], list)
    assert isinstance(data["index"], list)
    assert data["dataset_path"] == "recording/traces"


def test_structured_hdf5_3d_returns_images(client):
    response = client.get(
        f"/outputs/structured/{WORKSPACE_ID}/{UNIQUE_ID}/hdf5_3d_node",
        params={"start_index": 0, "end_index": 10},
    )
    data = response.json()

    assert response.status_code == 200
    assert data["data_type"] == "images"
    assert isinstance(data["data"], list)
    assert len(data["data"]) == 10
    assert len(data["data"][0]) == 64
    assert len(data["data"][0][0]) == 64
    assert data["total_frames"] == 10
    assert data["dataset_path"] == "recording/frames"


def test_structured_hdf5_3d_pagination(client):
    response = client.get(
        f"/outputs/structured/{WORKSPACE_ID}/{UNIQUE_ID}/hdf5_3d_node",
        params={"start_index": 2, "end_index": 5},
    )
    data = response.json()

    assert response.status_code == 200
    assert data["data_type"] == "images"
    assert len(data["data"]) == 3
    assert data["total_frames"] == 10


def test_structured_hdf5_1d_returns_bar(client):
    response = client.get(
        f"/outputs/structured/{WORKSPACE_ID}/{UNIQUE_ID}/hdf5_1d_node"
    )
    data = response.json()

    assert response.status_code == 200
    assert data["data_type"] == "bar"
    assert isinstance(data["data"], list)
    assert len(data["data"]) == 50
    assert isinstance(data["index"], list)
    assert len(data["index"]) == 50
    assert data["dataset_path"] == "values"


def test_structured_missing_workflow(client):
    response = client.get(
        "/outputs/structured/nonexistent_ws/nonexistent_uid/some_node"
    )
    assert response.status_code == 404


def test_structured_missing_node(client):
    response = client.get(
        f"/outputs/structured/{WORKSPACE_ID}/{UNIQUE_ID}/nonexistent_node"
    )
    assert response.status_code == 404


def test_structured_no_hdf5path_or_matpath(client):
    response = client.get(
        f"/outputs/structured/{WORKSPACE_ID}/{UNIQUE_ID}/no_path_node"
    )
    assert response.status_code == 400
    assert "hdf5Path or matPath" in response.json()["detail"]


def test_structured_mat_2d_returns_timeseries(client):
    response = client.get(f"/outputs/structured/{WORKSPACE_ID}/{UNIQUE_ID}/mat_node")
    data = response.json()

    assert response.status_code == 200
    assert data["data_type"] == "timeseries"
    assert isinstance(data["data"], list)
    assert len(data["data"]) == 80
    assert len(data["data"][0]) == 4
    assert isinstance(data["columns"], list)
    assert data["dataset_path"] == "data/behavior"
    assert isinstance(data["index"], list)


def test_structured_missing_file(client):
    response = client.get(
        f"/outputs/structured/{WORKSPACE_ID}/{UNIQUE_ID}/missing_file_node"
    )
    assert response.status_code == 404
    assert "File not found" in response.json()["detail"]


def test_structured_bad_dataset_path(client):
    response = client.get(
        f"/outputs/structured/{WORKSPACE_ID}/{UNIQUE_ID}/bad_dataset_node"
    )
    assert response.status_code == 404
    assert "Dataset not found" in response.json()["detail"]


def test_structured_3d_default_pagination(client):
    """When start_index/end_index are omitted, defaults to 0-10."""
    response = client.get(
        f"/outputs/structured/{WORKSPACE_ID}/{UNIQUE_ID}/hdf5_3d_node"
    )
    data = response.json()

    assert response.status_code == 200
    assert data["data_type"] == "images"
    assert len(data["data"]) == 10
    assert data["total_frames"] == 10


@pytest.mark.asyncio
async def test_structured_missing_input_triggers_on_demand_sync():
    """A missing input must trigger an on-demand input-file sync before 404, so
    public viewers get the lazily-synced input data (mirrors the csv/image
    endpoints)."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from fastapi import HTTPException

    from studio.app.common.routers.outputs import get_structured_data

    node = MagicMock()
    node.data.path = "lazy_input.h5"
    node.data.hdf5Path = "x"
    node.data.matPath = None
    config = MagicMock()
    config.nodeDict.get.return_value = node

    with patch(
        "studio.app.common.routers.outputs.WorkflowConfigReader.read",
        return_value=config,
    ), patch(
        "studio.app.common.routers.outputs.os.path.exists", return_value=False
    ), patch(
        "studio.app.common.routers.outputs.RemoteStorageDownloadUtils."
        "ensure_input_file_synced",
        new_callable=AsyncMock,
    ) as mock_sync:
        with pytest.raises(HTTPException) as exc:
            await get_structured_data(
                workspace_id="6",
                unique_id="abc123",
                node_id="n",
                remote_bucket_name="bucket-x",
            )

    # Sync is keyed on the input file + bucket, then 404 if still missing.
    mock_sync.assert_awaited_once_with("6", "lazy_input.h5", "bucket-x")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_structured_missing_input_resyncs_via_download_layer():
    """Regression: a missing input is re-fetched by downloading the input file
    itself, independent of the experiment's output-sync status. Routing the
    re-fetch through the output-sync path left inputs permanently 404 once the
    input cache was wiped while the output stayed marked synced."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from fastapi import HTTPException

    from studio.app.common.routers.outputs import get_structured_data

    node = MagicMock()
    node.data.path = "wiped_input.h5"
    node.data.hdf5Path = "x"
    node.data.matPath = None
    config = MagicMock()
    config.nodeDict.get.return_value = node

    rsc = "studio.app.common.core.storage.remote_storage_controller"
    with patch(
        "studio.app.common.routers.outputs.WorkflowConfigReader.read",
        return_value=config,
    ), patch(
        "studio.app.common.routers.outputs.os.path.exists", return_value=False
    ), patch(
        # Output marked fully synced: must NOT gate the input re-fetch.
        "studio.app.common.routers.outputs.RemoteSyncStatusFileUtil."
        "check_sync_status_unsynced",
        return_value=False,
    ), patch(
        f"{rsc}.os.path.exists", return_value=False
    ), patch(
        f"{rsc}.RemoteStorageController"
    ) as mock_controller_cls:
        mock_controller_cls.is_available.return_value = True
        mock_controller_cls.return_value.download_input_data = AsyncMock(
            return_value=True
        )
        with pytest.raises(HTTPException) as exc:
            await get_structured_data(
                workspace_id="6",
                unique_id="abc123",
                node_id="n",
                remote_bucket_name="bucket-x",
            )

    mock_controller_cls.return_value.download_input_data.assert_awaited_once_with(
        "6", "wiped_input.h5"
    )
    assert exc.value.status_code == 404
