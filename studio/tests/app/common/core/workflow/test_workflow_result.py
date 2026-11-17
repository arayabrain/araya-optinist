import os
import pickle
import shutil
import time
from pathlib import Path

import numpy as np
import pytest

from studio.app.common.core.auth.auth_dependencies import _get_user_remote_bucket_name
from studio.app.common.core.experiment.experiment import ExptFunction
from studio.app.common.core.mode import MODE
from studio.app.common.core.rules.runner import Runner
from studio.app.common.core.storage.mock_storage_controller import MockStorageController
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
    RemoteSyncAction,
    RemoteSyncStatusFileUtil,
)
from studio.app.common.core.workflow.workflow import Message, NodeRunStatus
from studio.app.common.core.workflow.workflow_result import (
    NodeResult,
    WorkflowMonitor,
    WorkflowResult,
)
from studio.app.common.dataclass.html import HTMLData
from studio.app.common.dataclass.timeseries import TimeSeriesData
from studio.app.common.schemas.outputs import PlotMetaData
from studio.app.dir_path import DIRPATH

# Set test mode before getting bucket name at module level
MODE.IS_TEST = True
remote_bucket_name = _get_user_remote_bucket_name()
workspace_id = "default"
unique_id = "result_test"
node_id_list = ["func1", "func2"]
node_1st = node_id_list[0]

workflow_dirpath = f"{DIRPATH.DATA_DIR}/output_test/{workspace_id}/{unique_id}"
output_dirpath = f"{DIRPATH.OUTPUT_DIR}/{workspace_id}/{unique_id}"
pickle_path = (
    f"{DIRPATH.DATA_DIR}/output_test/{workspace_id}/{unique_id}/{node_1st}/func1.pkl"
)


@pytest.mark.asyncio
async def test_WorkflowResult_get_success():
    # ----------------------------------------
    # Preparation
    # ----------------------------------------

    shutil.copytree(
        workflow_dirpath,
        output_dirpath,
        dirs_exist_ok=True,
    )

    # Write pid_file
    Runner.write_pid_file(
        output_dirpath, "xxxx_dummy_func", "xxxx_dummy_func_script.py"
    )

    # Write remote storage related files
    if RemoteStorageController.is_available():
        RemoteSyncStatusFileUtil.create_sync_status_file_for_success(
            remote_bucket_name,
            workspace_id,
            unique_id,
            RemoteSyncAction.UPLOAD,
        )

    # ----------------------------------------
    # Tests
    # ----------------------------------------

    output = await WorkflowResult(
        workspace_id=workspace_id, unique_id=unique_id
    ).observe(node_id_list)

    assert isinstance(output, dict)
    assert output[node_1st].status == "success"


@pytest.mark.asyncio
async def test_NodeResult_get():
    assert os.path.exists(pickle_path)

    expt_function = ExptFunction(
        unique_id=unique_id,
        name=node_1st,
        success=NodeRunStatus.RUNNING.value,
        hasNWB=False,
    )

    output = await NodeResult(
        workspace_id=workspace_id,
        unique_id=unique_id,
        node_id=node_1st,
    ).observe(expt_function)

    assert isinstance(output, Message)


@pytest.mark.asyncio
async def test_WorkflowResult_get_error():
    # ----------------------------------------
    # Preparation
    # ----------------------------------------

    shutil.copytree(
        workflow_dirpath,
        output_dirpath,
        dirs_exist_ok=True,
    )

    # Write pid_file file (causes timeout error)
    pid_file_create_time = time.time() - WorkflowMonitor.PROCESS_SNAKEMAKE_WAIT_TIMEOUT
    Runner.write_pid_file(
        output_dirpath,
        "xxxx_dummy_func",
        "xxxx_dummy_func_script.py",
        pid_file_create_time,
    )

    # Write remote storage related files
    if RemoteStorageController.is_available():
        RemoteSyncStatusFileUtil.create_sync_status_file_for_success(
            remote_bucket_name,
            workspace_id,
            unique_id,
            RemoteSyncAction.UPLOAD,
        )

    # ----------------------------------------
    # Preparation
    # ----------------------------------------

    output = await WorkflowResult(
        workspace_id=workspace_id, unique_id=unique_id
    ).observe(node_id_list)

    assert isinstance(output, dict)
    assert output[node_1st].status == "error"


@pytest.fixture()
def html_output_run():
    """result_test with func1 about to finish with an HTML output and a time
    series; yields its id, local and remote folders"""
    unique_id = "result_html_test"
    local = Path(DIRPATH.OUTPUT_DIR) / workspace_id / unique_id
    remote = Path(MockStorageController.MOCK_OUTPUT_DIR) / workspace_id / unique_id
    shutil.rmtree(local, ignore_errors=True)
    shutil.rmtree(remote, ignore_errors=True)
    shutil.copytree(workflow_dirpath, local)
    outputs = {
        "figure": HTMLData(
            "<p>figure</p>", file_name="figure", meta=PlotMetaData(title="figure")
        ),
        "trace": TimeSeriesData(np.arange(10.0).reshape(2, 5), file_name="trace"),
    }
    with open(local / node_1st / "func1.pkl", "wb") as f:
        pickle.dump(outputs, f)
    RemoteSyncStatusFileUtil.create_sync_status_file_for_success(
        remote_bucket_name, workspace_id, unique_id, RemoteSyncAction.UPLOAD
    )
    yield unique_id, local, remote
    shutil.rmtree(local, ignore_errors=True)
    shutil.rmtree(remote, ignore_errors=True)


@pytest.mark.asyncio
async def test_WorkflowResult_uploads_html_output(html_output_run):
    unique_id, local, remote = html_output_run

    await WorkflowResult(workspace_id=workspace_id, unique_id=unique_id).observe(
        [node_1st]
    )

    uploaded = {p.relative_to(remote).as_posix() for p in remote.rglob("*")}
    assert {
        DIRPATH.EXPERIMENT_YML,
        f"{node_1st}/figure.html",
        f"{node_1st}/figure.plot-meta.json",
    } <= uploaded
    # post_process's full upload carries the pickle and other large files
    assert f"{node_1st}/func1.pkl" not in uploaded


@pytest.mark.asyncio
async def test_WorkflowResult_uploads_nested_json_output(html_output_run):
    unique_id, local, remote = html_output_run

    await WorkflowResult(workspace_id=workspace_id, unique_id=unique_id).observe(
        [node_1st]
    )

    # The time series is saved as chunk files in its own folder
    chunks = {
        p.relative_to(local).as_posix()
        for p in (local / node_1st / "trace").rglob("*.json")
    }
    uploaded = {p.relative_to(remote).as_posix() for p in remote.rglob("*")}
    assert chunks and chunks <= uploaded


@pytest.mark.asyncio
async def test_WorkflowResult_keeps_sync_status_file_local(html_output_run):
    unique_id, local, remote = html_output_run
    status_file = RemoteSyncStatusFileUtil.REMOTE_SYNC_STATUS_FILE
    (local / node_1st / status_file).write_text("{}")

    await WorkflowResult(workspace_id=workspace_id, unique_id=unique_id).observe(
        [node_1st]
    )

    assert (remote / DIRPATH.EXPERIMENT_YML).exists()
    assert not (remote / status_file).exists()
    assert not (remote / node_1st / status_file).exists()
