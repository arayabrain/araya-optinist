import json
import logging

import numpy as np

from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.rules.runner import Runner
from studio.app.common.core.snakemake.smk_utils import SmkUtils
from studio.app.common.core.utils.filepath_finder import find_condaenv_filepath
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import BehaviorData, FluoData
from studio.app.wrappers import wrapper_dict

ETA_PATH = "optinist/basic_neural_analysis/eta"
OUTPUT_DIR = "default/test_conda_config/eta_node0000"
CONDA_WARNING = "Failed to add conda environment config to NWB file"

ETA_PARAMS = {
    "transpose_x": True,
    "transpose_y": False,
    "event_col_index": 1,
    "trigger_type": "up",
    "trigger_threshold": 0.5,
    "pre_event": -10,
    "post_event": 10,
}


def _eta_inputs():
    rng = np.random.default_rng(0)
    behavior = np.zeros((300, 2))
    for start in range(20, 280, 40):
        behavior[start : start + 5, 1] = 1.0
    return {
        "neural_data": FluoData(rng.random((30, 300)), file_name="fluorescence"),
        "behaviors_data": BehaviorData(behavior, file_name="behavior"),
    }


def _run_eta(caplog):
    caplog.set_level(logging.INFO, logger="optinist")
    output_info = Runner._Runner__execute_function(
        ETA_PATH, dict(ETA_PARAMS), None, OUTPUT_DIR, _eta_inputs()
    )

    # Positive control: without it, the absence assertions below are vacuous.
    assert "start ETA" in caplog.text

    function_id = ExptOutputPathIds(OUTPUT_DIR).function_id
    return output_info["nwbfile"][NWBDATASET.CONFIG][function_id]


def _with_conda_name(monkeypatch, conda_name):
    from studio.app.common.core.rules import runner as runner_module

    group = runner_module.wrapper_dict["optinist"]["basic_neural_analysis"]
    monkeypatch.setitem(group, "eta", {**group["eta"], "conda_name": conda_name})


def test_node_without_conda_name_records_params_without_warning(caplog):
    """eta declares no conda_name; that must not be reported as a failure."""
    config = _run_eta(caplog)

    assert CONDA_WARNING not in caplog.text
    assert json.loads(config["node_params"])["pre_event"] == -10
    assert "conda_config" not in config


def test_node_with_conda_name_still_records_conda_config(caplog, monkeypatch):
    _with_conda_name(monkeypatch, "optinist")

    config = _run_eta(caplog)

    assert CONDA_WARNING not in caplog.text
    assert "dependencies" in json.loads(config["conda_config"])


def _leaf_wrappers(tree):
    for value in tree.values():
        if "function" in value:
            yield value
        else:
            yield from _leaf_wrappers(value)


def test_every_declared_conda_name_resolves_to_an_env_yaml():
    """An unknown name would silently run in the base env and record "{}"."""
    names = {
        w["conda_name"] for w in _leaf_wrappers(wrapper_dict) if w.get("conda_name")
    }

    assert "optinist" in names
    assert [n for n in names if find_condaenv_filepath(n) is None] == []


def test_snakemake_conda_treats_explicit_none_as_no_env(monkeypatch):
    _with_conda_name(monkeypatch, None)

    assert SmkUtils.conda({"type": "eta", "path": ETA_PATH}) is None
