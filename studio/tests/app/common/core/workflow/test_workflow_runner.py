import os
import shutil

import pytest
from fastapi import BackgroundTasks

from studio.app.common.core.auth.auth_dependencies import _get_user_remote_bucket_name
from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
from studio.app.common.core.snakemake.smk import ForceRun
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
    RemoteStorageLockError,
    RemoteSyncLockFileUtil,
)
from studio.app.common.core.utils.filepath_creater import get_pickle_file, join_filepath
from studio.app.common.core.workflow import workflow_runner
from studio.app.common.core.workflow.workflow import (
    Edge,
    Node,
    NodeData,
    RunItem,
    WorkflowRunStatus,
)
from studio.app.common.core.workflow.workflow_params import read_default_params
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


def _stale_run_item():
    # dpca has no "transpose" parameter: the saved tree shares no key with the yaml
    stale = Node(
        id="algo",
        type="AlgorithmNode",
        data=NodeData(
            label="dpca",
            param={"transpose": {"type": "child", "value": False}},
            path="",
            type="",
        ),
        position={"x": 0, "y": 0},
        style={},
    )
    return RunItem(
        name="New Flow",
        nodeDict={"algo": stale},
        edgeDict={},
        snakemakeParam={},
        nwbParam={},
        forceRunList=[],
    )


def test_a_stale_parameter_tree_is_refused_before_any_record_is_written():
    shutil.rmtree(dirpath, ignore_errors=True)

    with pytest.raises(KeyError, match="Workflow yaml error"):
        WorkflowRunner(remote_bucket_name, workspace_id, unique_id, _stale_run_item())

    assert not os.path.exists(dirpath)


def test_a_refused_rerun_leaves_the_existing_record_as_it_was():
    shutil.rmtree(dirpath, ignore_errors=True)
    WorkflowRunner(
        remote_bucket_name, workspace_id, unique_id, _run_item()
    ).finish_workflow_without_run()
    names = ("experiment.yaml", "workflow.yaml")
    before = {n: open(f"{dirpath}/{n}", "rb").read() for n in names}

    with pytest.raises(KeyError, match="Workflow yaml error"):
        WorkflowRunner(remote_bucket_name, workspace_id, unique_id, _stale_run_item())

    assert {n: open(f"{dirpath}/{n}", "rb").read() for n in names} == before
    assert ExptConfigReader.read(workspace_id, unique_id).success == "success"
    shutil.rmtree(dirpath, ignore_errors=True)


def _to_message(params: dict) -> dict:
    return {
        key: {"type": "parent", "children": _to_message(value)}
        if isinstance(value, dict)
        else {"type": "child", "value": value}
        for key, value in params.items()
    }


def _eta_run_item(saved: dict, forced=()):
    node = Node(
        id="eta_node",
        type="AlgorithmNode",
        data=NodeData(label="eta", param=_to_message(saved), path="", type=""),
        position={"x": 0, "y": 0},
        style={},
    )
    return RunItem(
        name="New Flow",
        nodeDict={"eta_node": node},
        edgeDict={},
        snakemakeParam={},
        nwbParam={},
        forceRunList=[ForceRun(nodeId=n, name="eta") for n in forced],
    )


@pytest.fixture
def eta_output(monkeypatch):
    """A RUN on a record whose eta node has an output and no snakemake
    metadata, minus the parts that need snakemake. Yields the output path."""
    monkeypatch.setattr(RemoteStorageController, "is_available", lambda: False)
    monkeypatch.setattr(WorkflowRunner, "set_smk_config", lambda self: None)
    monkeypatch.setattr(workflow_runner, "delete_procs_dependencies", lambda **kw: None)
    shutil.rmtree(dirpath, ignore_errors=True)
    output = join_filepath(
        [
            DIRPATH.OUTPUT_DIR,
            get_pickle_file(workspace_id, unique_id, "eta_node", "eta"),
        ]
    )
    os.makedirs(os.path.dirname(output))
    open(output, "wb").close()
    yield output
    shutil.rmtree(dirpath, ignore_errors=True)


def _rerun(run_item):
    tasks = BackgroundTasks()
    WorkflowRunner(remote_bucket_name, workspace_id, unique_id, run_item).run_workflow(
        tasks
    )
    return [p.nodeId for p in tasks.tasks[0].args[2].forcerun]


def test_a_rerun_runs_a_node_again_when_the_check_adds_keys(eta_output):
    # saved before the yaml gained sort_by_peak; the client forces nothing, so
    # without metadata only the deleted output makes snakemake run it again
    saved = read_default_params("eta")
    del saved["ETA"]["sort_by_peak"]
    assert _rerun(_eta_run_item(saved)) == ["eta_node"]
    assert not os.path.exists(eta_output)


def test_a_rerun_keeps_the_output_of_a_complete_tree(eta_output):
    assert _rerun(_eta_run_item(read_default_params("eta"))) == []
    assert os.path.exists(eta_output)


def test_a_node_the_client_already_forces_is_listed_once(eta_output):
    saved = read_default_params("eta")
    del saved["ETA"]["sort_by_peak"]
    assert _rerun(_eta_run_item(saved, forced=["eta_node"])) == ["eta_node"]
