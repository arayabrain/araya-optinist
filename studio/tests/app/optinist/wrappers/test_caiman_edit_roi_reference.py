import os
import pickle

import numpy as np
import pytest
import tifffile

from studio.app.common.dataclass import ImageData
from studio.app.optinist.core.edit_ROI.edit_ROI import CellType
from studio.app.optinist.core.edit_ROI.wrappers.caiman_edit_roi.commit import (
    commit_edit,
)
from studio.app.optinist.dataclass import EditRoiData, FluoData

pytest.importorskip("caiman")

from studio.app.optinist.wrappers.caiman.cnmf import (  # noqa: E402
    util_get_image_memmap,
)

FRAMES, LY, LX = 31, 9, 7


def rois():
    im = np.full((3, LY, LX), np.nan)
    im[0, 1:4, 2:5] = 0
    im[1, 5, :] = 1
    im[2, 1:4, 2:5] = 2
    im[2, 6:8, 0:3] = 2  # a merged ROI: two separate patches
    return im


def new_traces(images, tmp_path, name):
    # Every ROI is pending, so every trace is computed from the movie
    data = EditRoiData(images=None, im=rois())
    iscell = np.full(3, CellType.TEMP_ADD)
    info = commit_edit(
        images, data, FluoData(np.zeros((0, FRAMES))), iscell, str(tmp_path / name), "x"
    )
    return info["fluorescence"].data


@pytest.mark.parametrize("dtype", [np.uint16, np.float32])
def test_reference_gives_the_traces_caimans_copy_gave(tmp_path, dtype):
    rng = np.random.default_rng(1)
    movie = rng.normal(2000, 400, (FRAMES, LY, LX)).clip(0).astype(dtype)
    path = str(tmp_path / "images.tif")
    tifffile.imwrite(path, movie)

    # Before this change, caiman_cnmf stored its float32 memmap of the movie
    os.makedirs(tmp_path / "copy")
    copy, _, _ = util_get_image_memmap(
        "fid", ImageData(path).data, str(tmp_path / "copy" / "images.tif")
    )
    expected = new_traces(copy, tmp_path, "a")
    actual = new_traces(ImageData(path), tmp_path, "b")

    np.testing.assert_array_equal(actual, expected)
    # and the stored reference is a path, not the movie
    assert len(pickle.dumps(EditRoiData(ImageData(path), rois()))) < 5000
