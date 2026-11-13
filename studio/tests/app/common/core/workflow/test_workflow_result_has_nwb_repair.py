"""A finished record that latched hasNWB false is repaired by the next poll.

`observe()` returns early once every node is finished, so a record whose poll
ran between the last node's pickle and `whole.nwb` kept `hasNWB: false` for
good. The early return now re-checks the file when the flag is false.
"""

import shutil
from pathlib import Path

import pytest

from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
from studio.app.common.core.workflow.workflow_result import WorkflowResult
from studio.app.dir_path import DIRPATH

WORKSPACE_ID = "888888"
UNIQUE_ID = "latched-run"
FIXTURE = Path(DIRPATH.DATA_DIR) / "output_test" / "default" / "result_test"


@pytest.fixture()
def finished_run():
    directory = Path(DIRPATH.OUTPUT_DIR) / WORKSPACE_ID / UNIQUE_ID
    shutil.copytree(FIXTURE, directory, dirs_exist_ok=True)
    yml = directory / DIRPATH.EXPERIMENT_YML
    yml.write_text(yml.read_text().replace("success: running", "success: success"))
    assert ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).hasNWB is False
    yield directory
    shutil.rmtree(directory.parent)


@pytest.mark.asyncio
async def test_a_poll_after_whole_nwb_landed_sets_has_nwb(finished_run):
    (finished_run / "whole.nwb").touch()

    await WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe(["func1", "func2"])

    assert ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).hasNWB is True


@pytest.mark.asyncio
async def test_a_poll_without_whole_nwb_leaves_the_flag_false(finished_run):
    await WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe(["func1", "func2"])

    assert ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).hasNWB is False
