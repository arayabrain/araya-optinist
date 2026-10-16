"""A finished node's outputs are saved as JSON once, outside the event loop.

Each `/run/result` poll used to rewrite every pending node's JSON, twice while
other nodes still ran, inside the async handler, so one large output blocked
every other request and client retries queued more rewrites behind it.
"""

import asyncio
import pickle
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import numpy as np
import pytest
from fastapi import HTTPException
from filelock import Timeout

from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
from studio.app.common.core.rules.runner import Runner
from studio.app.common.core.snakemake.smk import ForceRun
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
)
from studio.app.common.core.utils.pickle_handler import PickleReader, PickleWriter
from studio.app.common.core.workflow.workflow import (
    Message,
    Node,
    NodeData,
    NodeItem,
    RunItem,
)
from studio.app.common.core.workflow.workflow_result import (
    WorkflowMonitor,
    WorkflowResult,
    _save_slot,
)
from studio.app.common.core.workflow.workflow_runner import WorkflowRunner
from studio.app.common.dataclass.timeseries import TimeSeriesData
from studio.app.common.routers.run import run_id, run_result
from studio.app.dir_path import DIRPATH
from studio.app.optinist.dataclass import BehaviorData

WORKSPACE_ID = "777777"
UNIQUE_ID = "save-once"
NODES = ["func1", "func2"]  # func2 has no pickle yet, so the run is ongoing
FIXTURE = Path(DIRPATH.DATA_DIR) / "output_test" / "default" / "result_test"
save_json = TimeSeriesData.save_json


@pytest.fixture()
def finished_node():
    directory = Path(DIRPATH.OUTPUT_DIR) / WORKSPACE_ID / UNIQUE_ID
    shutil.copytree(FIXTURE, directory, dirs_exist_ok=True)
    yml = directory / DIRPATH.EXPERIMENT_YML
    yml.write_text(yml.read_text().replace("success: success", "success: running"))
    with open(directory / "func1" / "func1.pkl", "wb") as f:
        pickle.dump({"behaviors_data": BehaviorData(np.zeros((3, 10)))}, f)
    Runner.write_pid_file(str(directory), "dummy_func", "dummy_script.py")
    yield directory
    shutil.rmtree(directory.parent)


def poll():
    return run_result(
        workspace_id=WORKSPACE_ID,
        uid=UNIQUE_ID,
        nodeDict=NodeItem(pendingNodeIdList=NODES),
        background_tasks=Mock(),
        remote_bucket_name="",
        db=Mock(),
    )


def rerun():
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
    return run_id(
        workspace_id=WORKSPACE_ID,
        uid=UNIQUE_ID,
        runItem=RunItem(
            nodeDict=nodes, forceRunList=[ForceRun(nodeId="func1", name="func1")]
        ),
        background_tasks=Mock(),
        remote_bucket_name="",
        current_user=Mock(id=None),
        db=Mock(),
    )


@pytest.mark.asyncio
async def test_repeated_observes_save_a_finished_node_once(finished_node):
    saved = finished_node / "func1" / "behavior"

    first = await WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe(NODES)
    assert saved.is_dir()
    shutil.rmtree(saved)
    second = await WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe(NODES)

    assert not saved.exists()
    assert second["func1"] == first["func1"]


@pytest.mark.asyncio
async def test_a_finished_node_without_outputs_is_saved_once(finished_node):
    with open(finished_node / "func1" / "func1.pkl", "wb") as f:
        pickle.dump({}, f)
    executor = Mock(wraps=ThreadPoolExecutor)

    with patch(
        "studio.app.common.core.workflow.workflow_result.ProcessPoolExecutor",
        executor,
    ):
        first = await WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe(NODES)
        second = await WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe(NODES)

    assert first["func1"].outputPaths == {}
    assert second["func1"] == first["func1"]
    assert executor.call_count == 1


@pytest.mark.asyncio
async def test_a_saved_node_is_answered_without_unpickling(finished_node):
    first = await WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe(NODES)
    executor = Mock(wraps=ThreadPoolExecutor)

    with patch.object(PickleReader, "read", wraps=PickleReader.read) as read, patch(
        "studio.app.common.core.workflow.workflow_result.ProcessPoolExecutor",
        executor,
    ):
        second = await WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe(NODES)

    assert second["func1"] == first["func1"]
    assert read.call_count == 0
    assert executor.call_count == 0


@pytest.mark.asyncio
async def test_a_node_to_save_is_unpickled_once(finished_node):
    with patch.object(PickleReader, "read", wraps=PickleReader.read) as read, patch(
        "studio.app.common.core.workflow.workflow_result.ProcessPoolExecutor",
        ThreadPoolExecutor,
    ):
        results = await WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe(NODES)

    assert results["func1"].status == "success"
    assert read.call_count == 1


@pytest.mark.asyncio
async def test_a_torn_pickle_is_not_ready_until_it_reads(finished_node):
    pkl = finished_node / "func1" / "func1.pkl"
    whole = pkl.read_bytes()
    pkl.write_bytes(whole[: len(whole) // 2])

    torn = await WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe(NODES)

    func1 = ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).function["func1"]
    assert "func1" not in torn
    assert (func1.success, func1.outputPaths) == ("running", None)

    pkl.write_bytes(whole)
    whole_read = await WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe(NODES)
    assert whole_read["func1"].status == "success"


@pytest.mark.asyncio
async def test_an_error_pickle_is_reported_on_a_poll_and_at_finalization(
    finished_node,
):
    pkl = str(finished_node / "func1" / "func1.pkl")
    PickleWriter.write_error(pkl, ValueError("boom"))
    error = Message(status="error", message="\n".join(PickleReader.read(pkl)))

    polled = await WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe(NODES)
    finalized = await WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe_overall()

    func1 = ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).function["func1"]
    assert polled["func1"] == finalized["func1"] == error
    assert (func1.success, func1.message) == ("error", error.message)


@pytest.mark.asyncio
async def test_a_poll_overlapping_a_slow_save_answers_at_once(finished_node):
    def slow_save(self, json_dir):
        time.sleep(1)
        save_json(self, json_dir)

    with patch.object(
        TimeSeriesData, "save_json", autospec=True, side_effect=slow_save
    ) as save, patch(
        # In-process, so the mock sees every save
        "studio.app.common.core.workflow.workflow_result.ProcessPoolExecutor",
        ThreadPoolExecutor,
    ), patch(
        "studio.app.common.routers.run.ExptConfigReader.ensure_synced_async",
        new=AsyncMock(),
    ), patch(
        "studio.app.common.routers.run.ExperimentRecordService.is_available",
        return_value=False,
    ):
        first = asyncio.create_task(poll())
        await asyncio.sleep(0.2)

        started = time.monotonic()
        overlapping = await poll()
        assert time.monotonic() - started < 0.5
        assert overlapping.nodeResults == {}

        assert "func1" in (await first).nodeResults
        assert "func1" in (await poll()).nodeResults

    assert save.call_count == 1


@pytest.mark.asyncio
async def test_a_cancelled_poll_frees_the_loop_and_holds_its_lock(finished_node):
    started, finish = threading.Event(), threading.Event()

    def slow_save(self, json_dir):
        started.set()
        finish.wait(3)
        save_json(self, json_dir)

    with patch.object(
        TimeSeriesData, "save_json", autospec=True, side_effect=slow_save
    ), patch(
        "studio.app.common.core.workflow.workflow_result.ProcessPoolExecutor",
        ThreadPoolExecutor,
    ), patch(
        "studio.app.common.routers.run.ExptConfigReader.ensure_synced_async",
        new=AsyncMock(),
    ), patch(
        "studio.app.common.routers.run.ExperimentRecordService.is_available",
        return_value=False,
    ):
        task = asyncio.create_task(poll())
        try:
            assert await asyncio.to_thread(started.wait, 10)

            for _ in range(2):
                task.cancel()
                began = time.monotonic()
                await asyncio.sleep(0.1)
                assert time.monotonic() - began < 0.5
                assert not task.done()
                with pytest.raises(Timeout):
                    WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe_lock().acquire()
        finally:
            finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    with WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe_lock():
        assert (finished_node / "func1" / "behavior").is_dir()


@pytest.mark.asyncio
async def test_a_failed_save_answers_500_and_the_next_poll_retries(finished_node):
    with patch(
        "studio.app.common.routers.run.ExptConfigReader.ensure_synced_async",
        new=AsyncMock(),
    ), patch(
        "studio.app.common.routers.run.ExperimentRecordService.is_available",
        return_value=False,
    ):
        with patch.object(
            TimeSeriesData, "save_json", side_effect=BrokenProcessPool("worker died")
        ), patch(
            "studio.app.common.core.workflow.workflow_result.ProcessPoolExecutor",
            ThreadPoolExecutor,
        ):
            with pytest.raises(HTTPException) as failed:
                await poll()
        assert failed.value.status_code == 500

        func1 = ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).function["func1"]
        assert (func1.success, func1.outputPaths) == ("running", None)
        with WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe_lock():
            pass

        assert "func1" in (await poll()).nodeResults


@pytest.mark.asyncio
async def test_a_rerun_mid_save_is_refused_so_the_save_cannot_land_in_it(
    finished_node,
):
    started, finish = threading.Event(), threading.Event()

    def slow_save(self, json_dir):
        started.set()
        finish.wait(3)
        save_json(self, json_dir)

    with patch.object(
        TimeSeriesData, "save_json", autospec=True, side_effect=slow_save
    ), patch(
        "studio.app.common.core.workflow.workflow_result.ProcessPoolExecutor",
        ThreadPoolExecutor,
    ), patch(
        "studio.app.common.routers.run.ExptConfigReader.ensure_synced_async",
        new=AsyncMock(),
    ), patch(
        "studio.app.common.routers.run.ExperimentRecordService.is_available",
        return_value=False,
    ), patch(
        "studio.app.common.routers.run._check_storage_quota", new=AsyncMock()
    ), patch(
        "studio.app.common.routers.run.ensure_structure_caches", new=AsyncMock()
    ), patch(
        "studio.app.common.routers.run.validate_input_edges"
    ), patch(
        "studio.app.common.routers.run.get_current_user_storage_usage",
        new=AsyncMock(),
    ), patch.object(
        RemoteStorageController, "is_available", return_value=False
    ), patch.object(
        WorkflowRunner, "set_smk_config"
    ):
        task = asyncio.create_task(poll())
        try:
            assert await asyncio.to_thread(started.wait, 10)
            with pytest.raises(HTTPException) as refused:
                await rerun()
        finally:
            finish.set()
            await task
        assert refused.value.status_code == 423

        assert await rerun() == UNIQUE_ID

    func1 = ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).function["func1"]
    assert (func1.success, func1.outputPaths) == ("running", None)
    assert not (finished_node / "func1" / "func1.pkl").exists()


@pytest.mark.asyncio
async def test_finalization_waits_for_a_poll_mid_save(finished_node):
    held = threading.Event()
    saved_while_held = []

    def poll_mid_save():
        with WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe_lock():
            held.set()
            time.sleep(1.5)
            saved_while_held.append((finished_node / "func1" / "behavior").exists())

    poller = threading.Thread(target=poll_mid_save)
    poller.start()
    held.wait()
    results = await WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe_overall()
    poller.join()

    assert saved_while_held == [False]
    assert "func1" in results


@pytest.mark.asyncio
async def test_a_poll_leaves_a_node_to_the_next_poll_while_another_save_runs(
    finished_node,
):
    executor = Mock(wraps=ThreadPoolExecutor)

    with patch(
        "studio.app.common.core.workflow.workflow_result.ProcessPoolExecutor",
        executor,
    ):
        with _save_slot(0):
            busy = await WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe(NODES)
        free = await WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe(NODES)

    assert "func1" not in busy
    assert free["func1"].status == "success"
    assert executor.call_count == 1


@pytest.mark.asyncio
async def test_finalization_waits_for_the_save_slot_rather_than_save_beside_it(
    finished_node,
):
    held, release = threading.Event(), threading.Event()
    saved_while_held = []

    def hold_slot():
        with _save_slot(0):
            held.set()
            release.wait(5)
            saved_while_held.append((finished_node / "func1" / "behavior").exists())

    holder = threading.Thread(target=hold_slot)
    holder.start()
    held.wait()
    threading.Timer(0.3, release.set).start()
    with patch.object(WorkflowResult, "OBSERVE_LOCK_WAIT_SECONDS", 0.1), patch(
        "studio.app.common.core.workflow.workflow_result.logger"
    ) as log:
        results = await WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe_overall()
    holder.join()

    assert saved_while_held == [False]
    assert results["func1"].status == "success"
    assert any(
        "Save slot still held" in call.args[0] for call in log.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_a_deferred_save_is_not_recorded_as_a_dead_process(finished_node):
    # Only func1, whose pickle is complete: func2 really would be a dead run's gap
    with patch.object(WorkflowMonitor, "search_process", return_value=None):
        with _save_slot(0):
            await WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe(["func1"])
        deferred = ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).function["func1"]
        await WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe(["func1"])
    saved = ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).function["func1"]

    assert deferred.success == "running"
    assert saved.success == "success"


@pytest.mark.asyncio
async def test_finalization_past_the_lock_wait_still_saves(finished_node):
    with WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe_lock(), patch.object(
        WorkflowResult, "OBSERVE_LOCK_WAIT_SECONDS", 0.1
    ), patch("studio.app.common.core.workflow.workflow_result.logger") as log:
        results = await WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe_overall()

    func1 = ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).function["func1"]
    assert func1.success == "success"
    assert func1.outputPaths == results["func1"].outputPaths
    assert (finished_node / "func1" / "behavior").is_dir()
    assert any(
        "Observe lock still held" in call.args[0] for call in log.warning.call_args_list
    )
