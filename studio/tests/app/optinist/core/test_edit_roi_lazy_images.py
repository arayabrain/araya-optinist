import numpy as np
import pytest
import tifffile

from studio.app.common.dataclass.image import ImageData
from studio.app.optinist.core.edit_ROI.edit_ROI import CellType
from studio.app.optinist.dataclass import EditRoiData, FluoData
from studio.app.optinist.dataclass.roi import open_edit_images

FRAMES, LY, LX = 7, 6, 5


def movie():
    rng = np.random.default_rng(0)
    return rng.integers(0, 4000, (FRAMES, LY, LX)).astype(np.int16)


@pytest.fixture
def movie_ref(tmp_path):
    images = movie()
    # Written the way ImageData writes an array: tiff/<name>/<name>.tif
    return images, ImageData(images, output_dir=str(tmp_path), file_name="mc_images")


def test_shape_comes_from_headers_and_matches_data(movie_ref, tmp_path):
    images, ref = movie_ref
    assert ref.shape == ref.data.shape == images.shape
    assert ref.ndim == 3

    # Several files stack along frames, as data's concatenate does
    second = tmp_path / "second.tif"
    tifffile.imwrite(second, images[:3])
    both = ImageData([ref.path[0], str(second)])
    assert both.shape == both.data.shape == (FRAMES + 3, LY, LX)


def test_output_path_does_not_read_the_frames(movie_ref, monkeypatch):
    _, ref = movie_ref

    def no_full_read(self):
        raise AssertionError("output_path read the whole stack")

    monkeypatch.setattr(ImageData, "data", property(no_full_read))
    assert ref.output_path.max_index == FRAMES


def test_open_edit_images_memory_maps_a_reference(movie_ref):
    images, ref = movie_ref
    opened = open_edit_images(ref)
    assert isinstance(opened, np.memmap)
    assert np.array_equal(opened, images)


def test_open_edit_images_passes_arrays_through():
    # Pickles written before the change still carry the movie itself
    images = movie()
    assert open_edit_images(images) is images
    assert open_edit_images(None) is None


@pytest.mark.parametrize(
    "module",
    ["lccd_edit_roi", "vacant_roi_edit_roi", "caiman_edit_roi"],
)
def test_commit_traces_match_between_array_and_reference(movie_ref, tmp_path, module):
    import importlib

    commit_edit = importlib.import_module(
        f"studio.app.optinist.core.edit_ROI.wrappers.{module}"
    ).commit_edit
    images, ref = movie_ref

    def state():
        im = np.full((3, LY, LX), np.nan)
        im[0, 0:2, :] = 0
        im[1, 3, 1:4] = 1
        im[2, 0:2, :] = 2  # a pending merge of ROI 0, appended
        data = EditRoiData(images=None, im=im)
        data.temp_merge_roi[2.0] = [0, 0]
        fluo = FluoData(np.ones((2, FRAMES)))
        iscell = np.array([CellType.ROI, CellType.ROI, CellType.TEMP_ADD])
        return data, fluo, iscell

    from_array = commit_edit(images, *state(), str(tmp_path / "a"), "x")
    from_ref = commit_edit(ref, *state(), str(tmp_path / "b"), "x")
    np.testing.assert_array_equal(
        from_array["fluorescence"].data, from_ref["fluorescence"].data
    )


def _stack_on_disk(tmp_path, n=4):
    from studio.app.optinist.dataclass.roi import RoiStack

    stack, path = RoiStack.create(str(tmp_path / "roi_mask_stack.npy"), (n, LY, LX))
    expected = np.full((n, LY, LX), np.nan)
    for i in range(n):
        stack[i, i, : i + 1] = i
        expected[i, i, : i + 1] = i
    stack.flush()
    del stack
    return RoiStack.open(path), expected


def test_roi_stack_pickles_as_a_reference(tmp_path):
    import pickle

    im, expected = _stack_on_disk(tmp_path)
    blob = pickle.dumps({"im": im})
    assert len(blob) < 1000  # the path, not n x Ly x Lx x 8 bytes
    back = pickle.loads(blob)["im"]
    np.testing.assert_array_equal(back, expected)
    assert not back.flags.writeable


def test_anything_derived_from_the_stack_pickles_its_data(tmp_path):
    import pickle

    im, expected = _stack_on_disk(tmp_path)
    head = im[:2]  # a view, as EditROI's cancel path takes
    subset = im[[0, 3]]  # a fancy-indexed copy
    grown = np.vstack((im, expected[:1]))  # what EditROI.add does
    for derived, want in [
        (head, expected[:2]),
        (subset, expected[[0, 3]]),
        (grown, np.vstack((expected, expected[:1]))),
    ]:
        back = pickle.loads(pickle.dumps(derived))
        assert type(back) is np.ndarray
        np.testing.assert_array_equal(back, want)
    # Reductions come back as plain arrays too
    assert type(np.nanmax(im, axis=0)) is np.ndarray


def test_suite2p_label_images_equal_nanmax_over_the_stack(tmp_path):
    from studio.app.optinist.wrappers.suite2p.roi import _roi_label_images

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
    labels = _roi_label_images(np, stat, iscell, LY, LX)
    with np.errstate(invalid="ignore"), pytest.warns(RuntimeWarning):
        np.testing.assert_array_equal(labels["all"], np.nanmax(stack, axis=0))
    with pytest.warns(RuntimeWarning):
        np.testing.assert_array_equal(
            labels["cell"], np.nanmax(stack[iscell != 0], axis=0)
        )
