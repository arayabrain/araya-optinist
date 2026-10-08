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
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import numpy as np
import pytest

from studio.app.common.core.rules.runner import Runner
from studio.app.common.core.workflow.workflow import NodeItem
from studio.app.common.core.workflow.workflow_result import WorkflowResult
from studio.app.common.dataclass.timeseries import TimeSeriesData
from studio.app.common.routers.run import run_result
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
