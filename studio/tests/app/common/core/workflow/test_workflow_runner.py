import os
import shutil

import pytest
from fastapi import BackgroundTasks

from studio.app.common.core.auth.auth_dependencies import _get_user_remote_bucket_name
from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
    RemoteStorageLockError,
    RemoteSyncLockFileUtil,
)
from studio.app.common.core.workflow import workflow_runner
from studio.app.common.core.workflow.workflow import (
    Edge,
    Node,
    NodeData,
    RunItem,
    WorkflowRunStatus,
)
from studio.app.common.core.workflow.workflow_runner import WorkflowRunner
from studio.app.dir_path import DIRPATH

remote_bucket_name = _get_user_remote_bucket_name()
workspace_id = "default"
unique_id = "workflow_test"

node_data = NodeData(label="a", param={}, path="", type="")

nodeDict = {
    "test1": Node(
        id="node_id",
        type="a",
        data=node_data,
        position={"x": 0, "y": 0},
        style={
            "border": None,
            "borderRadius": 0,
            "height": 100,
            "padding": 0,
            "width": 180,
        },
    )
}

edgeDict = {
    "test2": Edge(
        id="edge_id",
        type="a",
        animated=False,
        source="",
        sourceHandle="",
        target="",
        targetHandle="",
        style={},
    )
}


dirpath = f"{DIRPATH.OUTPUT_DIR}/{workspace_id}/{unique_id}"


def test_finish_workflow_without_run():
    if os.path.exists(dirpath):
        shutil.rmtree(dirpath)

    runItem = RunItem(
        name="New Flow",
        nodeDict=nodeDict,
        edgeDict=edgeDict,
        snakemakeParam={},
        nwbParam={},
        forceRunList=[],
    )

    WorkflowRunner(
        remote_bucket_name, workspace_id, unique_id, runItem
    ).finish_workflow_without_run()

    assert os.path.exists(f"{dirpath}/experiment.yaml")
    assert os.path.exists(f"{dirpath}/workflow.yaml")

    exp_config = ExptConfigReader.read(workspace_id, unique_id)

    assert exp_config.success == WorkflowRunStatus.SUCCESS.value


def _run_item():
    return RunItem(
        name="New Flow",
        nodeDict=nodeDict,
        edgeDict=edgeDict,
        snakemakeParam={},
        nwbParam={},
        forceRunList=[],
    )


@pytest.fixture
def remote_run(monkeypatch):
    """A RUN with remote storage on, minus the parts that need snakemake.
    Yields the runner and the list of delete_procs_dependencies calls."""
    deleted = []
    monkeypatch.setattr(RemoteStorageController, "is_available", lambda: True)
    monkeypatch.setattr(WorkflowRunner, "set_smk_config", lambda self: None)
    monkeypatch.setattr(workflow_runner, "snakemake_execute", lambda *args: None)
    monkeypatch.setattr(
        workflow_runner, "delete_procs_dependencies", lambda **kw: deleted.append(kw)
    )
    shutil.rmtree(dirpath, ignore_errors=True)
    yield WorkflowRunner(
        remote_bucket_name, workspace_id, unique_id, _run_item()
    ), deleted
    shutil.rmtree(dirpath, ignore_errors=True)


def test_a_run_that_loses_the_lock_race_deletes_nothing(remote_run, monkeypatch):
    runner, deleted = remote_run
    is_locked = RemoteSyncLockFileUtil.check_sync_lock_file
    # Another worker's lock lands between this request's check and its create
    monkeypatch.setattr(
        RemoteSyncLockFileUtil, "check_sync_lock_file", lambda *a, **kw: False
    )
    RemoteSyncLockFileUtil.create_sync_lock_file(workspace_id, unique_id)

    with pytest.raises(RemoteStorageLockError):
        runner.run_workflow(BackgroundTasks())

    assert deleted == []
    assert is_locked(workspace_id, unique_id)


def test_a_run_that_fails_before_snakemake_releases_its_lock(remote_run, monkeypatch):
    runner, _ = remote_run

    def explode(**kw):
        raise RuntimeError("delete failed")

    monkeypatch.setattr(workflow_runner, "delete_procs_dependencies", explode)

    with pytest.raises(RuntimeError):
        runner.run_workflow(BackgroundTasks())

    assert not RemoteSyncLockFileUtil.check_sync_lock_file(workspace_id, unique_id)
