"""suite2p_registration lays its yaml over suite2p 1.1.0's settings tree, runs
the pipeline's registration step only, and keeps the flat ops contract. suite2p
is stubbed; the parameters are the shipped yaml.
"""
import copy
import os
import sys
import types
import weakref

import numpy as np
import pytest
import tifffile

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
        """suite2p 1.1.0's: a memory map of the file."""

        def __init__(self, Ly, Lx, filename, n_frames=None, write=False):
            self.shape = (n_frames, Ly, Lx)
            mode = "r+" if write else "r"
            self.file = np.memmap(filename, np.int16, mode, shape=self.shape)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def __getitem__(self, indices):
            return self.file[indices]

        def __setitem__(self, indices, frames):
            self.file[indices] = frames

    def pipeline(save_path, f_reg, run_registration, settings, device, f_raw=None):
        n, Ly, Lx = f_reg.shape
        # what registration does: reads f_raw's frames (f_reg's without one),
        # shifts their bidiphase in place, and writes the registered frames
        # into f_reg. f_raw is tested for truth as assign_reg_io does, so a
        # plain array there fails as it would in suite2p.
        frames = (f_reg if not f_raw else f_raw)[0:n]
        calls["pipeline"] = dict(
            save_path=save_path,
            input=np.array(frames),
            run_registration=run_registration,
            settings=copy.deepcopy(settings),
            device=device,
        )
        frames[:, 1::2] += 1
        f_reg[0:n] = frames + 100
        reg_outputs = {
            "refImg": np.ones((Ly, Lx), np.float32),
            "meanImg": np.full((Ly, Lx), 2, np.float32),  # the input's is ones
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


def _movie(Ly=H, Lx=W):
    return np.arange(FRAMES * Ly * Lx).reshape(FRAMES, Ly, Lx).astype(np.int16)


def _input_ops(tmp_path, Ly=H, Lx=W):
    """File conversion's ops, and its binary of _movie()."""
    binary = tmp_path / "data.bin"
    _movie(Ly, Lx).tofile(binary)
    return Suite2pData(
        {
            "meanImg": np.ones((Ly, Lx), np.float32),
            "nframes": FRAMES,
            "Ly": Ly,
            "Lx": Lx,
            "reg_file": str(binary),
            "batch_size": 500,
        }
    )


def _registered(movie):
    """What the stubbed registration writes for movie."""
    registered = movie.copy()
    registered[:, 1::2] += 1
    return registered + 100


def test_yaml_lands_in_the_settings_tree_and_only_registration_runs(
    output_dir, tmp_path, suite2p_stub
):
    params = copy.deepcopy(read_default_params("suite2p_registration"))
    params["registration"]["maxregshift"] = 0.2  # not suite2p's default
    params["registration"]["bidiphase"] = 1.0  # the form sends floats
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
    assert settings["registration"]["bidiphase"] == 1
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


def test_temporal_smoothing_is_refused_on_1_1_0(output_dir, tmp_path, suite2p_stub):
    # 1.1.0's temporal_smooth returns numpy into torch.argmax: a TypeError
    # deep in registration for any positive value
    params = copy.deepcopy(read_default_params("suite2p_registration"))
    params["registration"]["smooth_sigma_time"] = 2

    with pytest.raises(ValueError, match="smooth_sigma_time"):
        suite2p_registration(_input_ops(tmp_path), output_dir, params=params)
    assert "pipeline" not in suite2p_stub


@pytest.mark.parametrize(
    "key, value",
    [
        ("nimg_init", 0),  # an empty reference stack
        ("batch_size", 0),  # range() step of zero
        ("smooth_sigma", 0),  # NaN kernel: NaN shifts, every frame bad, no error
        ("spatial_taper", 0),  # NaN taper: same
        ("spatial_taper", -1),
    ],
)
def test_a_setting_suite2p_divides_by_must_be_positive(
    output_dir, tmp_path, suite2p_stub, key, value
):
    params = copy.deepcopy(read_default_params("suite2p_registration"))
    params["registration"][key] = value
    with pytest.raises(ValueError, match=f"registration.{key}"):
        suite2p_registration(_input_ops(tmp_path), output_dir, params=params)


@pytest.mark.parametrize("block_size", [[128], "128, 128", [128, 128, 128]])
def test_a_block_size_that_is_not_two_numbers_is_refused(
    output_dir, tmp_path, suite2p_stub, block_size
):
    params = copy.deepcopy(read_default_params("suite2p_registration"))
    params["registration"]["block_size"] = block_size
    with pytest.raises(ValueError, match="block_size"):
        suite2p_registration(_input_ops(tmp_path), output_dir, params=params)


def test_registration_writes_mc_images_and_leaves_the_binary_unregistered(
    output_dir, tmp_path, suite2p_stub
):
    params = read_default_params("suite2p_registration")

    info = suite2p_registration(_input_ops(tmp_path), output_dir, params=params)

    np.testing.assert_array_equal(suite2p_stub["pipeline"]["input"], _movie())
    np.testing.assert_array_equal(
        np.fromfile(tmp_path / "data.bin", np.int16), _movie().ravel()
    )
    (path,) = info["mc_images"].path
    np.testing.assert_array_equal(tifffile.imread(path), _registered(_movie()))
    # the movie suite2p_roi and ROI editing read
    ops = info["ops"].data
    assert ops["registered_tiff"] == path
    # not under reg_file, which an earlier release reads as registered
    assert ops["raw_file"] == str(tmp_path / "data.bin")
    assert "reg_file" not in ops


def test_the_map_is_closed_before_mc_images_is_renamed(
    output_dir, tmp_path, suite2p_stub, monkeypatch
):
    # Windows cannot rename a file that is still mapped
    maps, mapped_at_rename = [], []
    memmap, replace = tifffile.memmap, os.replace

    def recording_memmap(*args, **kwargs):
        mapped = memmap(*args, **kwargs)
        maps.append(weakref.ref(mapped))
        return mapped

    def recording_replace(src, dst):
        if str(src).endswith(".partial"):
            mapped_at_rename.extend(ref() is not None for ref in maps)
        replace(src, dst)

    monkeypatch.setattr(tifffile, "memmap", recording_memmap)
    monkeypatch.setattr(os, "replace", recording_replace)
    params = read_default_params("suite2p_registration")

    suite2p_registration(_input_ops(tmp_path), output_dir, params=params)

    assert mapped_at_rename == [False]


def test_a_second_registration_registers_the_first_ones_result(
    output_dir, tmp_path, suite2p_stub
):
    params = read_default_params("suite2p_registration")
    first = suite2p_registration(_input_ops(tmp_path), output_dir, params=params)
    second_dir = tmp_path / "output" / "default" / "uid" / "second"
    second_dir.mkdir()

    second = suite2p_registration(first["ops"], str(second_dir), params=params)

    once = _registered(_movie())
    np.testing.assert_array_equal(suite2p_stub["pipeline"]["input"], once)
    (path,) = second["mc_images"].path
    np.testing.assert_array_equal(tifffile.imread(path), _registered(once))
    # the first node's result is left as it was
    np.testing.assert_array_equal(tifffile.imread(first["mc_images"].path[0]), once)
    assert second["ops"].data["registered_tiff"] == path
    assert second["ops"].data["raw_file"] == str(tmp_path / "data.bin")


def test_output_ops_keep_the_flat_contract_and_carry_the_settings(
    output_dir, tmp_path, suite2p_stub
):
    params = read_default_params("suite2p_registration")
    info = suite2p_registration(_input_ops(tmp_path), output_dir, params=params)

    ops = info["ops"].data
    # what suite2p_roi, suite2p_spike_deconv and ROI editing read
    for key in (
        "registered_tiff",
        "raw_file",
        "nframes",
        "Ly",
        "Lx",
        "batch_size",
        "meanImg",
    ):
        assert key in ops, key
    for key in ("refImg", "meanImgE", "yrange", "xrange", "badframes"):
        assert key in ops, key
    assert ops["settings"]["version"] == "1.1.0"
    assert info["mc_images"].path == [f"{output_dir}/tiff/mc_images/mc_images.tif"]
    assert os.path.exists(info["mc_images"].path[0])


def test_mean_image_output_is_the_registered_mean_not_the_input_mean(
    output_dir, tmp_path, suite2p_stub
):
    params = read_default_params("suite2p_registration")
    info = suite2p_registration(_input_ops(tmp_path), output_dir, params=params)

    np.testing.assert_array_equal(info["meanImg"].data, np.full((H, W), 2))
    np.testing.assert_array_equal(info["refImg"].data, np.ones((H, W)))


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
    suite2p_registration(_input_ops(tmp_path, Ly, Lx), output_dir, params=params)

    assert suite2p_stub["pipeline"]["settings"]["registration"]["nonrigid"] is nonrigid
