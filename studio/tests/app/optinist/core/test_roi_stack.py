import copy
import os
import pickle
import shutil

import numpy as np
import pytest

from studio.app.common.core.experiment.experiment_writer import ExptDataWriter
from studio.app.dir_path import DIRPATH
from studio.app.optinist.dataclass.roi import RoiStack

LY, LX = 6, 5


def stack_on_disk(tmp_path, n=4):
    """A stack file as suite2p_roi wrote it before RoiMasks."""
    expected = np.full((n, LY, LX), np.nan)
    for i in range(n):
        expected[i, i, : i + 1] = i
    path = str(tmp_path / "roi_mask_stack.npy")
    np.save(path, expected)
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


def test_copying_an_experiment_remaps_the_stack_without_reading_it(tmp_path):
    old_id, new_id = "0123abcd", "4567cdef"
    node_dir = tmp_path / "ws" / old_id / "suite2p_roi_x"
    node_dir.mkdir(parents=True)
    im, expected = stack_on_disk(node_dir)
    with open(node_dir / "suite2p_roi_x.pkl", "wb") as f:
        pickle.dump({"nwbfile": {"all_roi_img": im}, "edit_roi_data": {"im": im}}, f)
    # the data filter's backup of the unfiltered node, restored on reset
    shutil.copy(node_dir / "suite2p_roi_x.pkl", node_dir / "suite2p_roi_x.pkl.orig")
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
    for name in ("suite2p_roi_x.pkl", "suite2p_roi_x.pkl.orig"):
        copied_pkl = copied / "suite2p_roi_x" / name
        assert copied_pkl.stat().st_size < 1000
        with open(copied_pkl, "rb") as f:
            back = pickle.load(f)
        a, b = back["nwbfile"]["all_roi_img"], back["edit_roi_data"]["im"]
        assert a is b
        assert type(a) is RoiStack and a._stack_path == str(copied_stack)
        np.testing.assert_array_equal(a, expected)


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


def test_a_reopened_reference_pickles_the_same_from_the_rule_cwd(tmp_path, monkeypatch):
    # a downstream rule runs in the experiment directory and re-pickles the
    # reference it loaded; the path must stay OUTPUT_DIR-relative, not grow
    monkeypatch.setattr(DIRPATH, "OUTPUT_DIR", str(tmp_path))
    node_dir = tmp_path / "ws" / "uid" / "suite2p_roi_x"
    node_dir.mkdir(parents=True)
    im, expected = stack_on_disk(node_dir)
    from_node = pickle.dumps(im)
    monkeypatch.chdir(tmp_path / "ws" / "uid")
    downstream = pickle.dumps(pickle.loads(from_node))
    assert b"ws/uid/suite2p_roi_x/roi_mask_stack.npy" in downstream
    assert b"ws/uid/ws/uid" not in downstream
    np.testing.assert_array_equal(pickle.loads(downstream), expected)
