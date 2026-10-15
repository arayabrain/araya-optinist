"""suite2p_registration: its batch_size sizes registration only, and the PC
metrics are opt-in. suite2p is stubbed; the parameters are the shipped yaml.
"""
import copy
import sys
import types

import numpy as np
import pytest

from studio.app.common.core.workflow.workflow_params import read_default_params
from studio.app.optinist.dataclass import Suite2pData
from studio.app.optinist.wrappers.suite2p.registration import suite2p_registration

H, W = 8, 8
INPUT_BATCH_SIZE = 500


@pytest.fixture
def output_dir(tmp_path):
    d = tmp_path / "output" / "default" / "uid" / "func"
    d.mkdir(parents=True)
    return str(d)


@pytest.fixture
def suite2p_stub(monkeypatch):
    calls = {"register_batch_size": None, "pc_metrics": 0}

    def register_binary(ops, refImg=None):
        calls["register_batch_size"] = ops["batch_size"]
        return {**ops, "refImg": refImg, "meanImgE": refImg}

    def get_pc_metrics(ops):
        calls["pc_metrics"] += 1
        return ops

    class BinaryFile:
        def __init__(self, Lx, Ly, read_filename):
            self.data = np.zeros((6, Ly, Lx), np.int16)

    fake = types.ModuleType("suite2p")
    fake.default_ops = lambda: {"batch_size": 500, "do_regmetrics": True}
    fake.io = types.SimpleNamespace(BinaryFile=BinaryFile)
    fake.registration = types.SimpleNamespace(
        register_binary=register_binary, get_pc_metrics=get_pc_metrics
    )
    monkeypatch.setitem(sys.modules, "suite2p", fake)
    return calls


def _input_ops(nframes=2000, batch_size=INPUT_BATCH_SIZE):
    ops = {
        "meanImg": np.ones((H, W), np.float32),
        "nframes": nframes,
        "Ly": H,
        "Lx": W,
        "reg_file": "data.bin",
    }
    if batch_size is not None:
        ops["batch_size"] = batch_size
    return Suite2pData(ops)


def test_registers_with_its_own_batch_size_and_passes_the_input_one_on(
    output_dir, suite2p_stub
):
    params = read_default_params("suite2p_registration")
    node_batch_size = params["Registration"]["batch_size"]
    info = suite2p_registration(_input_ops(), output_dir, params=params)

    assert node_batch_size != INPUT_BATCH_SIZE
    assert suite2p_stub["register_batch_size"] == node_batch_size
    assert info["ops"].data["reg_batch_size"] == node_batch_size
    assert info["ops"].data["batch_size"] == INPUT_BATCH_SIZE


def test_input_without_batch_size_passes_suite2p_default_on(output_dir, suite2p_stub):
    params = read_default_params("suite2p_registration")
    info = suite2p_registration(_input_ops(batch_size=None), output_dir, params=params)

    assert suite2p_stub["register_batch_size"] == params["Registration"]["batch_size"]
    assert info["ops"].data["batch_size"] == 500


def test_temporal_smoothing_registers_with_the_input_batch_size(
    output_dir, suite2p_stub
):
    params = copy.deepcopy(read_default_params("suite2p_registration"))
    params["Registration"]["smooth_sigma_time"] = 2
    info = suite2p_registration(_input_ops(), output_dir, params=params)

    assert suite2p_stub["register_batch_size"] == INPUT_BATCH_SIZE
    assert info["ops"].data["reg_batch_size"] == INPUT_BATCH_SIZE
    assert info["ops"].data["batch_size"] == INPUT_BATCH_SIZE


def test_pc_metrics_are_off_by_default(output_dir, suite2p_stub):
    params = read_default_params("suite2p_registration")
    suite2p_registration(_input_ops(), output_dir, params=params)

    assert suite2p_stub["pc_metrics"] == 0


def test_pc_metrics_run_when_enabled(output_dir, suite2p_stub):
    params = copy.deepcopy(read_default_params("suite2p_registration"))
    params["Registration"]["do_regmetrics"] = True
    suite2p_registration(_input_ops(), output_dir, params=params)

    assert suite2p_stub["pc_metrics"] == 1


def _params_without_new_keys():
    params = copy.deepcopy(read_default_params("suite2p_registration"))
    del params["Registration"]["batch_size"]
    del params["Registration"]["do_regmetrics"]
    return params


def test_params_without_these_keys_take_the_shipped_defaults(output_dir, suite2p_stub):
    shipped = read_default_params("suite2p_registration")["Registration"]
    info = suite2p_registration(
        _input_ops(), output_dir, params=_params_without_new_keys()
    )

    assert suite2p_stub["register_batch_size"] == shipped["batch_size"]
    assert suite2p_stub["pc_metrics"] == (1 if shipped["do_regmetrics"] else 0)
    assert info["ops"].data["batch_size"] == INPUT_BATCH_SIZE


def test_params_without_these_keys_and_smoothing_use_the_input_batch_size(
    output_dir, suite2p_stub
):
    params = _params_without_new_keys()
    params["Registration"]["smooth_sigma_time"] = 2
    info = suite2p_registration(_input_ops(), output_dir, params=params)

    assert suite2p_stub["register_batch_size"] == INPUT_BATCH_SIZE
    assert info["ops"].data["reg_batch_size"] == INPUT_BATCH_SIZE
