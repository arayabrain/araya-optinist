import copy
import pickle

import numpy as np
import pytest

from studio.app.optinist.dataclass.roi import RoiStack
from studio.app.optinist.wrappers.suite2p.roi import roi_label_images

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
