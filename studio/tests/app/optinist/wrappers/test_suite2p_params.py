"""The suite2p node yamls mirror suite2p 1.1.0's settings tree, and
merge_settings rejects anything that does not.
"""
import logging

import pytest

from studio.app.common.core.utils.config_handler import ConfigReader
from studio.app.common.core.workflow.workflow_params import (
    dict2nest,
    get_typecheck_params,
    read_default_params,
)
from studio.app.dir_path import DIRPATH
from studio.app.optinist.wrappers.suite2p.utils import merge_settings
from studio.tests.app.optinist.suite2p_settings_1_1_0 import (
    SETTINGS_1_1_0,
    default_settings,
)

SUITE2P_DIR = f"{DIRPATH.ROOT_DIR}/studio/app/optinist/wrappers/suite2p"


def _leaves(tree, prefix=()):
    for key, value in tree.items():
        if isinstance(value, dict):
            yield from _leaves(value, prefix + (key,))
        else:
            # suite2p's tuples arrive from yaml as lists
            yield prefix + (key,), list(value) if isinstance(value, tuple) else value


@pytest.mark.parametrize(
    "node, changed",
    [
        ("suite2p_registration", {("torch_device",), ("run", "do_regmetrics")}),
        ("suite2p_roi", set()),
        ("suite2p_spike_deconv", set()),
    ],
)
def test_shipped_yaml_merges_onto_the_1_1_0_settings_tree(node, changed):
    # the yamls carry suite2p's own defaults except the two deliberate choices
    params = read_default_params(node)
    merged = dict(_leaves(merge_settings(default_settings(), params)))
    defaults = dict(_leaves(SETTINGS_1_1_0))
    assert merged.keys() == defaults.keys()
    assert {path for path in merged if merged[path] != defaults[path]} == changed


def test_the_settings_snapshot_is_the_pinned_suite2p_version():
    with open(f"{SUITE2P_DIR}/conda/suite2p.yaml") as f:
        assert f"suite2p=={SETTINGS_1_1_0['version']}" in f.read()


def test_nested_partial_dict_overrides_only_its_leaves():
    merged = merge_settings(
        default_settings(),
        {"tau": 1.5, "detection": {"sparsery_settings": {"max_ROIs": 7}}},
    )
    assert merged["tau"] == 1.5
    assert merged["detection"]["sparsery_settings"]["max_ROIs"] == 7
    assert merged["detection"]["sparsery_settings"]["spatial_scale"] == 0
    assert merged["detection"]["algorithm"] == "sparsery"


def test_unknown_key_is_refused_by_name():
    with pytest.raises(KeyError, match="nbinned"):
        merge_settings(default_settings(), {"detection": {"nbinned": 5000}})


def test_a_group_parsed_to_none_is_refused():
    with pytest.raises(TypeError, match="detection"):
        merge_settings(default_settings(), {"detection": None})


def test_a_group_landing_on_a_value_is_refused():
    with pytest.raises(TypeError, match="tau"):
        merge_settings(default_settings(), {"tau": {"value": 1.0}})


# the parameter trees of the 0.10.3 yamls, as a saved workflow carries them
OLD_REGISTRATION = {
    "I/O": {"nplanes": 1, "nchannels": 1, "functional_chan": 1, "frames_include": -1},
    "Registration": {"smooth_sigma": 1.15, "smooth_sigma_time": 0, "reg_tif": False},
    "1P setting": {"1Preg": False, "spatial_hp_reg": 42, "spatial_taper": 40},
    "Non-Rigid": {"nonrigid": True, "block_size": [128, 128], "snr_thresh": 1.2},
}
OLD_ROI = {
    "tau": 1.0,
    "classification": {"soma_crop": True},
    "cell_detection": {"high_pass": 100, "sparse_mode": True, "nbinned": 5000},
    "1P": {"spatial_hp_detect": 25},
    "Output": {"preclassify": 0.0},
    "ROI_extraction": {"allow_overlap": False, "min_neuropil_pixels": 350},
}
OLD_ROI_FLAT = {"tau": 1.0, "high_pass": 100, "sparse_mode": True, "nbinned": 5000}
OLD_SPIKE_DECONV = {"neucoeff": 0.7, "baseline": "maximin", "win_baseline": 60.0}


@pytest.mark.parametrize(
    "node, tree",
    [
        ("suite2p_registration", OLD_REGISTRATION),
        ("suite2p_spike_deconv", OLD_SPIKE_DECONV),
    ],
)
def test_a_pre_upgrade_tree_sharing_no_key_with_the_yaml_is_refused(node, tree):
    with pytest.raises(KeyError, match="Workflow yaml error"):
        get_typecheck_params(dict2nest(tree), node)


def test_a_pre_upgrade_roi_tree_is_refused_by_its_old_groups():
    # tau and classification overlap, but 0.10.3 groups stand where 1.1.0 ones go
    with pytest.raises(
        KeyError, match="'1P', 'Output', 'ROI_extraction', 'cell_detection'"
    ):
        get_typecheck_params(dict2nest(OLD_ROI), "suite2p_roi")


def test_a_flat_v1_roi_tree_keeps_tau_and_takes_the_rest_from_the_yaml(caplog):
    # no group, so it reads as a tree saved before the yaml gained its groups
    with caplog.at_level(logging.WARNING):
        params = get_typecheck_params(dict2nest(OLD_ROI_FLAT), "suite2p_roi")
    assert params == {**read_default_params("suite2p_roi"), "tau": 1.0}
    assert "Dropping saved param 'high_pass'" in caplog.text


def test_none_params_leave_the_defaults_alone():
    assert merge_settings(default_settings(), None) == SETTINGS_1_1_0


SAVED_WORKFLOWS = [
    f"{DIRPATH.ROOT_DIR}/sample_data/tutorial/output/tutorial1/workflow.yaml",
    f"{DIRPATH.ROOT_DIR}/sample_data/tutorial/output/tutorial3/workflow.yaml",
    f"{DIRPATH.ROOT_DIR}/sample_data/tutorial/output/tutorial4/workflow.yaml",
    f"{DIRPATH.DATA_DIR}/output_test/default/0123/workflow.yaml",
    f"{DIRPATH.DATA_DIR}/output_test/default/remote_storage_test/workflow.yaml",
]


@pytest.mark.parametrize("path", SAVED_WORKFLOWS, ids=lambda p: p.split("/")[-2])
def test_saved_suite2p_nodes_match_the_shipped_yamls(path, caplog):
    # a stale key would be dropped with a warning, or refused, at run time
    nodes = ConfigReader.read(path)["nodeDict"].values()
    suite2p_nodes = [n for n in nodes if n["data"]["label"].startswith("suite2p_")]
    assert suite2p_nodes

    with caplog.at_level(logging.WARNING):
        for node in suite2p_nodes:
            label = node["data"]["label"]
            params = get_typecheck_params(node["data"]["param"], label)
            assert params == read_default_params(label), label
    assert "Dropping saved param" not in caplog.text
