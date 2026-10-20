import copy
import os
import pathlib
import pickle
import shutil
import sys
import types

import numpy as np
import pytest

from studio.app.common.core.experiment.experiment_writer import ExptDataWriter
from studio.app.dir_path import DIRPATH
from studio.app.optinist.dataclass import Suite2pData
from studio.app.optinist.dataclass.roi import RoiStack
from studio.app.optinist.wrappers.suite2p.roi import roi_label_images, suite2p_roi

LY, LX = 6, 5


def stack_on_disk(tmp_path, n=4):
    stack, path = RoiStack.create(str(tmp_path / "roi_mask_stack.npy"), (n, LY, LX))
    expected = np.full((n, LY, LX), np.nan)
    for i in range(n):
        stack[i, i, : i + 1] = i
        expected[i, i, : i + 1] = i
    stack.flush()
    del stack
    return RoiStack.open(path), expected


def test_the_whole_stack_pickles_as_a_reference(tmp_path):
    im, expected = stack_on_disk(tmp_path)
    blob = pickle.dumps({"im": im})
    assert len(blob) < 1000  # the path, not n x Ly x Lx x 8 bytes
    back = pickle.loads(blob)["im"]
    np.testing.assert_array_equal(back, expected)
    assert not back.flags.writeable


def test_anything_derived_from_the_stack_pickles_its_data(tmp_path):
    im, expected = stack_on_disk(tmp_path)
    for derived, want in [
        (im[:2], expected[:2]),  # a view, as EditROI.cancel takes
        (im[[0, 3]], expected[[0, 3]]),  # a fancy-indexed copy
        (np.vstack((im, expected[:1])), np.vstack((expected, expected[:1]))),
        (copy.deepcopy(im), expected),  # WorkflowFilter.filter_data
    ]:
        back = pickle.loads(pickle.dumps(derived))
        assert type(back) is np.ndarray
        np.testing.assert_array_equal(back, want)


def test_reductions_return_plain_arrays(tmp_path):
    im, expected = stack_on_disk(tmp_path)
    with pytest.warns(RuntimeWarning):  # all-NaN pixels, as with any stack
        result = np.nanmax(im, axis=0)
    assert type(result) is np.ndarray
    with pytest.warns(RuntimeWarning):
        np.testing.assert_array_equal(result, np.nanmax(expected, axis=0))


def test_label_images_equal_nanmax_over_the_stack():
    rng = np.random.default_rng(1)
    stat = []
    for _ in range(12):  # overlapping ROIs, as suite2p can return
        y0, x0 = rng.integers(0, LY - 2), rng.integers(0, LX - 2)
        yy, xx = np.mgrid[y0 : y0 + 3, x0 : x0 + 3]
        stat.append({"ypix": yy.ravel(), "xpix": xx.ravel()})
    iscell = rng.integers(0, 2, len(stat))
    stack = np.full((len(stat), LY, LX), np.nan)
    for i, s in enumerate(stat):
        stack[i, s["ypix"], s["xpix"]] = i

    labels = roi_label_images(stat, iscell, LY, LX)

    with pytest.warns(RuntimeWarning):
        np.testing.assert_array_equal(labels["all"], np.nanmax(stack, axis=0))
    with pytest.warns(RuntimeWarning):
        np.testing.assert_array_equal(
            labels["cell"], np.nanmax(stack[iscell != 0], axis=0)
        )
    with pytest.warns(RuntimeWarning):
        np.testing.assert_array_equal(
            labels["non_cell"], np.nanmax(stack[iscell == 0], axis=0)
        )


def test_copying_an_experiment_remaps_the_stack_without_reading_it(tmp_path):
    old_id, new_id = "0123abcd", "4567cdef"
    node_dir = tmp_path / "ws" / old_id / "suite2p_roi_x"
    node_dir.mkdir(parents=True)
    im, expected = stack_on_disk(node_dir)
    with open(node_dir / "suite2p_roi_x.pkl", "wb") as f:
        pickle.dump({"nwbfile": {"all_roi_img": im}, "edit_roi_data": {"im": im}}, f)
    copied = tmp_path / "ws" / new_id
    shutil.copytree(tmp_path / "ws" / old_id, copied)
    copied_stack = copied / "suite2p_roi_x" / "roi_mask_stack.npy"
    stack_bytes = copied_stack.read_bytes()
    os.utime(copied_stack, ns=(0, 0))

    ExptDataWriter("", "ws", old_id)._ExptDataWriter__copy_data_replace_unique_id(
        str(copied), old_id, new_id
    )

    assert copied_stack.stat().st_mtime_ns == 0  # not rewritten
    assert copied_stack.read_bytes() == stack_bytes
    copied_pkl = copied / "suite2p_roi_x" / "suite2p_roi_x.pkl"
    assert copied_pkl.stat().st_size < 1000
    with open(copied_pkl, "rb") as f:
        back = pickle.load(f)
    a, b = back["nwbfile"]["all_roi_img"], back["edit_roi_data"]["im"]
    assert a is b
    assert type(a) is RoiStack and a._stack_path == str(copied_stack)
    np.testing.assert_array_equal(a, expected)


def run_suite2p_roi(monkeypatch, tmp_path, n_roi):
    stat = np.array(
        [
            {"ypix": np.array([i]), "xpix": np.array([i]), "lam": [1.0]}
            for i in range(n_roi)
        ]
    )
    suite2p = types.ModuleType("suite2p")
    suite2p.default_ops = dict
    suite2p.detection = types.SimpleNamespace(detect=lambda ops, classfile: (ops, stat))
    suite2p.extraction = types.SimpleNamespace(
        create_masks_and_extract=lambda ops, stat: (
            ops,
            stat,
            np.ones((n_roi, 3)),
            np.ones((n_roi, 3)),
            None,
            None,
        )
    )
    suite2p.classification = types.SimpleNamespace(
        builtin_classfile=pathlib.Path("builtin"),
        user_classfile=pathlib.Path("user"),
        classify=lambda stat, classfile: np.ones((len(stat), 2)),
    )
    monkeypatch.setitem(sys.modules, "suite2p", suite2p)
    output_dir = tmp_path / "ws" / "0123abcd" / "suite2p_roi_x"
    output_dir.mkdir(parents=True, exist_ok=True)
    ops = {
        "Ly": LY,
        "Lx": LX,
        "nframes": 3,
        "filelist": ["movie.tif"],
        "use_builtin_classifier": True,
        "max_proj": np.zeros((LY, LX)),
        "Vcorr": np.zeros((LY, LX)),
    }
    return suite2p_roi(Suite2pData(ops), str(output_dir), params={})


def test_the_node_references_the_stack_it_wrote(monkeypatch, tmp_path):
    info = run_suite2p_roi(monkeypatch, tmp_path, n_roi=2)
    im = info["edit_roi_data"].im
    assert type(im) is RoiStack and im.shape == (2, LY, LX)
    (postprocess,) = info["nwbfile"]["POSTPROCESS"].values()
    assert postprocess["all_roi_img"] is im


def test_no_rois_still_finishes_with_empty_outputs(monkeypatch, tmp_path):
    info = run_suite2p_roi(monkeypatch, tmp_path, n_roi=0)
    assert info["edit_roi_data"].im.shape == (0, LY, LX)


def test_a_failed_stack_write_fails_the_node(monkeypatch, tmp_path):
    def disk_full(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(RoiStack, "create", disk_full)
    with pytest.raises(OSError, match="No space left"):
        run_suite2p_roi(monkeypatch, tmp_path, n_roi=2)


@pytest.mark.parametrize(
    "derive", [lambda im: im, lambda im: im[[0, 2]], copy.deepcopy]
)
def test_nanmax_with_a_nan_initial_sees_the_stack(tmp_path, derive):
    # numpy's fast path for initial= is for exact ndarrays; a subclass took the
    # slow path and returned all NaN, blanking every ROI image a commit redrew
    im, expected = stack_on_disk(tmp_path)
    result = np.nanmax(derive(im), axis=0, initial=np.nan)
    np.testing.assert_array_equal(
        result, np.nanmax(derive(expected), axis=0, initial=np.nan)
    )
    assert type(result) is np.ndarray
    assert np.isscalar(im.sum()) and np.isscalar(np.nanmax(im))


def test_a_stack_under_the_output_dir_pickles_relative_to_it(tmp_path, monkeypatch):
    monkeypatch.setattr(DIRPATH, "OUTPUT_DIR", str(tmp_path))
    node_dir = tmp_path / "ws" / "0123" / "suite2p_roi_x"
    node_dir.mkdir(parents=True)
    im, expected = stack_on_disk(node_dir)
    blob = pickle.dumps(im)
    assert b"roi_mask_stack.npy" in blob and str(tmp_path).encode() not in blob
    np.testing.assert_array_equal(pickle.loads(blob), expected)
    moved = tmp_path / "moved"
    shutil.move(str(tmp_path / "ws"), str(moved / "ws"))
    monkeypatch.setattr(DIRPATH, "OUTPUT_DIR", str(moved))
    np.testing.assert_array_equal(pickle.loads(blob), expected)


def test_a_rerun_replaces_the_file_so_an_open_stack_keeps_its_data(
    monkeypatch, tmp_path
):
    first = run_suite2p_roi(monkeypatch, tmp_path, n_roi=3)["edit_roi_data"].im
    before = np.array(first)
    second = run_suite2p_roi(monkeypatch, tmp_path, n_roi=1)["edit_roi_data"].im
    assert second.shape[0] == 1
    np.testing.assert_array_equal(first, before)
    assert not (
        tmp_path / "ws" / "0123abcd" / "suite2p_roi_x" / "roi_mask_stack.npy.tmp"
    ).exists()
