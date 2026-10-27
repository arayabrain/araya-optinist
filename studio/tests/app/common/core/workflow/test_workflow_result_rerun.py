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
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import numpy as np
import pytest
from filelock import Timeout
from snakemake.exceptions import LockException

from studio.app.common.core.experiment import experiment_writer
from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
from studio.app.common.core.experiment.experiment_writer import ExptConfigWriter
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
PARAMS = SmkParam(use_conda=False, cores=1, forceall=False)


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


async def rerun(background_tasks=None):
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
            background_tasks=background_tasks or Mock(),
            remote_bucket_name="",
            current_user=Mock(id=None),
            db=Mock(),
        )


async def scheduled_generation() -> str:
    """Rerun, returning the generation run_id hands to the snakemake child"""
    background_tasks = Mock()
    await rerun(background_tasks)
    (execute, *_, generation), _ = background_tasks.add_task.call_args
    assert execute is snakemake_executor.snakemake_execute
    assert generation == ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).started_at
    return generation


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
        assert (function("func1").success, shape("func1")) == ("success", [10, 3])
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
        assert (function(node).success, shape(node)) == ("success", [10, 5])
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

    assert (function("func1").success, shape("func1")) == ("success", [10, 5])
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
    await rerun()

    assert finalize(cancelled) == {}

    config = ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID)
    assert config.success == "running"
    assert [config.function[n].success for n in NODES] == ["running", "running"]
    assert os.path.exists(WorkflowResult(WORKSPACE_ID, UNIQUE_ID).plan_pending_path)


@pytest.mark.asyncio
async def test_a_child_superseded_by_a_rerun_skips_finalization(previous_run):
    generation = await scheduled_generation()

    def rerun_while_snakemake_runs():
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
            PARAMS,
            generation,
        )

    finalization.assert_not_called()
    assert os.path.exists(WorkflowResult(WORKSPACE_ID, UNIQUE_ID).plan_pending_path)


@pytest.mark.asyncio
async def test_a_same_second_rerun_gets_a_new_generation(previous_run):
    clock = iter(["12:00:00", "12:00:00", "12:00:00", "12:00:01"])
    with patch.object(
        experiment_writer,
        "get_datetime_for_timezone_formatted",
        side_effect=lambda *_: next(clock),
    ):
        await rerun()
        superseded = ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).started_at
        await rerun()

    assert ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).started_at == "12:00:01"
    assert finalize(superseded) == {}


@pytest.mark.asyncio
async def test_a_rerun_during_finalization_skips_registration(previous_run):
    generation = await scheduled_generation()
    observe_overall = WorkflowResult.observe_overall

    async def rerun_while_waiting_for_the_lock(self, generation):
        await rerun()
        return await observe_overall(self, generation)

    dag = Mock()
    with patch.object(
        snakemake_executor, "planned_outputs", return_value=[]
    ), patch.object(snakemake_executor, "_dag", return_value=dag), patch.object(
        WorkflowResult, "observe_overall", rerun_while_waiting_for_the_lock
    ), patch.object(
        snakemake_executor.ExperimentRecordService, "is_available", return_value=True
    ), patch.object(
        snakemake_executor.ExperimentRecordService,
        "regist_record_on_workflow_completed",
    ) as registration, patch.object(
        snakemake_executor.WorkspaceDataCapacityService,
        "update_experiment_data_usage",
    ) as data_usage:
        await asyncio.to_thread(
            snakemake_executor._snakemake_execute_process,
            WORKSPACE_ID,
            UNIQUE_ID,
            PARAMS,
            generation,
        )

    assert ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).started_at != generation
    registration.assert_not_called()
    data_usage.assert_not_called()


@pytest.mark.asyncio
async def test_a_dag_that_fails_to_build_still_finalizes_the_rerun(previous_run):
    generation = await scheduled_generation()
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
            PARAMS,
            generation,
        )

    assert succeeded is False
    finalization.assert_called_once()
    assert "bad dag" in (WDIR / SmkStatusLogger.ERROR_LOG_NAME).read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize("during_planning", [False, True])
async def test_a_child_superseded_before_it_runs_touches_nothing(
    previous_run, during_planning
):
    generation = await scheduled_generation()
    error_log = WDIR / SmkStatusLogger.ERROR_LOG_NAME

    def newer_run_fails():
        asyncio.run(rerun())
        error_log.write_text("newer run failed")

    if not during_planning:
        await asyncio.to_thread(newer_run_fails)

    def plan_the_superseded_run(*_):
        if during_planning:
            newer_run_fails()
        return [str(pkl(node)) for node in NODES + [PP]]

    dag = Mock()
    with patch.object(
        snakemake_executor, "planned_outputs", side_effect=plan_the_superseded_run
    ), patch.object(snakemake_executor, "_dag", return_value=dag), patch.object(
        snakemake_executor, "_observe_overall_with_lock_retry"
    ) as finalization:
        succeeded = await asyncio.to_thread(
            snakemake_executor._snakemake_execute_process,
            WORKSPACE_ID,
            UNIQUE_ID,
            PARAMS,
            generation,
        )

    assert succeeded is False
    dag.execute_workflow.assert_not_called()
    finalization.assert_not_called()
    assert all(pkl(node).exists() for node in NODES)
    assert os.path.exists(WorkflowResult(WORKSPACE_ID, UNIQUE_ID).plan_pending_path)
    assert error_log.read_text() == "newer run failed"


@pytest.mark.asyncio
async def test_a_locked_workdir_keeps_the_previous_results(previous_run):
    generation = await scheduled_generation()
    locks = WDIR / ".snakemake" / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    (locks / "0.output.lock").write_text(str(pkl(NODES[0])))

    dag = Mock()
    dag.execute_workflow.side_effect = LockException()
    with patch.object(
        snakemake_executor,
        "planned_outputs",
        return_value=[str(pkl(node)) for node in NODES + [PP]],
    ), patch.object(snakemake_executor, "_dag", return_value=dag), patch.object(
        snakemake_executor,
        "_observe_overall_with_lock_retry",
        return_value=(False, False, False),
    ):
        succeeded = await asyncio.to_thread(
            snakemake_executor._snakemake_execute_process,
            WORKSPACE_ID,
            UNIQUE_ID,
            PARAMS,
            generation,
        )

    assert succeeded is False
    assert all(pkl(node).exists() for node in NODES)
    assert os.path.exists(WorkflowResult(WORKSPACE_ID, UNIQUE_ID).plan_pending_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("superseded", [False, True])
async def test_a_failed_gate_check_runs_the_workflow_unless_superseded(
    previous_run, superseded
):
    generation = await scheduled_generation()
    if superseded:
        await rerun()

    dag = Mock()
    with patch.object(
        snakemake_executor, "planned_outputs", return_value=[]
    ), patch.object(snakemake_executor, "_dag", return_value=dag), patch.object(
        WorkflowResult, "invalidate_planned_outputs", side_effect=Timeout("lock")
    ), patch.object(
        snakemake_executor, "_observe_overall_with_lock_retry"
    ) as finalization:
        succeeded = await asyncio.to_thread(
            snakemake_executor._snakemake_execute_process,
            WORKSPACE_ID,
            UNIQUE_ID,
            PARAMS,
            generation,
        )

    if superseded:
        assert succeeded is False
        dag.execute_workflow.assert_not_called()
        finalization.assert_not_called()
    else:
        dag.execute_workflow.assert_called_once()
        finalization.assert_called_once()


@pytest.mark.asyncio
async def test_a_cancelled_rerun_holds_the_lock_until_its_runner_is_built(
    previous_run,
):
    started, release = threading.Event(), threading.Event()
    active_workflows, release_threads = [], []

    def release_slot():
        release_threads.append(threading.get_ident())
        active_workflows.pop()

    def slow_runner(*args):
        active_workflows.append(1)
        started.set()
        release.wait(10)
        return Mock(release_unscheduled=Mock(side_effect=release_slot))

    with patch("studio.app.common.routers.run.WorkflowRunner", slow_runner):
        request = asyncio.ensure_future(rerun())
        await asyncio.to_thread(started.wait, 10)
        request.cancel()
        await asyncio.sleep(0.1)

        with pytest.raises(Timeout):
            WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe_lock().acquire()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await request

    assert active_workflows == []
    assert release_threads and threading.get_ident() not in release_threads
    with WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe_lock():
        pass


def test_snakemake_execute_hands_the_generation_to_its_child():
    with patch.object(snakemake_executor, "ProcessPoolExecutor") as pool, patch.object(
        snakemake_executor, "update_user_storage_after_workflow", new=AsyncMock()
    ):
        snakemake_executor.snakemake_execute(WORKSPACE_ID, UNIQUE_ID, PARAMS, None, "g")

    submit = pool.return_value.__enter__.return_value.submit
    assert submit.call_args.kwargs["generation"] == "g"


@pytest.mark.asyncio
async def test_a_rerun_resets_the_config_off_the_event_loop(previous_run):
    threads = []
    add_run_info = ExptConfigWriter.add_run_info

    def record_thread(self, *args):
        threads.append(threading.get_ident())
        return add_run_info(self, *args)

    with patch.object(ExptConfigWriter, "add_run_info", record_thread):
        await rerun()

    assert threads and threading.get_ident() not in threads


def test_a_start_refused_before_the_reset_leaves_no_gate(previous_run):
    with patch.object(ExptConfigWriter, "write", side_effect=RuntimeError):
        with pytest.raises(Exception):
            asyncio.run(rerun())

    assert not os.path.exists(WorkflowResult(WORKSPACE_ID, UNIQUE_ID).plan_pending_path)


def test_a_start_that_fails_after_the_reset_keeps_the_gate(previous_run):
    generation = ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).started_at
    with patch.object(WorkflowRunner, "run_workflow", side_effect=RuntimeError):
        with pytest.raises(Exception):
            asyncio.run(rerun())

    assert ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).started_at != generation
    assert os.path.exists(WorkflowResult(WORKSPACE_ID, UNIQUE_ID).plan_pending_path)


@pytest.mark.asyncio
async def test_a_rerun_cancelled_after_the_reset_keeps_the_gate(previous_run):
    generation = ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).started_at
    started, release = threading.Event(), threading.Event()
    init = WorkflowRunner.__init__

    def slow_init(self, *args):
        started.set()
        release.wait(10)
        init(self, *args)

    with patch.object(WorkflowRunner, "__init__", slow_init):
        request = asyncio.ensure_future(rerun())
        await asyncio.to_thread(started.wait, 10)
        request.cancel()
        await asyncio.sleep(0.1)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await request

    assert ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).started_at != generation
    assert os.path.exists(WorkflowResult(WORKSPACE_ID, UNIQUE_ID).plan_pending_path)


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
