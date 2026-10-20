"""A rerun deletes the pickles snakemake plans to replace before any poll reads
them, so a snakemake upgrade must keep its dry run naming them."""

import os
import pickle
import shutil
import time
from pathlib import Path

import pytest

from studio.app.common.core.snakemake.smk import SmkParam
from studio.app.common.core.snakemake.snakemake_executor import planned_outputs
from studio.app.common.core.workflow.workflow_result import WorkflowResult
from studio.app.dir_path import DIRPATH

WORKSPACE_ID = "default"
UNIQUE_ID = "smk_plan"
FIXTURE = Path(DIRPATH.DATA_DIR) / "output_test" / "default" / "smk_exec_lccd"
OUTPUTS = [
    "input_0/data_endoscope.pkl",
    "lccd_cell_detection_1234/lccd_cell_detection.pkl",
    "pca_1234/pca.pkl",
]
PARAMS = SmkParam(use_conda=False, cores=1, forceall=False)


@pytest.fixture()
def workflow():
    """The lccd workflow under its own uid, every output newer than its input"""
    workdir = Path(DIRPATH.OUTPUT_DIR) / WORKSPACE_ID / UNIQUE_ID
    shutil.copytree(FIXTURE, workdir, dirs_exist_ok=True)
    config = workdir / "snakemake.yaml"
    config.write_text(
        config.read_text().replace(
            "default/smk_exec_lccd/", f"{WORKSPACE_ID}/{UNIQUE_ID}/"
        )
    )
    tif = Path(DIRPATH.INPUT_DIR) / "1" / "test.tif"
    tif_stat = tif.stat()
    past = time.time() - 100
    os.utime(tif, (past, past))
    for i, output in enumerate(OUTPUTS):
        path = workdir / output
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({}, f)
        os.utime(path, (past + 10 + i, past + 10 + i))
    yield workdir, tif
    os.utime(tif, ns=(tif_stat.st_atime_ns, tif_stat.st_mtime_ns))
    shutil.rmtree(workdir)


def test_the_dry_run_names_the_pickles_an_input_change_replaces(workflow):
    workdir, tif = workflow
    assert planned_outputs(str(workdir), PARAMS) == []

    os.utime(tif, None)
    planned = planned_outputs(str(workdir), PARAMS)
    assert sorted(os.path.relpath(workdir / p, workdir) for p in planned) == OUTPUTS

    result = WorkflowResult(WORKSPACE_ID, UNIQUE_ID)
    result.mark_plan_pending()
    result.invalidate_planned_outputs(planned)
    assert not any((workdir / output).exists() for output in OUTPUTS)
    assert not os.path.exists(result.plan_pending_path)
