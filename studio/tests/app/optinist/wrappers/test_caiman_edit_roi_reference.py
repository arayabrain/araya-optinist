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
from studio.app.optinist.dataclass.roi import open_edit_images
from studio.app.optinist.wrappers.caiman.cnmf import util_edit_roi_movie

FRAMES = 31


def caiman_copy(movie, tmp_path):
    """caiman_cnmf's float32 copy of the movie, laid out as util_get_image_memmap
    lays it out: pixels x time in C order, viewed as (T, Y, X) in F order."""
    T, dims = len(movie), movie.shape[1:]
    mm = np.memmap(
        tmp_path / "copy.mmap",
        mode="w+",
        dtype=np.float32,
        shape=(int(np.prod(dims)), T),
        order="C",
    )
    copy = np.reshape(mm.T, [T] + list(dims), order="F")
    copy[:] = movie
    return copy


def rois(ly, lx):
    im = np.full((4, ly, lx), np.nan)
    im[0, 1:4, 2:5] = 0
    im[1, 5, :] = 1
    im[2, 1:4, 2:5] = 2
    im[2, 6:8, 0:3] = 2  # a merged ROI: two separate patches
    im[3] = 3  # the whole frame: long enough for pairwise summation to matter
    return im


def new_traces(images, im, tmp_path, name):
    # Every ROI is pending, so every trace is computed from the movie
    data = EditRoiData(images=None, im=im)
    iscell = np.full(len(im), CellType.TEMP_ADD)
    info = commit_edit(
        images, data, FluoData(np.zeros((0, FRAMES))), iscell, str(tmp_path / name), "x"
    )
    return info["fluorescence"].data


# Value ranges a recording uses, wide enough that float32 sums round, so the
# traces depend on the summation order and not only on the values
RANGES = {
    np.uint16: (500, 60000),
    np.int16: (-3000, 30000),
    np.uint8: (0, 255),
    np.float32: (-1000, 60000),
}


@pytest.mark.parametrize("dtype", list(RANGES))
@pytest.mark.parametrize("shape", [(9, 7), (128, 128), (100, 101)])
def test_reference_gives_the_traces_caimans_copy_gave(tmp_path, dtype, shape):
    rng = np.random.default_rng(1)
    movie = rng.uniform(*RANGES[dtype], (FRAMES, *shape)).astype(dtype)
    path = str(tmp_path / "images.tif")
    tifffile.imwrite(path, movie)
    copy = caiman_copy(movie, tmp_path)
    im = rois(*shape)

    # develop-main's commit, on the float32 copy caiman_cnmf stored then
    expected = [np.mean(copy[:, ~np.isnan(roi)], axis=1) for roi in im]

    # a movie in the experiment is now read through a reference, one outside it
    # through the same copy as before
    for name, images in [("ref", ImageData(path)), ("copy", copy)]:
        np.testing.assert_array_equal(
            new_traces(images, im, tmp_path, name), expected, err_msg=name
        )


def test_the_copy_here_is_laid_out_as_caiman_cnmfs(tmp_path):
    pytest.importorskip("caiman")
    from studio.app.optinist.wrappers.caiman.cnmf import util_get_image_memmap

    movie = np.random.default_rng(2).normal(200, 40, (FRAMES, 9, 7))
    os.makedirs(tmp_path / "caiman")
    copy, _, _ = util_get_image_memmap("fid", movie, str(tmp_path / "caiman" / "m.tif"))
    np.testing.assert_array_equal(caiman_copy(movie, tmp_path), copy)


def test_a_movie_in_the_experiment_is_kept_as_a_reference(tmp_path):
    experiment = tmp_path / "output" / "default" / "uid"
    frames = np.arange(48, dtype=np.uint16).reshape(3, 4, 4)
    movie = ImageData(
        frames, output_dir=str(experiment / "caiman_mc_x"), file_name="mc_images"
    )
    copy = frames.astype(np.float32)
    node_dir = str(experiment / "caiman_cnmf_x")

    kept = util_edit_roi_movie(movie, copy, node_dir)

    assert isinstance(kept, ImageData)
    assert len(pickle.dumps(kept)) < 1000  # a path, not the movie
    np.testing.assert_array_equal(open_edit_images(kept, node_dir), frames)


def test_a_reference_never_builds_the_fallback_copy(tmp_path):
    experiment = tmp_path / "output" / "default" / "uid"
    movie = ImageData(
        np.zeros((3, 4, 4), np.uint16),
        output_dir=str(experiment / "caiman_mc_x"),
        file_name="mc_images",
    )

    def build_copy():
        raise AssertionError("the fallback copy was built")

    kept = util_edit_roi_movie(movie, build_copy, str(experiment / "caiman_x"))

    assert isinstance(kept, ImageData)


def test_a_movie_outside_the_experiment_builds_the_fallback_copy(tmp_path):
    movie = ImageData(
        np.zeros((3, 4, 4), np.uint16),
        output_dir=str(tmp_path / "input" / "default"),
        file_name="m",
    )
    copy = np.zeros((3, 4, 4), np.uint16)
    output_dir = tmp_path / "output" / "default" / "uid" / "caiman_x"
    assert util_edit_roi_movie(movie, lambda: copy, str(output_dir)) is copy


@pytest.mark.parametrize("where", ["input/default", "output/default/input_tiff/abc"])
def test_a_movie_outside_the_experiment_keeps_caimans_copy(tmp_path, where):
    # an input file wired straight in, or the HDF5 / MAT / NWB input_tiff cache
    movie = ImageData(
        np.zeros((3, 4, 4), np.uint16), output_dir=str(tmp_path / where), file_name="m"
    )
    copy = np.zeros((3, 4, 4), np.float32)
    output_dir = tmp_path / "output" / "default" / "uid" / "caiman_cnmf_x"
    assert util_edit_roi_movie(movie, copy, str(output_dir)) is copy
