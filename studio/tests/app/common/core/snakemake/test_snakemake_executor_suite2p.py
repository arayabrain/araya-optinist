import os
import shutil

import numpy as np
import pytest

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
from studio.app.dir_path import DIRPATH

# Set test mode before getting bucket name at module level
MODE.IS_TEST = True
remote_bucket_name = _get_user_remote_bucket_name()
workspace_id = "default"
unique_id = "smk_exec_suite2p"

# the fixture's node chain, in run order
node_labels = [
    "suite2p_file_convert",
    "suite2p_registration",
    "suite2p_roi",
    "suite2p_spike_deconv",
]
node_ids = {label: f"{label}_1234" for label in node_labels}

smk_param = SmkParam(
    use_conda=True,
    cores=2,
    forceall=True,
    # forcetargets=True,
    # lock=False,
)

shutil.copytree(
    f"{DIRPATH.DATA_DIR}/output_test/{workspace_id}/{unique_id}",
    f"{DIRPATH.DATA_DIR}/output/{workspace_id}/{unique_id}",
    dirs_exist_ok=True,
)


nodeDict = {
    node_ids[label]: Node(
        id=node_ids[label],
        type="a",
        data=NodeData(label=label, param={}, path="", type=""),
        position={"x": 0, "y": 0},
        style={
            "border": None,
            "borderRadius": 0,
            "height": 100,
            "padding": 0,
            "width": 180,
        },
    )
    for label in node_labels
}

sources = ["input_0"] + [node_ids[label] for label in node_labels[:-1]]
edgeDict = {
    f"edge{i}": Edge(
        id=f"edge{i}",
        source=source,
        animated=False,
        sourceHandle="",
        style={},
        target=node_ids[label],
        targetHandle="",
        type={},
    )
    for i, (source, label) in enumerate(zip(sources, node_labels), start=1)
}

output_dirpath = f"{DIRPATH.OUTPUT_DIR}/{workspace_id}/{unique_id}"


def _pkl_path(label):
    return f"{output_dirpath}/{node_ids[label]}/{label}.pkl"


@pytest.mark.heavier_processing
def test_snakemake_execute(client):
    """
    Test for Run snakemake
    """

    # Force running in standalone-mode
    MODE.reset_mode(is_standalone=True)

    # Write remote storage related files
    if RemoteStorageController.is_available():
        RemoteSyncStatusFileUtil.create_sync_status_file_for_processing(
            remote_bucket_name,
            workspace_id,
            unique_id,
            RemoteSyncAction.UPLOAD,
        )

    # Run snakemake executor
    snakemake_execute(workspace_id, unique_id, smk_param)

    # Every node pickle is written by the suite2p conda env and read here by
    # the host interpreter
    pkls = {}
    for label in node_labels:
        assert os.path.exists(_pkl_path(label)), f"{label} pickle not found"
        pkls[label] = PickleReader.read(_pkl_path(label))
        assert PickleReader.check_is_valid_node_pickle(
            pkls[label]
        ), f"{label}: {pkls[label]}"

    convert = pkls["suite2p_file_convert"]["ops"].data
    registration = pkls["suite2p_registration"]["ops"].data
    roi = pkls["suite2p_roi"]["ops"].data
    spks = pkls["suite2p_spike_deconv"]["spks"].data

    assert registration["settings"]["version"] == "1.1.0"
    for key in ("refImg", "meanImgE", "yrange", "xrange", "badframes"):
        assert key in registration, key
    # the fixture's 64 px blocks on the 128 px movie run non-rigid registration
    assert registration["settings"]["registration"]["nonrigid"] is True
    assert registration["yoff1"].shape[0] == convert["nframes"]
    assert os.path.exists(
        f"{output_dirpath}/{node_ids['suite2p_registration']}"
        "/tiff/mc_images/mc_images.tif"
    )

    # pipeline()'s copies of the roi outputs are not kept beside the pickle
    roi_dir = f"{output_dirpath}/{node_ids['suite2p_roi']}"
    assert not os.path.exists(f"{roi_dir}/suite2p")
    assert not os.path.exists(f"{roi_dir}/F.npy")
    assert len(roi["stat"]) > 0
    assert roi["F"].shape == (len(roi["stat"]), convert["nframes"])
    # the detection images cover the registration's valid frame range
    (y0, y1), (x0, x1) = registration["yrange"], registration["xrange"]
    assert roi["max_proj"].shape == roi["Vcorr"].shape == (y1 - y0, x1 - x0)
    # what classify and the Edit ROI port read from each ROI
    assert {"skew", "std", "overlap", "med", "radius"} <= roi["stat"][0].keys()
    assert spks.shape == roi["F"].shape
    assert np.any(spks != 0)

    # Check for generated snakemake_status_logger log file
    error_log_filepath = f"{output_dirpath}/{SmkStatusLogger.ERROR_LOG_NAME}"
    assert os.path.exists(error_log_filepath)


@pytest.mark.heavier_processing
def test_snakemake_delete_dependencies():
    """
    Test for delete snakemake dependencies
    """

    # Existence check of dependencies files
    # *Files are pre-generated by `test_snakemake_execute`
    for label in node_labels:
        assert os.path.exists(_pkl_path(label)), "Dependencies files not found"

    # Run delete_dependencies
    delete_dependencies(
        workspace_id=workspace_id,
        unique_id=unique_id,
        node_ids=list(node_ids.values()),
        nodeDict=nodeDict,
        edgeDict=edgeDict,
    )

    # Check for deleted dependencies files
    for label in node_labels:
        assert not os.path.exists(_pkl_path(label)), "Dependencies files remain"
