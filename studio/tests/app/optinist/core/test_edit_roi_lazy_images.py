import importlib
import pickle
import sys
import types

import numpy as np
import pytest

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


def test_open_edit_images_memory_maps_a_reference(movie_ref):
    images, ref = movie_ref
    opened = open_edit_images(ref)
    assert isinstance(opened, np.memmap)
    assert not opened.flags.writeable
    np.testing.assert_array_equal(opened, images)


def test_open_edit_images_passes_arrays_through():
    # Pickles written before the change carry the movie itself
    images = movie()
    assert open_edit_images(images) is images
    assert open_edit_images(None) is None


@pytest.mark.parametrize(
    "module", ["lccd_edit_roi", "vacant_roi_edit_roi", "caiman_edit_roi"]
)
def test_commit_traces_match_between_array_and_reference(movie_ref, tmp_path, module):
    commit_edit = importlib.import_module(
        f"studio.app.optinist.core.edit_ROI.wrappers.{module}"
    ).commit_edit
    images, ref = movie_ref

    def state():
        im = np.full((3, LY, LX), np.nan)
        im[0, 0:2, :] = 0
        im[1, 3, 1:4] = 1
        im[2, 0:2, :] = 2  # a pending merge, appended
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
    # The appended (merged) ROI gets a new trace: the mean over its pixels
    expected = images[:, 0:2, :].reshape(FRAMES, -1).mean(axis=1)
    np.testing.assert_allclose(from_ref["fluorescence"].data[2], expected)


@pytest.fixture
def output_dir(tmp_path):
    d = tmp_path / "output" / "default" / "uid" / "func"
    d.mkdir(parents=True)
    return str(d)


@pytest.fixture
def lccd_stub(monkeypatch):
    from studio.app.optinist.wrappers.lccd import lccd_detection

    class FakeLCCD:
        def __init__(self, params):
            pass

        def apply(self, D):
            roi = np.zeros((D.shape[0] * D.shape[1], 2))
            roi[:4, 0] = 1
            roi[4:8, 1] = 1
            return roi

    fake = types.ModuleType("lccd")
    fake.LCCD = FakeLCCD
    monkeypatch.setitem(
        sys.modules, f"{lccd_detection.__package__}.lccd_python.lccd", fake
    )


def _run_wrapper(name, images, output_dir):
    params = {"f0_frames": 5, "f0_percentile": 8}
    if name == "vacant_roi":
        from studio.app.optinist.wrappers.data_utils.vacant_roi import vacant_roi

        return vacant_roi(images, output_dir, params=params)
    from studio.app.optinist.wrappers.lccd.lccd_detection import lccd_detect

    return lccd_detect(images, output_dir, params=params)


@pytest.mark.parametrize("name", ["vacant_roi", "lccd"])
def test_wrappers_store_a_reference_not_the_movie(
    name, tmp_path, output_dir, lccd_stub
):
    # Square frames, as these wrappers' PIL-based loader expects
    frames = np.random.default_rng(0).random((40, 8, 8)).astype(np.float32)
    ref = ImageData(frames, output_dir=str(tmp_path), file_name="mc_images")
    info = _run_wrapper(name, ref, output_dir)
    stored = info["edit_roi_data"].images
    assert stored is ref
    # The pickled EditRoiData carries a path, not FRAMES x LY x LX pixels
    assert len(pickle.dumps(stored)) < 1000
