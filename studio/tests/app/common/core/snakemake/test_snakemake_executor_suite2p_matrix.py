"""The suite2p chain on real 1.1.0, one non-default setting at a time: each
variant takes a different path through suite2p (rigid, single-block fallback,
PC metrics, sourcery, denoise, the zero-ROI path, the percentile baseline)."""
import copy
import shutil

import numpy as np
import pytest
import yaml

from studio.app.common.core.auth.auth_dependencies import _get_user_remote_bucket_name
from studio.app.common.core.mode import MODE
from studio.app.common.core.snakemake.smk import SmkParam
from studio.app.common.core.snakemake.snakemake_executor import snakemake_execute
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
    RemoteSyncAction,
    RemoteSyncStatusFileUtil,
)
from studio.app.common.core.utils.pickle_handler import PickleReader
from studio.app.dir_path import DIRPATH

MODE.IS_TEST = True
remote_bucket_name = _get_user_remote_bucket_name()
workspace_id = "default"
fixture_id = "smk_exec_suite2p"
node_labels = [
    "suite2p_file_convert",
    "suite2p_registration",
    "suite2p_roi",
    "suite2p_spike_deconv",
]
smk_param = SmkParam(use_conda=True, cores=2, forceall=True)


def deep_merge(base: dict, overrides: dict) -> dict:
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def assert_saved(saved: dict, want: dict, path: str):
    for key, value in want.items():
        if isinstance(value, dict):
            assert_saved(saved[key], value, f"{path}.{key}")
        else:
            assert np.array_equal(saved[key], value), f"{path}.{key}: {saved[key]}"


# (overrides per node, settings the node must have saved instead, ROIs expected)
variants = [
    pytest.param(
        {"suite2p_registration": {"registration": {"nonrigid": False}}},
        None,
        True,
        id="rigid",
    ),
    pytest.param(
        {"suite2p_registration": {"registration": {"block_size": [128, 128]}}},
        {"suite2p_registration": {"registration": {"nonrigid": False}}},
        True,
        id="single_block_falls_back_to_rigid",
    ),
    pytest.param(
        {"suite2p_registration": {"run": {"do_regmetrics": True}}},
        None,
        True,
        id="do_regmetrics",
    ),
    pytest.param(
        {"suite2p_roi": {"detection": {"algorithm": "sourcery"}}},
        None,
        True,
        id="sourcery",
    ),
    pytest.param(
        {"suite2p_roi": {"detection": {"denoise": True}}}, None, True, id="denoise"
    ),
    pytest.param(
        {"suite2p_roi": {"detection": {"threshold_scaling": 100.0}}},
        None,
        False,
        id="no_rois",
    ),
    pytest.param(
        {"suite2p_spike_deconv": {"dcnv_preprocess": {"baseline": "prctile"}}},
        None,
        True,
        id="baseline_prctile",
    ),
]


@pytest.mark.heavier_processing
@pytest.mark.parametrize("overrides, expected, has_rois", variants)
def test_suite2p_settings_matrix(request, overrides, expected, has_rois):
    MODE.reset_mode(is_standalone=True)
    unique_id = f"{fixture_id}_{request.node.callspec.id}"
    output_dirpath = f"{DIRPATH.OUTPUT_DIR}/{workspace_id}/{unique_id}"
    shutil.rmtree(output_dirpath, ignore_errors=True)
    shutil.copytree(
        f"{DIRPATH.DATA_DIR}/output_test/{workspace_id}/{fixture_id}", output_dirpath
    )

    smk_path = f"{output_dirpath}/snakemake.yaml"
    with open(smk_path) as f:
        text = f.read().replace(f"/{fixture_id}/", f"/{unique_id}/")
    config = yaml.safe_load(text)
    for label, params in overrides.items():
        deep_merge(config["rules"][f"{label}_1234"]["params"], params)
    with open(smk_path, "w") as f:
        yaml.safe_dump(config, f, sort_keys=False)

    if RemoteStorageController.is_available():
        RemoteSyncStatusFileUtil.create_sync_status_file_for_processing(
            remote_bucket_name, workspace_id, unique_id, RemoteSyncAction.UPLOAD
        )

    snakemake_execute(workspace_id, unique_id, smk_param)

    pkls = {}
    for label in node_labels:
        pkl = PickleReader.read(f"{output_dirpath}/{label}_1234/{label}.pkl")
        assert PickleReader.check_is_valid_node_pickle(pkl), f"{label}: {pkl}"
        pkls[label] = pkl

    nframes = pkls["suite2p_file_convert"]["ops"].data["nframes"]
    registration = pkls["suite2p_registration"]["ops"].data
    roi = pkls["suite2p_roi"]["ops"].data
    spks = pkls["suite2p_spike_deconv"]["ops"].data["spks"]

    # every override, or the value a guard replaced it with, is what ran
    for label in ("suite2p_registration", "suite2p_roi"):
        want = deep_merge(
            copy.deepcopy(overrides.get(label, {})), (expected or {}).get(label, {})
        )
        assert_saved(pkls[label]["ops"].data["settings"], want, label)

    regmetrics = registration["settings"]["run"]["do_regmetrics"]
    assert ("regPC" in registration) == (regmetrics and nframes >= 1500)
    assert not np.isnan(registration["yoff"]).any()

    assert (len(roi["stat"]) > 0) == has_rois
    assert roi["F"].shape == (len(roi["stat"]), nframes)
    assert spks.shape == roi["F"].shape
    assert not np.isnan(roi["F"]).any() and not np.isnan(spks).any()
    if has_rois:
        assert np.any(roi["F"]) and np.any(spks)
