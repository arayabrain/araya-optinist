import os
import shutil

import numpy as np
import pytest
import yaml

from studio.app.common.core.auth.auth_dependencies import _get_user_remote_bucket_name
from studio.app.common.core.mode import MODE
from studio.app.common.core.snakemake.smk import SmkParam
from studio.app.common.core.snakemake.smk_status_logger import SmkStatusLogger
from studio.app.common.core.snakemake.snakemake_executor import snakemake_execute
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
    RemoteSyncAction,
    RemoteSyncStatusFileUtil,
)
from studio.app.common.core.utils.pickle_handler import PickleReader
from studio.app.common.core.workflow.workflow import Edge, Node, NodeData
from studio.app.common.core.workflow.workflow_dependencies import delete_dependencies
from studio.app.dir_path import CORE_PARAM_PATH, DIRPATH

# The per-PR snakemake run: real rules and a real conda env, on a tiny table
MODE.IS_TEST = True
workspace_id = "default"
unique_id = "smk_exec_data_utils"
input_relpath = f"{unique_id}/table.csv"
run_relpath = f"{workspace_id}/{unique_id}"
output_dirpath = f"{DIRPATH.OUTPUT_DIR}/{run_relpath}"

NODE_IDS = ["transpose_1", "transpose_2"]
TABLE = np.arange(12, dtype=float).reshape(3, 4)


def node(node_id, label):
    return Node(
        id=node_id,
        type="a",
        data=NodeData(label=label, param={}, path="", type=""),
        position={"x": 0, "y": 0},
        style={},
    )


def edge(source, target):
    return Edge(
        id=f"{source}-{target}",
        source=source,
        animated=False,
        sourceHandle="",
        style={},
        target=target,
        targetHandle="",
        type={},
    )


def transpose_rule(node_id, upstream_pkl, upstream_key):
    return {
        "input": [f"{run_relpath}/{upstream_pkl}"],
        "return_arg": {upstream_key: "data"},
        "params": {"transpose_dims": []},
        "output": f"{run_relpath}/{node_id}/data_transpose.pkl",
        "type": "data_transpose",
        "nwbfile": None,
        "hdf5Path": None,
        "matPath": None,
        "path": "utils/data_transpose",
    }


@pytest.fixture
def workflow():
    with open(CORE_PARAM_PATH.nwb.value) as f:
        nwb_template = yaml.safe_load(f)

    os.makedirs(f"{DIRPATH.INPUT_DIR}/{unique_id}", exist_ok=True)
    np.savetxt(f"{DIRPATH.INPUT_DIR}/{input_relpath}", TABLE, delimiter=",")

    rules = {
        "input_0": {
            "input": input_relpath,
            "return_arg": "input_0",
            "params": {"setHeader": None, "transpose": False},
            "output": f"{run_relpath}/input_0/table.pkl",
            "type": "csv",
            "nwbfile": nwb_template,
            "hdf5Path": None,
            "matPath": None,
            "path": None,
        },
        "transpose_1": transpose_rule("transpose_1", "input_0/table.pkl", "input_0"),
        "transpose_2": transpose_rule(
            "transpose_2", "transpose_1/data_transpose.pkl", "transposed_data"
        ),
    }
    function = {
        node_id: {
            "unique_id": node_id,
            "name": node_id,
            "success": "running",
            "hasNWB": False,
            "message": None,
            "outputPaths": None,
            "started_at": None,
            "finished_at": None,
        }
        for node_id in rules
    }

    os.makedirs(output_dirpath, exist_ok=True)
    with open(f"{output_dirpath}/{DIRPATH.SNAKEMAKE_CONFIG_YML}", "w") as f:
        yaml.dump({"rules": rules, "last_output": [rules["transpose_2"]["output"]]}, f)
    with open(f"{output_dirpath}/{DIRPATH.EXPERIMENT_YML}", "w") as f:
        yaml.dump(
            {
                "workspace_id": workspace_id,
                "unique_id": unique_id,
                "name": "data utils run",
                "started_at": "2026-01-01 00:00:00",
                "finished_at": None,
                "success": "running",
                "hasNWB": False,
                "function": function,
                "nwb": nwb_template,
                "snakemake": {"use_conda": True, "cores": 2, "forceall": True},
            },
            f,
        )

    yield

    shutil.rmtree(output_dirpath, ignore_errors=True)
    shutil.rmtree(f"{DIRPATH.INPUT_DIR}/{unique_id}", ignore_errors=True)


@pytest.mark.lighter_processing
def test_snakemake_runs_a_two_node_workflow_in_its_conda_env(client, workflow):
    MODE.reset_mode(is_standalone=True)

    if RemoteStorageController.is_available():
        RemoteSyncStatusFileUtil.create_sync_status_file_for_processing(
            _get_user_remote_bucket_name(),
            workspace_id,
            unique_id,
            RemoteSyncAction.UPLOAD,
        )

    snakemake_execute(
        workspace_id, unique_id, SmkParam(use_conda=True, cores=2, forceall=True)
    )

    pickles = {
        node_id: f"{output_dirpath}/{node_id}/data_transpose.pkl"
        for node_id in NODE_IDS
    }
    for path in pickles.values():
        assert os.path.exists(path), f"{path} was not written"
        assert PickleReader.check_is_valid_node_pickle(PickleReader.read(path))

    once = PickleReader.read(pickles["transpose_1"])["transposed_data"].data
    twice = PickleReader.read(pickles["transpose_2"])["transposed_data"].data
    np.testing.assert_array_equal(once, TABLE.T)
    np.testing.assert_array_equal(twice, TABLE)

    assert os.path.exists(f"{output_dirpath}/{SmkStatusLogger.ERROR_LOG_NAME}")

    # Deleting the first node also deletes the node downstream of it
    delete_dependencies(
        workspace_id=workspace_id,
        unique_id=unique_id,
        node_ids=["transpose_1"],
        nodeDict={
            "transpose_1": node("transpose_1", "data_transpose"),
            "transpose_2": node("transpose_2", "data_transpose"),
        },
        edgeDict={"e": edge("transpose_1", "transpose_2")},
    )
    for path in pickles.values():
        assert not os.path.exists(path), f"{path} was not deleted"
