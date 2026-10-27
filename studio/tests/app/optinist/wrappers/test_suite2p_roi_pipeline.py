"""suite2p_roi lays its yaml over suite2p 1.1.0's settings tree, hands the
registration step's frame range to pipeline(), and keeps the flat ops contract
through the empty and failing paths. suite2p is stubbed.
"""
import copy
import logging
import os
import sys
import types

import numpy as np
import pytest

from studio.app.common.core.workflow.workflow_params import read_default_params
from studio.app.optinist.dataclass import Suite2pData
from studio.app.optinist.wrappers.suite2p.roi import suite2p_roi
from studio.tests.app.optinist.suite2p_settings_1_1_0 import default_settings

FRAMES, LY, LX = 600, 10, 12  # 600 frames: the default tau x fs bin of 30 fits
CELLS = [((2, 3), (4, 5)), ((7, 8), (9, 10))]  # (ypix, xpix) of two ROIs


def _stat():
    return np.array(
        [
            {
                "ypix": np.array(ypix),
                "xpix": np.array(xpix),
                "lam": np.ones(2, np.float32),
                "overlap": np.zeros(2, bool),
                "med": [ypix[0], xpix[0]],
                "npix": 2,
                "radius": 1.0,
            }
            for ypix, xpix in CELLS
        ],
        dtype=object,
    )


def _detect_outputs():
    return {
        "max_proj": np.ones((LY, LX)),
        "Vcorr": np.full((LY, LX), 2.0),
        "meanImg_crop": np.zeros((LY, LX)),
        "diameter": np.array([12.0, 12.0]),
    }


def _found(stat=None, F=None, iscell=None):
    stat = _stat() if stat is None else stat
    F = np.ones((len(stat), FRAMES), np.float32) if F is None else F
    if iscell is None:
        iscell = np.array([[1, 0.9], [0, 0.1]])[: len(stat)]
    return (None, _detect_outputs(), stat, F, F * 0.5, None, None, None, iscell) + (
        None,
        None,
        {},
    )


def _nothing_found():
    return (None, _detect_outputs(), np.array([], dtype=object)) + (None,) * 8 + ({},)


@pytest.fixture
def output_dir(tmp_path):
    d = tmp_path / "output" / "default" / "uid" / "suite2p_roi_x"
    d.mkdir(parents=True)
    return str(d)


@pytest.fixture
def suite2p_stub(monkeypatch):
    calls = {"result": _found()}

    class BinaryFile:
        def __init__(self, Ly, Lx, filename, n_frames=None, write=False):
            calls["binary"] = dict(filename=filename, n_frames=n_frames, write=write)
            self.shape = (n_frames, Ly, Lx)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def pipeline(save_path, f_reg, run_registration, settings, device):
        reg_path = os.path.join(save_path, "reg_outputs.npy")
        calls["pipeline"] = dict(
            save_path=save_path,
            run_registration=run_registration,
            settings=copy.deepcopy(settings),
            device=device,
            reg_outputs=np.load(reg_path, allow_pickle=True).item()
            if os.path.exists(reg_path)
            else None,
        )
        # what pipeline() writes beside reg_outputs.npy
        np.save(os.path.join(save_path, "F.npy"), np.zeros(1))
        result = calls["result"]
        if isinstance(result, Exception):
            raise result
        return result

    fake = types.ModuleType("suite2p")
    fake.default_settings = default_settings
    fake.io = types.SimpleNamespace(BinaryFile=BinaryFile)
    fake.pipeline = pipeline
    run_s2p = types.ModuleType("suite2p.run_s2p")
    run_s2p._assign_torch_device = lambda name: f"device:{name}"
    monkeypatch.setitem(sys.modules, "suite2p", fake)
    monkeypatch.setitem(sys.modules, "suite2p.run_s2p", run_s2p)
    return calls


def _registered_ops(tmp_path, **extra):
    ops = {
        "Ly": LY,
        "Lx": LX,
        "nframes": FRAMES,
        "reg_file": str(tmp_path / "data.bin"),
        "file_list": [str(tmp_path / "movie.tif")],
        "batch_size": 500,
        "yrange": [1, LY - 1],
        "xrange": [0, LX],
        "badframes": np.zeros(FRAMES, bool),
    }
    ops.update(extra)
    return Suite2pData(ops)


def test_yaml_lands_in_the_settings_tree_and_detection_runs_on_the_binary(
    output_dir, tmp_path, suite2p_stub
):
    params = copy.deepcopy(read_default_params("suite2p_roi"))
    params["detection"]["threshold_scaling"] = 2.5  # not suite2p's default
    info = suite2p_roi(
        _registered_ops(tmp_path),
        output_dir,
        params=params,
        nwbfile={"imaging_plane": {"imaging_rate": 15.0}},
    )

    call = suite2p_stub["pipeline"]
    assert call["save_path"] == f"{output_dir}/suite2p"
    assert call["run_registration"] is False
    assert call["device"] == "device:cpu"
    settings = call["settings"]
    assert settings["fs"] == 15.0
    assert settings["run"]["do_deconvolution"] is False
    assert settings["detection"]["algorithm"] == params["detection"]["algorithm"]
    assert settings["detection"]["threshold_scaling"] == 2.5
    assert settings["tau"] == params["tau"]
    assert settings["diameter"] == [12.0, 12.0]
    assert suite2p_stub["binary"]["filename"] == str(tmp_path / "data.bin")
    assert suite2p_stub["binary"]["write"] is False

    # the registration step's frame range, where pipeline() reads it
    assert call["reg_outputs"]["yrange"] == [1, LY - 1]
    assert call["reg_outputs"]["badframes"].shape == (FRAMES,)
    # pipeline()'s copies of the outputs are gone; the pickle carries them
    assert not os.path.exists(call["save_path"])

    ops = info["ops"].data
    assert len(ops["stat"]) == len(CELLS)
    assert ops["F"].shape == ops["Fneu"].shape == (len(CELLS), FRAMES)
    assert ops["fs"] == 15.0
    assert ops["settings"]["version"] == "1.1.0"
    np.testing.assert_array_equal(ops["max_proj"], np.ones((LY, LX)))
    np.testing.assert_array_equal(info["iscell"].data, [1, 0])
    assert info["edit_roi_data"].images.path == [str(tmp_path / "movie.tif")]
    roi_masks = info["edit_roi_data"].roi_masks
    assert len(roi_masks) == len(CELLS) and roi_masks.shape == (LY, LX)


def test_without_a_registration_node_suite2p_gets_the_full_frame(
    output_dir, tmp_path, suite2p_stub
):
    # a file from an earlier run of the node must not be read either
    os.makedirs(f"{output_dir}/suite2p")
    np.save(f"{output_dir}/suite2p/reg_outputs.npy", {"yrange": [3, 4]})
    ops = _registered_ops(tmp_path).data
    for key in ("yrange", "xrange", "badframes"):
        del ops[key]

    suite2p_roi(Suite2pData(ops), output_dir, params=read_default_params("suite2p_roi"))

    assert suite2p_stub["pipeline"]["reg_outputs"] is None


def test_no_rois_gives_empty_outputs_and_keeps_the_detection_images(
    output_dir, tmp_path, suite2p_stub
):
    suite2p_stub["result"] = _nothing_found()

    info = suite2p_roi(
        _registered_ops(tmp_path), output_dir, params=read_default_params("suite2p_roi")
    )

    ops = info["ops"].data
    assert ops["stat"] == []
    assert ops["F"].shape == (0, FRAMES)
    assert info["iscell"].data.shape == (0,)
    roi_masks = info["edit_roi_data"].roi_masks
    assert len(roi_masks) == 0 and roi_masks.shape == (LY, LX)
    np.testing.assert_array_equal(ops["max_proj"], np.ones((LY, LX)))
    assert np.isnan(info["cell_roi"].data).all()


@pytest.mark.parametrize("label", [1, 0], ids=["all_cells", "no_cells"])
def test_an_empty_cell_or_non_cell_set_gives_an_all_nan_image(
    output_dir, tmp_path, suite2p_stub, label
):
    n = len(CELLS)
    suite2p_stub["result"] = _found(
        iscell=np.column_stack([np.full(n, label), np.full(n, 0.5)])
    )

    info = suite2p_roi(
        _registered_ops(tmp_path), output_dir, params=read_default_params("suite2p_roi")
    )

    assert list(info["iscell"].data) == [label] * n
    filled, empty = (
        ("cell_roi", "non_cell_roi") if label else ("non_cell_roi", "cell_roi")
    )
    assert np.isnan(info[empty].data).all()
    np.testing.assert_array_equal(info[filled].data, info["all_roi"].data)
    assert np.nanmax(info["all_roi"].data) == n - 1


@pytest.mark.parametrize(
    "message",
    [
        "no ROIs were found -- check registered binary",
        "no ROIs passed preclassify -- turn off preclassify",
    ],
)
def test_suite2p_finding_no_roi_is_logged_and_gives_empty_outputs(
    output_dir, tmp_path, suite2p_stub, caplog, message
):
    suite2p_stub["result"] = ValueError(message)

    with caplog.at_level(logging.WARNING):
        info = suite2p_roi(
            _registered_ops(tmp_path),
            output_dir,
            params=read_default_params("suite2p_roi"),
        )

    assert message in caplog.text
    ops = info["ops"].data
    assert ops["stat"] == [] and ops["F"].shape == (0, FRAMES)
    np.testing.assert_array_equal(ops["max_proj"], np.zeros((LY, LX)))
    np.testing.assert_array_equal(ops["Vcorr"], np.zeros((LY, LX)))
    assert not os.path.exists(f"{output_dir}/suite2p")


@pytest.mark.parametrize(
    "error", [ZeroDivisionError("integer division by zero"), ValueError("bad shape")]
)
def test_any_other_detection_error_fails_the_node(
    output_dir, tmp_path, suite2p_stub, error
):
    suite2p_stub["result"] = error

    with pytest.raises(type(error), match=str(error)):
        suite2p_roi(
            _registered_ops(tmp_path),
            output_dir,
            params=read_default_params("suite2p_roi"),
        )
    assert not os.path.exists(f"{output_dir}/suite2p")


@pytest.mark.parametrize(
    "group, key, value, match",
    [
        ("detection", "algorithm", "cellpose", "algorithm"),
        ("detection", "max_overlap", 1.0, "max_overlap"),
        (None, "diameter", [12], "diameter"),
        (None, "diameter", "12, 12", "diameter"),
        (None, "tau", 0, "tau"),  # ZeroDivisionError two nodes later
        ("detection", "nbins", 0, "nbins"),
        ("detection", "threshold_scaling", 0, "threshold_scaling"),  # junk ROIs
        ("extraction", "batch_size", -1, "batch_size"),  # all-zero traces
    ],
)
def test_settings_suite2p_would_misread_are_refused(
    output_dir, tmp_path, suite2p_stub, group, key, value, match
):
    params = copy.deepcopy(read_default_params("suite2p_roi"))
    (params[group] if group else params)[key] = value
    with pytest.raises(ValueError, match=match):
        suite2p_roi(_registered_ops(tmp_path), output_dir, params=params)
    assert "pipeline" not in suite2p_stub


def test_a_non_positive_imaging_rate_is_refused(output_dir, tmp_path, suite2p_stub):
    with pytest.raises(ValueError, match="imaging_rate"):
        suite2p_roi(
            _registered_ops(tmp_path),
            output_dir,
            params=read_default_params("suite2p_roi"),
            nwbfile={"imaging_plane": {"imaging_rate": 0}},
        )


@pytest.mark.parametrize(
    "tau, fs, ops_extra",
    [
        (20, 30, {}),  # a 600-frame bin, above bin_movie's 500-frame batch
        (1, 30, {"nframes": 20}),  # a movie shorter than one bin
        (1, 30, {"badframes": np.ones(FRAMES, bool)}),  # registration left none
    ],
)
def test_a_detection_bin_wider_than_the_usable_frames_is_refused(
    output_dir, tmp_path, suite2p_stub, tau, fs, ops_extra
):
    # bin_movie divides by (batch // bin_size), a bare ZeroDivisionError
    params = copy.deepcopy(read_default_params("suite2p_roi"))
    params["tau"] = tau
    with pytest.raises(ValueError, match="lower tau or raise nbins"):
        suite2p_roi(
            _registered_ops(tmp_path, **ops_extra),
            output_dir,
            params=params,
            nwbfile={"imaging_plane": {"imaging_rate": fs}},
        )


def test_a_pre_upgrade_parameter_tree_is_refused(output_dir, tmp_path, suite2p_stub):
    # what the type check leaves of a 0.10.3 roi tree: its unknown groups are
    # dropped and only these two keys overlap the 1.1.0 yaml
    with pytest.raises(KeyError, match="delete the node"):
        suite2p_roi(
            _registered_ops(tmp_path),
            output_dir,
            params={"tau": 1.0, "classification": {}},
        )
    assert "pipeline" not in suite2p_stub


def test_a_pre_upgrade_registration_pickle_is_accepted(
    output_dir, tmp_path, suite2p_stub
):
    ops = _registered_ops(tmp_path).data
    del ops["file_list"]
    ops.update(filelist=[str(tmp_path / "old.tif")], aspect=1.0, tau=1.0)

    info = suite2p_roi(
        Suite2pData(ops), output_dir, params=read_default_params("suite2p_roi")
    )

    assert "aspect" not in info["ops"].data
    assert info["edit_roi_data"].images.path == [str(tmp_path / "old.tif")]


def _label_img(info):
    (postprocess,) = info["nwbfile"]["POSTPROCESS"].values()
    return postprocess["all_roi_label_img"]


def _run(tmp_path, output_dir):
    return suite2p_roi(
        _registered_ops(tmp_path), output_dir, params=read_default_params("suite2p_roi")
    )


def test_the_rois_are_kept_as_pixel_lists_and_no_stack_is_written(
    output_dir, tmp_path, suite2p_stub
):
    info = _run(tmp_path, output_dir)
    stat = info["ops"].data["stat"]
    stack = np.full((len(stat), LY, LX), np.nan)
    for i, s in enumerate(stat):
        stack[i, s["ypix"], s["xpix"]] = i

    # EditRoiData's ROIs are the stack's layers; NWB keeps the all-ROI image
    roi_masks = info["edit_roi_data"].roi_masks
    for i in range(len(stat)):
        np.testing.assert_array_equal(roi_masks.label_image([i]), stack[i])
    all_roi = np.nanmax(stack, axis=0, initial=np.nan)
    np.testing.assert_array_equal(_label_img(info), all_roi[np.newaxis])
    np.testing.assert_array_equal(info["all_roi"].data, all_roi)
    assert not os.path.exists(f"{output_dir}/roi_mask_stack.npy")
