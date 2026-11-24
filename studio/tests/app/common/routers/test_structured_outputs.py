import asyncio
import os
import shutil
import threading
from unittest.mock import AsyncMock, patch

import h5py
import httpx
import numpy as np
import pytest
import scipy.io
import yaml
from botocore.exceptions import ClientError

from studio.__main_unit__ import app
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
    RemoteSyncAction,
    RemoteSyncLockFileUtil,
    RemoteSyncStatusFileUtil,
)
from studio.app.common.core.storage.s3_storage_controller import S3StorageController
from studio.app.common.routers import outputs
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

    # Create a compound (table) HDF5 file; one column should resolve to bar
    hdf5_table_path = os.path.join(input_dir, "data_table.h5")
    with h5py.File(hdf5_table_path, "w") as f:
        table = np.zeros(50, dtype=[("time", "f8"), ("lick", "f8")])
        table["lick"] = np.random.rand(50)
        f.create_dataset("table", data=table)

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
            "hdf5_table_col_node": {
                "data": {
                    "label": "HDF5 table column",
                    "param": {},
                    "path": "data_table.h5",
                    "type": "input",
                    "fileType": "hdf5",
                    "hdf5Path": "table/lick",
                    "matPath": None,
                },
                "id": "hdf5_table_col_node",
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
        f"/api/visualizations/structured/{WORKSPACE_ID}/{UNIQUE_ID}/hdf5_2d_node"
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
        f"/api/visualizations/structured/{WORKSPACE_ID}/{UNIQUE_ID}/hdf5_3d_node",
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
        f"/api/visualizations/structured/{WORKSPACE_ID}/{UNIQUE_ID}/hdf5_3d_node",
        params={"start_index": 2, "end_index": 5},
    )
    data = response.json()

    assert response.status_code == 200
    assert data["data_type"] == "images"
    assert len(data["data"]) == 3
    assert data["total_frames"] == 10


def test_structured_hdf5_1d_returns_bar(client):
    response = client.get(
        f"/api/visualizations/structured/{WORKSPACE_ID}/{UNIQUE_ID}/hdf5_1d_node"
    )
    data = response.json()

    assert response.status_code == 200
    assert data["data_type"] == "bar"
    assert isinstance(data["data"], list)
    assert len(data["data"]) == 50
    assert isinstance(data["index"], list)
    assert len(data["index"]) == 50
    assert data["dataset_path"] == "values"


def test_structured_compound_column_returns_bar(client):
    response = client.get(
        f"/api/visualizations/structured/{WORKSPACE_ID}/{UNIQUE_ID}/hdf5_table_col_node"
    )
    data = response.json()

    assert response.status_code == 200
    assert data["data_type"] == "bar"
    assert len(data["data"]) == 50
    assert data["dataset_path"] == "table/lick"


def test_structured_missing_workflow(client):
    response = client.get(
        "/api/visualizations/structured/nonexistent_ws/nonexistent_uid/some_node"
    )
    assert response.status_code == 404


def _structured_url(unique_id, node_id):
    return f"/api/visualizations/structured/{WORKSPACE_ID}/{unique_id}/{node_id}"


def _drop_record(unique_id):
    shutil.rmtree(
        os.path.join(DIRPATH.OUTPUT_DIR, WORKSPACE_ID, unique_id), ignore_errors=True
    )


def _remote_config(delay=0.0):
    """
    Stands in for the remote metadata download: after `delay` seconds, writes
    the module record's workflow.yaml into the requested record. Returns the
    patch for RemoteStorageController and the record ids it was asked for.
    """
    source = os.path.join(DIRPATH.OUTPUT_DIR, WORKSPACE_ID, UNIQUE_ID, "workflow.yaml")
    asked = []

    async def download_experiment_meta(self, workspace_id, unique_id):
        asked.append(unique_id)
        await asyncio.sleep(delay)
        record_dir = os.path.join(DIRPATH.OUTPUT_DIR, workspace_id, unique_id)
        os.makedirs(record_dir, exist_ok=True)
        shutil.copy(source, os.path.join(record_dir, "workflow.yaml"))
        return True

    meta_patch = patch.object(
        RemoteStorageController, "download_experiment_meta", download_experiment_meta
    )
    return meta_patch, asked


def test_structured_fetches_a_missing_config_before_reading_it(client):
    """A server that has not synced the record has no workflow.yaml for it; the
    route fetches the record's metadata, then reads it."""
    meta_patch, asked = _remote_config()
    try:
        with meta_patch:
            response = client.get(_structured_url("run_unsynced", "hdf5_1d_node"))
    finally:
        _drop_record("run_unsynced")

    assert response.status_code == 200, response.text
    assert response.json()["data_type"] == "bar"
    assert asked == ["run_unsynced"]


def test_structured_reads_a_local_config_without_the_record_lock(client):
    """A run holds the record's lock, with its sync status at processing, until
    its upload starts; a server that has the config needs neither."""
    token = RemoteSyncLockFileUtil.create_sync_lock_file(WORKSPACE_ID, UNIQUE_ID)
    RemoteSyncStatusFileUtil.create_sync_status_file_for_processing(
        "bucket", WORKSPACE_ID, UNIQUE_ID, RemoteSyncAction.UPLOAD
    )
    meta_patch, asked = _remote_config()
    try:
        with meta_patch:
            response = client.get(_structured_url(UNIQUE_ID, "hdf5_1d_node"))
    finally:
        RemoteSyncLockFileUtil.delete_sync_lock_file(WORKSPACE_ID, UNIQUE_ID, token)
        os.remove(
            os.path.join(
                DIRPATH.OUTPUT_DIR,
                WORKSPACE_ID,
                UNIQUE_ID,
                RemoteSyncStatusFileUtil.REMOTE_SYNC_STATUS_FILE,
            )
        )

    assert response.status_code == 200, response.text
    assert asked == []


@pytest.mark.asyncio
async def test_structured_cards_on_a_server_without_the_config_both_load():
    """The dialog's HDF5 and MAT cards ask at once: the second waits for the
    first's fetch instead of failing on the lock it holds."""
    meta_patch, asked = _remote_config(delay=0.3)
    try:
        with meta_patch:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as http:
                responses = await asyncio.gather(
                    http.get(_structured_url("run_unsynced_pair", "hdf5_1d_node")),
                    http.get(_structured_url("run_unsynced_pair", "mat_node")),
                )
    finally:
        _drop_record("run_unsynced_pair")

    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    assert asked == ["run_unsynced_pair"]


def test_structured_answers_423_when_the_lock_outlasts_the_wait(client, monkeypatch):
    monkeypatch.setattr(outputs, "STRUCTURED_CONFIG_WAIT_SECONDS", 0.3)
    token = RemoteSyncLockFileUtil.create_sync_lock_file(WORKSPACE_ID, "run_locked")
    meta_patch, asked = _remote_config()
    try:
        with meta_patch:
            response = client.get(_structured_url("run_locked", "hdf5_1d_node"))
    finally:
        RemoteSyncLockFileUtil.delete_sync_lock_file(WORKSPACE_ID, "run_locked", token)
        _drop_record("run_locked")

    assert response.status_code == 423, response.text
    assert asked == []


def test_structured_answers_429_when_the_config_cannot_be_fetched(client):
    """A failed fetch asks the client to retry later. Not 404, which reads as a
    missing record; not 503, which the frontend takes for an unreachable
    instance."""

    async def unreachable(self, workspace_id, unique_id):
        raise RuntimeError("S3 unreachable")

    try:
        with patch.object(
            RemoteStorageController, "download_experiment_meta", unreachable
        ):
            response = client.get(_structured_url("run_unreachable", "hdf5_1d_node"))
    finally:
        _drop_record("run_unreachable")

    assert response.status_code == 429, response.text
    assert response.headers["Retry-After"] == "5"


def _client_error(code):
    return ClientError({"Error": {"Code": code, "Message": code}}, "HeadObject")


def _via_s3(head_error=None, download_error=None):
    """
    Runs the real S3StorageController under the route, on a mocked client whose
    bucket holds the module record's workflow.yaml and no other metadata file.
    The errors, when given, are what S3 answers for workflow.yaml.
    """
    source = os.path.join(DIRPATH.OUTPUT_DIR, WORKSPACE_ID, UNIQUE_ID, "workflow.yaml")
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None

    async def head_object(Bucket, Key):
        if not Key.endswith("/workflow.yaml"):
            raise _client_error("404")
        if head_error:
            raise head_error

    async def download_file(bucket, key, path):
        if download_error:
            # aioboto3 creates its target before the body arrives
            with open(path, "w") as f:
                f.write("nodeDict:\n  par")
            raise download_error
        shutil.copy(source, path)

    client.head_object.side_effect = head_object
    client.download_file.side_effect = download_file

    async def download_experiment_meta(self, workspace_id, unique_id):
        s3 = S3StorageController("bucket")
        with patch.object(
            s3, "_S3StorageController__get_s3_client", return_value=client
        ):
            return await s3.download_experiment_meta(workspace_id, unique_id)

    return patch.object(
        RemoteStorageController, "download_experiment_meta", download_experiment_meta
    )


@pytest.mark.parametrize(
    "head_error, download_error, expected",
    [
        (None, None, 200),
        (_client_error("404"), None, 404),
        (_client_error("SlowDown"), None, 429),
        (None, _client_error("InternalError"), 429),
    ],
    ids=["fetched", "absent", "throttled", "failed-mid-body"],
)
def test_structured_tells_a_failed_s3_read_from_an_absent_config(
    client, head_error, download_error, expected
):
    """A config S3 does not have reads as missing. A read S3 failed asks the
    client to retry, and leaves no partial config for the retry to trust."""
    unique_id = "run_via_s3"
    config_path = os.path.join(
        DIRPATH.OUTPUT_DIR, WORKSPACE_ID, unique_id, "workflow.yaml"
    )
    try:
        with _via_s3(head_error, download_error):
            response = client.get(_structured_url(unique_id, "hdf5_1d_node"))
        config_kept = os.path.exists(config_path)
    finally:
        _drop_record(unique_id)

    assert response.status_code == expected, response.text
    assert config_kept == (expected == 200)
    if expected == 429:
        assert response.headers["Retry-After"] == "5"


def test_structured_missing_node(client):
    response = client.get(
        f"/api/visualizations/structured/{WORKSPACE_ID}/{UNIQUE_ID}/nonexistent_node"
    )
    assert response.status_code == 404


def test_structured_no_hdf5path_or_matpath(client):
    response = client.get(
        f"/api/visualizations/structured/{WORKSPACE_ID}/{UNIQUE_ID}/no_path_node"
    )
    assert response.status_code == 400
    assert "hdf5Path or matPath" in response.json()["detail"]


def test_structured_mat_2d_returns_timeseries(client):
    response = client.get(
        f"/api/visualizations/structured/{WORKSPACE_ID}/{UNIQUE_ID}/mat_node"
    )
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
        f"/api/visualizations/structured/{WORKSPACE_ID}/{UNIQUE_ID}/missing_file_node"
    )
    assert response.status_code == 404
    assert "File not found" in response.json()["detail"]


def test_structured_bad_dataset_path(client):
    response = client.get(
        f"/api/visualizations/structured/{WORKSPACE_ID}/{UNIQUE_ID}/bad_dataset_node"
    )
    assert response.status_code == 404
    assert "Dataset not found" in response.json()["detail"]


def test_structured_3d_default_pagination(client):
    """When start_index/end_index are omitted, defaults to 0-10."""
    response = client.get(
        f"/api/visualizations/structured/{WORKSPACE_ID}/{UNIQUE_ID}/hdf5_3d_node"
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
        "studio.app.common.routers.outputs._ensure_workflow_config",
        new_callable=AsyncMock,
    ), patch(
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
        "studio.app.common.routers.outputs._ensure_workflow_config",
        new_callable=AsyncMock,
    ), patch(
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


def _hdf5_2d_values():
    path = os.path.join(DIRPATH.INPUT_DIR, WORKSPACE_ID, "data_2d.h5")
    with h5py.File(path, "r") as f:
        return f["recording/traces"][:]


def test_structured_2d_within_budget_is_unchanged(client):
    response = client.get(
        f"/api/visualizations/structured/{WORKSPACE_ID}/{UNIQUE_ID}/hdf5_2d_node"
    )
    data = response.json()

    assert response.status_code == 200
    assert np.array_equal(np.array(data["data"]), _hdf5_2d_values())
    assert data["index"] == [str(i) for i in range(100)]
    assert data["columns"] == [str(c) for c in range(5)]
    assert data["shape"] == [100, 5]


def test_structured_2d_over_budget_is_reduced(client, monkeypatch):
    monkeypatch.setattr("studio.app.common.routers.outputs.STRUCTURED_MAX_VALUES", 60)
    monkeypatch.setattr("studio.app.common.routers.outputs.STRUCTURED_MAX_COLUMNS", 3)
    response = client.get(
        f"/api/visualizations/structured/{WORKSPACE_ID}/{UNIQUE_ID}/hdf5_2d_node"
    )
    data = response.json()

    assert response.status_code == 200
    assert len(data["columns"]) == 3
    assert len(data["data"]) * len(data["data"][0]) <= 60
    assert data["index"] == [str(i) for i in range(20)]
    assert data["columns"] == ["0", "1", "2"]
    assert data["shape"] == [100, 5]
    assert np.array_equal(np.array(data["data"]), _hdf5_2d_values()[:20, :3])


def test_structured_mat_2d_over_budget_is_reduced(client, monkeypatch):
    monkeypatch.setattr("studio.app.common.routers.outputs.STRUCTURED_MAX_VALUES", 50)
    monkeypatch.setattr("studio.app.common.routers.outputs.STRUCTURED_MAX_COLUMNS", 3)
    response = client.get(
        f"/api/visualizations/structured/{WORKSPACE_ID}/{UNIQUE_ID}/mat_node"
    )
    data = response.json()

    assert response.status_code == 200
    assert data["shape"] == [80, 4]
    assert data["columns"] == ["0", "1", "2"]
    assert len(data["data"]) * len(data["data"][0]) <= 50
    assert data["index"] == [str(i) for i in range(16)]


@pytest.mark.parametrize(
    "params, frames",
    [
        ({}, 3),
        ({"start_index": 0, "end_index": 1_000_000}, 3),
        ({"start_index": 8, "end_index": 1_000_000}, 2),
        ({"start_index": 2, "end_index": 4}, 2),
    ],
)
def test_structured_3d_frames_are_capped(client, monkeypatch, params, frames):
    monkeypatch.setattr("studio.app.common.routers.outputs.STRUCTURED_MAX_FRAMES", 3)
    response = client.get(
        f"/api/visualizations/structured/{WORKSPACE_ID}/{UNIQUE_ID}/hdf5_3d_node",
        params=params,
    )
    data = response.json()

    assert response.status_code == 200
    assert len(data["data"]) == frames
    assert data["total_frames"] == 10


@pytest.mark.parametrize(
    "budget, status, frames",
    [(64 * 64 * 4, 200, 4), (64 * 64 * 20, 200, 10), (64 * 64 - 1, 422, None)],
)
def test_structured_3d_frames_fit_the_value_budget(
    client, monkeypatch, budget, status, frames
):
    monkeypatch.setattr(
        "studio.app.common.routers.outputs.STRUCTURED_MAX_3D_VALUES", budget
    )
    response = client.get(
        f"/api/visualizations/structured/{WORKSPACE_ID}/{UNIQUE_ID}/hdf5_3d_node",
        params={"start_index": 0, "end_index": 1_000_000},
    )

    assert response.status_code == status
    if frames is not None:
        assert len(response.json()["data"]) == frames


@pytest.mark.parametrize("node_id", ["hdf5_1d_node", "hdf5_table_col_node"])
def test_structured_1d_over_budget_is_reduced(client, monkeypatch, node_id):
    monkeypatch.setattr("studio.app.common.routers.outputs.STRUCTURED_MAX_VALUES", 20)
    response = client.get(
        f"/api/visualizations/structured/{WORKSPACE_ID}/{UNIQUE_ID}/{node_id}"
    )
    data = response.json()

    assert response.status_code == 200
    assert len(data["data"]) == 20
    assert data["index"] == list(range(20))
    assert data["shape"] == [50]


@pytest.mark.parametrize(
    "params", [{"end_index": -1}, {"start_index": -1}, {"start_index": "x"}]
)
def test_structured_rejects_negative_indices(client, params):
    response = client.get(
        f"/api/visualizations/structured/{WORKSPACE_ID}/{UNIQUE_ID}/hdf5_3d_node",
        params=params,
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_structured_read_not_started_in_time_is_refused(monkeypatch):
    monkeypatch.setattr(outputs, "STRUCTURED_READ_WAIT_SECONDS", 0.3)
    reading, released = threading.Event(), threading.Event()
    read = []
    structured_body = outputs._structured_body

    def held_structured_body(full_path, *args):
        read.append(os.path.basename(full_path))
        reading.set()
        released.wait(5)
        return structured_body(full_path, *args)

    monkeypatch.setattr(outputs, "_structured_body", held_structured_body)
    url = f"/api/visualizations/structured/{WORKSPACE_ID}/{UNIQUE_ID}"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        held = asyncio.create_task(http.get(f"{url}/hdf5_3d_node"))
        for _ in range(500):
            if reading.is_set():
                break
            await asyncio.sleep(0.01)
        refused = await http.get(f"{url}/hdf5_1d_node")
        released.set()
        held = await held

    assert refused.status_code == 429, refused.text
    assert refused.headers["Retry-After"] == "5"
    assert held.status_code == 200, held.text
    assert read == ["data_3d.h5"]
