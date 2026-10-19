"""suite2p_registration lays its yaml over suite2p 1.1.0's settings tree, runs
the pipeline's registration step only, and keeps the flat ops contract. suite2p
is stubbed; the parameters are the shipped yaml.
"""
import copy
import os
import sys
import types

import numpy as np
import pytest

from studio.app.common.core.workflow.workflow_params import read_default_params
from studio.app.optinist.dataclass import Suite2pData
from studio.app.optinist.wrappers.suite2p.registration import suite2p_registration
from studio.tests.app.optinist.suite2p_settings_1_1_0 import default_settings

FRAMES, H, W = 6, 8, 8


@pytest.fixture
def output_dir(tmp_path):
    d = tmp_path / "output" / "default" / "uid" / "func"
    d.mkdir(parents=True)
    return str(d)


@pytest.fixture
def suite2p_stub(monkeypatch):
    calls = {}

    class BinaryFile:
        def __init__(self, Ly, Lx, filename, n_frames=None, write=False):
            calls["binary"] = dict(filename=filename, n_frames=n_frames, write=write)
            self.shape = (n_frames, Ly, Lx)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def __getitem__(self, indices):
            n = len(range(*indices.indices(self.shape[0])))
            return np.zeros((n,) + self.shape[1:], np.int16)

    def pipeline(save_path, f_reg, run_registration, settings, device):
        calls["pipeline"] = dict(
            save_path=save_path,
            run_registration=run_registration,
            settings=copy.deepcopy(settings),
            device=device,
        )
        n, Ly, Lx = f_reg.shape
        reg_outputs = {
            "refImg": np.ones((Ly, Lx), np.float32),
            "meanImgE": np.ones((Ly, Lx), np.float32),
            "yoff": np.zeros(n),
            "xoff": np.zeros(n),
            "yrange": [0, Ly],
            "xrange": [0, Lx],
            "badframes": np.zeros(n, bool),
        }
        return (reg_outputs,) + (None,) * 10 + ({},)

    fake = types.ModuleType("suite2p")
    fake.default_settings = default_settings
    fake.io = types.SimpleNamespace(BinaryFile=BinaryFile)
    fake.pipeline = pipeline
    run_s2p = types.ModuleType("suite2p.run_s2p")
    run_s2p._assign_torch_device = lambda name: f"device:{name}"
    monkeypatch.setitem(sys.modules, "suite2p", fake)
    monkeypatch.setitem(sys.modules, "suite2p.run_s2p", run_s2p)
    return calls


def _input_ops(tmp_path):
    return Suite2pData(
        {
            "meanImg": np.ones((H, W), np.float32),
            "nframes": FRAMES,
            "Ly": H,
            "Lx": W,
            "reg_file": str(tmp_path / "data.bin"),
            "batch_size": 500,
        }
    )


def test_yaml_lands_in_the_settings_tree_and_only_registration_runs(
    output_dir, tmp_path, suite2p_stub
):
    params = copy.deepcopy(read_default_params("suite2p_registration"))
    params["registration"]["maxregshift"] = 0.2  # not suite2p's default
    suite2p_registration(_input_ops(tmp_path), output_dir, params=params)

    call = suite2p_stub["pipeline"]
    assert call["save_path"] == output_dir
    assert call["run_registration"] is True
    assert call["device"] == "device:cpu"
    settings = call["settings"]
    assert settings["run"]["do_detection"] is False
    assert settings["run"]["do_regmetrics"] is params["run"]["do_regmetrics"]
    assert (
        settings["registration"]["batch_size"] == params["registration"]["batch_size"]
    )
    assert settings["registration"]["maxregshift"] == 0.2
    assert settings["registration"]["spatial_taper"] == 3.45
    # suite2p slices with it
    assert isinstance(settings["registration"]["bidiphase"], int)


def test_params_without_a_key_take_the_shipped_yaml_not_suite2p_defaults(
    output_dir, tmp_path, suite2p_stub
):
    # a node saved before a key was added to the yaml cannot add it from its
    # parameter panel; suite2p's own defaults are cuda and metrics on
    params = copy.deepcopy(read_default_params("suite2p_registration"))
    del params["torch_device"], params["run"]

    suite2p_registration(_input_ops(tmp_path), output_dir, params=params)

    call = suite2p_stub["pipeline"]
    assert call["device"] == "device:cpu"
    assert call["settings"]["run"]["do_regmetrics"] is False


def test_temporal_smoothing_registers_with_the_input_batch_size(
    output_dir, tmp_path, suite2p_stub
):
    # the phase correlations are smoothed over each batch, so a different batch
    # size gives different shifts: suite2p_file_convert's 500 is kept
    params = copy.deepcopy(read_default_params("suite2p_registration"))
    params["registration"]["smooth_sigma_time"] = 2

    suite2p_registration(_input_ops(tmp_path), output_dir, params=params)

    assert suite2p_stub["pipeline"]["settings"]["registration"]["batch_size"] == 500


@pytest.mark.parametrize("block_size", [[128], "128, 128", [128, 128, 128]])
def test_a_block_size_that_is_not_two_numbers_is_refused(
    output_dir, tmp_path, suite2p_stub, block_size
):
    params = copy.deepcopy(read_default_params("suite2p_registration"))
    params["registration"]["block_size"] = block_size
    with pytest.raises(ValueError, match="block_size"):
        suite2p_registration(_input_ops(tmp_path), output_dir, params=params)


def test_the_binary_is_registered_in_place(output_dir, tmp_path, suite2p_stub):
    params = read_default_params("suite2p_registration")
    suite2p_registration(_input_ops(tmp_path), output_dir, params=params)

    assert suite2p_stub["binary"] == {
        "filename": str(tmp_path / "data.bin"),
        "n_frames": FRAMES,
        "write": True,
    }


def test_output_ops_keep_the_flat_contract_and_carry_the_settings(
    output_dir, tmp_path, suite2p_stub
):
    params = read_default_params("suite2p_registration")
    info = suite2p_registration(_input_ops(tmp_path), output_dir, params=params)

    ops = info["ops"].data
    # what suite2p_roi, suite2p_spike_deconv and ROI editing read
    for key in ("reg_file", "nframes", "Ly", "Lx", "batch_size", "meanImg"):
        assert key in ops, key
    for key in ("refImg", "meanImgE", "yrange", "xrange", "badframes"):
        assert key in ops, key
    assert ops["settings"]["version"] == "1.1.0"
    assert info["mc_images"].path == [f"{output_dir}/tiff/mc_images/mc_images.tif"]
    assert os.path.exists(info["mc_images"].path[0])


def test_a_retired_parameter_fails_the_node_by_name(output_dir, tmp_path, suite2p_stub):
    params = copy.deepcopy(read_default_params("suite2p_registration"))
    params["registration"]["1Preg"] = False
    with pytest.raises(KeyError, match="1Preg"):
        suite2p_registration(_input_ops(tmp_path), output_dir, params=params)


@pytest.mark.parametrize(
    "Ly, Lx, nonrigid", [(300, 300, True), (128, 300, False), (300, 64, False)]
)
def test_nonrigid_needs_a_frame_larger_than_the_block_on_both_axes(
    output_dir, tmp_path, suite2p_stub, Ly, Lx, nonrigid
):
    # suite2p 1.1.0 interpolates the block shifts onto a grid that is empty
    # when an axis has a single block, and raises
    params = read_default_params("suite2p_registration")
    assert params["registration"]["block_size"] == [128, 128]
    ops = _input_ops(tmp_path).data
    ops.update(Ly=Ly, Lx=Lx, meanImg=np.ones((Ly, Lx), np.float32))

    suite2p_registration(Suite2pData(ops), output_dir, params=params)

    assert suite2p_stub["pipeline"]["settings"]["registration"]["nonrigid"] is nonrigid
