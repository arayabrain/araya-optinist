"""A rerun's polls see only this run's pickles, and a superseded run writes nothing.

Snakemake deletes a job's old output only when the job starts, so a poll could
save the previous run's pickle as this run's result, and save-once kept it.
Snakemake is simulated here by writing node pickles in the order its scheduler
does; the frontend by polling only the nodes it still holds as pending.
"""

import asyncio
import os
import pickle
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import numpy as np
import pytest

from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
from studio.app.common.core.snakemake import snakemake_executor
from studio.app.common.core.snakemake.smk import SmkParam
from studio.app.common.core.snakemake.smk_status_logger import SmkStatusLogger
from studio.app.common.core.storage.mock_storage_controller import MockStorageController
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
)
from studio.app.common.core.workflow.workflow import Edge, Node, NodeData, RunItem
from studio.app.common.core.workflow.workflow_result import (
    WorkflowMonitor,
    WorkflowResult,
)
from studio.app.common.core.workflow.workflow_runner import WorkflowRunner
from studio.app.common.routers.run import run_id
from studio.app.dir_path import DIRPATH
from studio.app.optinist.dataclass import BehaviorData

WORKSPACE_ID = "777778"
UNIQUE_ID = "rerun-plan"
NODES = ["func1", "func2"]
PP = "post_process_0"
WDIR = Path(DIRPATH.OUTPUT_DIR) / WORKSPACE_ID / UNIQUE_ID


def pkl(node: str) -> Path:
    return WDIR / node / f"{'post_process' if node == PP else node}.pkl"


def write(node: str, value: float = 0, rows: int = 3, error: bool = False):
    path = pkl(node)
    path.parent.mkdir(parents=True, exist_ok=True)
    if error:
        data = ["Traceback: node error"]
    elif node == PP:
        data = {}
    else:
        data = {"behaviors_data": BehaviorData(np.full((rows, 10), float(value)))}
    with open(path, "wb") as f:
        pickle.dump(data, f)


def run_item() -> RunItem:
    nodes = {
        node_id: Node(
            id=node_id,
            type="AlgorithmNode",
            data=NodeData(label=node_id, param={}, path="", type="algorithm"),
            position={"x": 0, "y": 0},
            style={},
        )
        for node_id in NODES
    }
    edge = Edge(
        id="e1",
        source="func1",
        sourceHandle="func1--out",
        target="func2",
        targetHandle="func2--in",
        type="buttonedge",
        animated=False,
        style={},
    )
    return RunItem(nodeDict=nodes, edgeDict={"e1": edge}, forceRunList=[])


async def rerun():
    """A same-uid run with nothing forced, as the frontend starts it"""
    with patch(
        "studio.app.common.routers.run._check_storage_quota", new=AsyncMock()
    ), patch(
        "studio.app.common.routers.run.ensure_structure_caches", new=AsyncMock()
    ), patch(
        "studio.app.common.routers.run.validate_input_edges"
    ), patch(
        "studio.app.common.routers.run.get_current_user_storage_usage",
        new=AsyncMock(),
    ), patch.object(
        WorkflowRunner, "set_smk_config"
    ):
        return await run_id(
            workspace_id=WORKSPACE_ID,
            uid=UNIQUE_ID,
            runItem=run_item(),
            background_tasks=Mock(),
            remote_bucket_name="",
            current_user=Mock(id=None),
            db=Mock(),
        )


def plan(nodes):
    """What the snakemake child does once snakemake has planned its jobs"""
    WorkflowResult(WORKSPACE_ID, UNIQUE_ID).invalidate_planned_outputs(
        [str(pkl(node)) for node in nodes]
    )


class Frontend:
    def __init__(self):
        self.pending = NODES + [PP]

    async def poll(self) -> dict:
        results = await WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe(self.pending)
        self.pending = [n for n in self.pending if n not in results]
        return {node: message.status for node, message in results.items()}


def function(node: str):
    return ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).function[node]


def shape(node: str):
    output_path = function(node).outputPaths["behaviors_data"]
    if isinstance(output_path, dict):
        return output_path["data_shape"]
    return output_path.data_shape


def json_matches_pickle(node: str, scratch: Path) -> bool:
    with open(pkl(node), "rb") as f:
        pickle.load(f)["behaviors_data"].save_json(str(scratch))

    def jsons(root: Path):
        return {p.relative_to(root): p.read_bytes() for p in root.rglob("*.json")}

    fresh = jsons(scratch)
    return bool(fresh) and fresh == jsons(WDIR / node)


def finalize(generation: str = None):
    """The snakemake child's finalization, off this thread's event loop"""
    observe = WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe_overall(generation)
    with ThreadPoolExecutor(1) as executor:
        return executor.submit(asyncio.run, observe).result()


@pytest.fixture()
def previous_run():
    """A finished run whose func1 and func2 held value 0 in 3 rows"""
    shutil.rmtree(WDIR, ignore_errors=True)
    with patch.object(
        RemoteStorageController, "is_available", return_value=False
    ), patch.object(WorkflowMonitor, "search_process", return_value=object()):
        WorkflowRunner("", WORKSPACE_ID, UNIQUE_ID, run_item(), None)
        for node in NODES + [PP]:
            write(node)
        finalize()
        assert (function("func1").success, shape("func1")) == ("success", [3, 10])
        yield
    shutil.rmtree(WDIR.parent, ignore_errors=True)


@pytest.mark.asyncio
async def test_polls_wait_for_the_plan_then_skipped_nodes_succeed(previous_run):
    frontend = Frontend()
    assert await rerun() == UNIQUE_ID

    before_plan = await frontend.poll()
    plan([PP])  # nothing changed, so snakemake reruns only the post process
    after_plan = await frontend.poll()

    assert before_plan == {}
    assert after_plan == {"func1": "success", "func2": "success"}


@pytest.mark.asyncio
async def test_an_unforced_rerun_reports_and_keeps_this_runs_result(
    previous_run, tmp_path
):
    frontend = Frontend()
    await rerun()
    before_plan = await frontend.poll()
    plan(NODES + [PP])
    before_its_turn = await frontend.poll()
    write("func1", 1, rows=5)
    func1_done = await frontend.poll()
    write("func2", 2, rows=5)
    write(PP)
    await frontend.poll()
    finalize()

    assert before_plan == before_its_turn == {}
    assert func1_done == {"func1": "success"}
    for node in NODES:
        assert (function(node).success, shape(node)) == ("success", [5, 10])
        assert json_matches_pickle(node, tmp_path / node)


@pytest.mark.asyncio
async def test_a_cancelled_rerun_leaves_an_unreached_node_in_error(previous_run):
    frontend = Frontend()
    await rerun()
    plan(NODES + [PP])
    write("func1", 1, rows=5)
    await frontend.poll()
    (WDIR / SmkStatusLogger.ERROR_LOG_NAME).write_text("job did not complete\n")
    finalize()

    assert (function("func1").success, shape("func1")) == ("success", [5, 10])
    assert (function("func2").success, function("func2").outputPaths) == (
        "error",
        None,
    )


@pytest.mark.asyncio
async def test_a_downstream_node_that_errors_ends_in_error(previous_run):
    frontend = Frontend()
    await rerun()
    plan(NODES + [PP])
    write("func1", 1, rows=5)
    await frontend.poll()
    write("func2", error=True)
    write(PP, error=True)
    await frontend.poll()
    finalize()

    assert function("func1").success == "success"
    assert function("func2").success == "error"


@pytest.mark.asyncio
async def test_a_cancelled_runs_late_finalization_leaves_the_rerun_alone(
    previous_run,
):
    await rerun()
    plan(NODES + [PP])
    write("func1", 1, rows=5)
    await Frontend().poll()
    cancelled = ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).started_at
    (WDIR / SmkStatusLogger.ERROR_LOG_NAME).write_text("job did not complete\n")
    time.sleep(1.1)  # started_at has second resolution
    await rerun()

    assert finalize(cancelled) == {}

    config = ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID)
    assert config.success == "running"
    assert [config.function[n].success for n in NODES] == ["running", "running"]
    assert os.path.exists(WorkflowResult(WORKSPACE_ID, UNIQUE_ID).plan_pending_path)


@pytest.mark.asyncio
async def test_a_child_superseded_by_a_rerun_skips_finalization(previous_run):
    def rerun_while_snakemake_runs():
        time.sleep(1.1)  # started_at has second resolution
        asyncio.run(rerun())
        raise RuntimeError("job killed")

    dag = Mock()
    dag.execute_workflow.side_effect = rerun_while_snakemake_runs
    with patch.object(
        snakemake_executor, "planned_outputs", return_value=[]
    ), patch.object(snakemake_executor, "_dag", return_value=dag), patch.object(
        snakemake_executor, "_observe_overall_with_lock_retry"
    ) as finalization:
        await asyncio.to_thread(
            snakemake_executor._snakemake_execute_process,
            WORKSPACE_ID,
            UNIQUE_ID,
            SmkParam(use_conda=False, cores=1, forceall=False),
        )

    finalization.assert_not_called()
    assert os.path.exists(WorkflowResult(WORKSPACE_ID, UNIQUE_ID).plan_pending_path)


@pytest.mark.asyncio
async def test_a_dag_that_fails_to_build_still_finalizes_the_rerun(previous_run):
    await rerun()
    with patch.object(
        snakemake_executor, "_dag", side_effect=RuntimeError("bad dag")
    ), patch.object(
        snakemake_executor,
        "_observe_overall_with_lock_retry",
        return_value=(True, False, True),
    ) as finalization:
        succeeded = await asyncio.to_thread(
            snakemake_executor._snakemake_execute_process,
            WORKSPACE_ID,
            UNIQUE_ID,
            SmkParam(use_conda=False, cores=1, forceall=False),
        )

    assert succeeded is False
    finalization.assert_called_once()
    assert "bad dag" in (WDIR / SmkStatusLogger.ERROR_LOG_NAME).read_text()


def test_a_failed_start_leaves_no_gate(previous_run):
    with patch.object(WorkflowRunner, "run_workflow", side_effect=RuntimeError):
        with pytest.raises(Exception):
            asyncio.run(rerun())

    assert not os.path.exists(WorkflowResult(WORKSPACE_ID, UNIQUE_ID).plan_pending_path)


def test_invalidation_resolves_outputs_against_the_workflow_dir(previous_run):
    result = WorkflowResult(WORKSPACE_ID, UNIQUE_ID)
    result.mark_plan_pending()

    result.invalidate_planned_outputs([os.path.relpath(pkl("func1"), WDIR)])

    assert not pkl("func1").exists()
    assert pkl("func2").exists()
    assert not os.path.exists(result.plan_pending_path)


@pytest.mark.asyncio
async def test_a_full_upload_leaves_the_plan_marker_out(previous_run):
    WorkflowResult(WORKSPACE_ID, UNIQUE_ID).mark_plan_pending()
    remote = Path(MockStorageController.MOCK_OUTPUT_DIR) / WORKSPACE_ID / UNIQUE_ID
    try:
        await MockStorageController().upload_experiment(WORKSPACE_ID, UNIQUE_ID)

        assert (remote / DIRPATH.EXPERIMENT_YML).exists()
        assert not list(remote.rglob("plan_pending"))
    finally:
        shutil.rmtree(remote.parent, ignore_errors=True)
