import importlib
import os
import pickle
import shutil
import sys
import types

import imageio
import numpy as np
import pytest
import tifffile

from studio.app.common.dataclass.image import ImageData
from studio.app.optinist.core.edit_ROI.edit_ROI import CellType
from studio.app.optinist.dataclass import EditRoiData, FluoData
from studio.app.optinist.dataclass.roi import (
    EditImagesUnavailableError,
    edit_reference,
    open_edit_images,
)

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
    opened = open_edit_images(ref, "unused/node")  # an absolute path stays
    assert isinstance(opened, np.memmap)
    assert not opened.flags.writeable
    np.testing.assert_array_equal(opened, images)


def test_open_edit_images_passes_arrays_through():
    # Pickles written before the change carry the movie itself
    images = movie()
    assert open_edit_images(images, "unused/node") is images
    assert open_edit_images(None, "unused/node") is None


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


def square_frames():
    # Square frames, as these wrappers' PIL-based loader expects
    return np.random.default_rng(0).random((40, 8, 8)).astype(np.float32)


@pytest.mark.parametrize("name", ["vacant_roi", "lccd"])
def test_wrappers_store_a_reference_to_a_movie_in_the_experiment(
    name, output_dir, lccd_stub
):
    # An upstream node's output, e.g. caiman_mc's, in the same experiment
    upstream = os.path.join(os.path.dirname(output_dir), "caiman_mc_x")
    ref = ImageData(square_frames(), output_dir=upstream, file_name="mc_images")
    info = _run_wrapper(name, ref, output_dir)
    stored = info["edit_roi_data"].images
    # relative to the experiment, so it survives a copy or a moved OPTINIST_DIR
    assert stored.path == [
        os.path.join("caiman_mc_x", "tiff", "mc_images", "mc_images.tif")
    ]
    # The pickled EditRoiData carries a path, not FRAMES x LY x LX pixels
    assert len(pickle.dumps(stored)) < 1000
    np.testing.assert_array_equal(open_edit_images(stored, output_dir), square_frames())


@pytest.mark.parametrize("name", ["vacant_roi", "lccd"])
def test_wrappers_keep_the_frames_of_a_movie_outside_the_experiment(
    name, tmp_path, output_dir, lccd_stub
):
    # An input file (replaceable by a same-name upload) or the workspace's
    # input_tiff cache (not synced with the experiment)
    frames = square_frames()
    for where in [tmp_path / "input" / "default", tmp_path / "output" / "default"]:
        ref = ImageData(frames, output_dir=str(where / "input_tiff"), file_name="m")
        stored = _run_wrapper(name, ref, output_dir)["edit_roi_data"].images
        assert isinstance(stored, np.ndarray)
        np.testing.assert_array_equal(stored, frames)


def test_a_movie_that_cannot_be_memory_mapped_is_read_once(tmp_path, monkeypatch):
    images = movie()
    path = str(tmp_path / "zlib.tif")
    tifffile.imwrite(path, images, compression="zlib")

    def no_concatenated_copy(self):
        raise AssertionError("read through ImageData.data")

    monkeypatch.setattr(ImageData, "data", property(no_concatenated_copy))
    opened = open_edit_images(ImageData([path]), str(tmp_path / "node"))
    np.testing.assert_array_equal(opened, images)


def test_a_mount_that_cannot_memory_map_reads_the_movie(tmp_path, monkeypatch):
    images = movie()
    path = str(tmp_path / "m.tif")
    tifffile.imwrite(path, images)

    def no_mmap(*args, **kwargs):
        raise OSError(19, "No such device")  # ENODEV: mmap unsupported here

    monkeypatch.setattr(tifffile, "memmap", no_mmap)
    opened = open_edit_images(ImageData([path]), str(tmp_path / "node"))
    np.testing.assert_array_equal(opened, images)


def test_a_missing_movie_names_the_file(tmp_path):
    ref = ImageData([str(tmp_path / "caiman_mc_x" / "mc_images.tif")])
    with pytest.raises(EditImagesUnavailableError, match="mc_images.tif"):
        open_edit_images(ref, str(tmp_path / "node"))


@pytest.mark.parametrize(
    "module", ["lccd_edit_roi", "vacant_roi_edit_roi", "caiman_edit_roi"]
)
def test_a_delete_only_commit_does_not_open_the_movie(tmp_path, module):
    commit_edit = importlib.import_module(
        f"studio.app.optinist.core.edit_ROI.wrappers.{module}"
    ).commit_edit
    gone = ImageData([str(tmp_path / "deleted.tif")])
    im = np.full((2, LY, LX), np.nan)
    im[0, 0, :] = 0
    im[1, 3, :] = 1
    data = EditRoiData(images=None, im=im)
    data.temp_delete_roi[1] = None
    fluo = FluoData(np.arange(2 * FRAMES, dtype=float).reshape(2, FRAMES))
    iscell = np.array([CellType.ROI, CellType.TEMP_DELETE])

    info = commit_edit(gone, data, fluo, iscell, str(tmp_path), "x")

    np.testing.assert_array_equal(info["fluorescence"].data, fluo.data)
    assert list(info["iscell"].data) == [CellType.ROI, CellType.NON_ROI]


@pytest.mark.parametrize(
    "module", ["lccd_edit_roi", "vacant_roi_edit_roi", "caiman_edit_roi"]
)
def test_a_commit_on_a_node_without_rois_opens_the_reference(
    movie_ref, tmp_path, module
):
    # With no traces the frame count comes from the movie, which must be opened
    # first: ImageData itself has no shape
    commit_edit = importlib.import_module(
        f"studio.app.optinist.core.edit_ROI.wrappers.{module}"
    ).commit_edit
    _, ref = movie_ref
    data = EditRoiData(images=None, im=np.zeros((0, LY, LX)))
    no_traces = FluoData(np.zeros((0, FRAMES)))  # stored as (0, 0)

    info = commit_edit(ref, data, no_traces, np.array([]), str(tmp_path), "x")

    assert info["fluorescence"].data.size == 0


def test_experiment_copy_keeps_the_reference_working(tmp_path, lccd_stub):
    from studio.app.common.core.experiment.experiment_writer import ExptDataWriter

    old_exp = tmp_path / "output" / "default" / "aaaa1111"
    upstream = ImageData(
        square_frames(), output_dir=str(old_exp / "caiman_mc_x"), file_name="mc"
    )
    info = _run_wrapper("lccd", upstream, str(old_exp / "lccd_x"))
    # As Runner stores it: the upstream node's nwbfile, holding the same object
    info["nwbfile"] = {"caiman_mc_x": {"mc_data": upstream}, "lccd_x": {}}
    for name in ["lccd.pkl", "lccd.pkl.orig"]:  # .orig: the data filter's backup
        with open(old_exp / "lccd_x" / name, "wb") as f:
            pickle.dump(info, f)

    new_exp = tmp_path / "output" / "default" / "bbbb2222"
    shutil.copytree(old_exp, new_exp)
    writer = object.__new__(ExptDataWriter)
    pickle_path = str(new_exp / "lccd_x" / "lccd.pkl")
    writer._ExptDataWriter__copy_data_update_pickle(pickle_path, "aaaa1111", "bbbb2222")
    shutil.rmtree(old_exp)  # the copy must not depend on the source

    for name in ["lccd.pkl", "lccd.pkl.orig"]:  # .orig left as is: relative either way
        with open(new_exp / "lccd_x" / name, "rb") as f:
            copied = pickle.load(f)["edit_roi_data"].images
        opened = open_edit_images(copied, str(new_exp / "lccd_x"))
        np.testing.assert_array_equal(opened, square_frames())


def test_a_movie_rewritten_since_the_node_ran_is_refused(output_dir, lccd_stub):
    # e.g. an upstream re-run that rewrote mc_images.tif, then stopped before
    # this node ran again
    upstream = os.path.join(os.path.dirname(output_dir), "caiman_mc_x")
    ref = ImageData(square_frames(), output_dir=upstream, file_name="mc_images")
    stored = _run_wrapper("lccd", ref, output_dir)["edit_roi_data"].images
    tifffile.imwrite(ref.path[0], square_frames()[::-1].copy())  # same size

    with pytest.raises(EditImagesUnavailableError, match="has changed"):
        open_edit_images(stored, output_dir)


@pytest.mark.parametrize(
    "movie_side, node_side", [("efs", "mounted"), ("mounted", "efs"), ("mounted",) * 2]
)
def test_an_experiment_reached_through_a_symlink_is_still_the_experiment(
    tmp_path, lccd_stub, movie_side, node_side
):
    (tmp_path / "efs" / "output" / "default" / "uid" / "lccd_x").mkdir(parents=True)
    os.symlink(tmp_path / "efs", tmp_path / "mounted")

    def experiment(side):
        return tmp_path / side / "output" / "default" / "uid"

    ref = ImageData(
        square_frames(),
        output_dir=str(experiment(movie_side) / "caiman_mc_x"),
        file_name="m",
    )
    node_dir = str(experiment(node_side) / "lccd_x")

    stored = _run_wrapper("lccd", ref, node_dir)["edit_roi_data"].images
    # the path within the experiment, however either side reaches it
    assert stored.path == [os.path.join("caiman_mc_x", "tiff", "m", "m.tif")]
    np.testing.assert_array_equal(open_edit_images(stored, node_dir), square_frames())


def test_a_trailing_slash_on_the_node_directory_changes_nothing(output_dir):
    upstream = os.path.join(os.path.dirname(output_dir), "caiman_mc_x")
    ref = ImageData(square_frames(), output_dir=upstream, file_name="mc_images")
    stored = edit_reference(ref, output_dir + os.sep)
    assert stored is not None
    np.testing.assert_array_equal(
        open_edit_images(stored, output_dir + os.sep), square_frames()
    )


@pytest.mark.parametrize("frame", [0, 32, 63])  # first MiB, sampled middle, last MiB
def test_a_rewrite_anywhere_in_a_large_movie_is_refused(output_dir, frame):
    # 64 frames of 64 KiB: 4 MiB, more than the MiB read from each end
    frames = np.random.default_rng(0).random((64, 128, 128)).astype(np.float32)
    upstream = os.path.join(os.path.dirname(output_dir), "caiman_mc_x")
    ref = ImageData(frames.copy(), output_dir=upstream, file_name="mc_images")
    stored = edit_reference(ref, output_dir)
    np.testing.assert_array_equal(open_edit_images(stored, output_dir), frames)

    frames[frame] += 1
    tifffile.imwrite(ref.path[0], frames)  # same size
    with pytest.raises(EditImagesUnavailableError, match="has changed"):
        open_edit_images(stored, output_dir)


def test_a_reference_to_several_files_reads_them_all(output_dir, lccd_stub):
    upstream = os.path.join(os.path.dirname(output_dir), "split_x")
    frames = square_frames()
    paths = [
        ImageData(part, output_dir=upstream, file_name=f"part{i}").path[0]
        for i, part in enumerate([frames[:25], frames[25:]])
    ]
    stored = _run_wrapper("lccd", ImageData(paths), output_dir)["edit_roi_data"].images
    assert len(stored.path) == 2
    np.testing.assert_array_equal(open_edit_images(stored, output_dir), frames)

    # a rewrite of either file is refused
    tifffile.imwrite(paths[1], frames[25:][::-1].copy())
    with pytest.raises(EditImagesUnavailableError, match="has changed"):
        open_edit_images(stored, output_dir)


def test_several_files_are_mapped_and_concatenated_once(tmp_path, monkeypatch):
    # reading each file whole and then concatenating held the movie twice
    frames = np.random.default_rng(1).integers(0, 4000, (12, LY, LX)).astype(np.int16)
    paths = [str(tmp_path / name) for name in ["a.tif", "b.tif", "c.tif"]]
    tifffile.imwrite(paths[0], frames[:5])
    tifffile.imwrite(paths[1], frames[5:7], compression="zlib")  # cannot be mapped
    tifffile.imwrite(paths[2], frames[7:])
    read_whole = []
    volread = imageio.volread

    def recorded(path, *args, **kwargs):
        read_whole.append(os.path.basename(path))
        return volread(path, *args, **kwargs)

    monkeypatch.setattr(imageio, "volread", recorded)
    monkeypatch.setattr(
        ImageData, "data", property(lambda self: pytest.fail("ImageData.data"))
    )
    opened = open_edit_images(ImageData(paths), str(tmp_path / "node"))
    np.testing.assert_array_equal(opened, frames)
    assert read_whole == ["b.tif"]


def test_paths_on_another_drive_keep_the_frames(output_dir, lccd_stub, monkeypatch):
    # os.path.commonpath raises for paths on different Windows drives
    def other_drive(paths):
        raise ValueError("Paths don't have the same drive")

    monkeypatch.setattr(os.path, "commonpath", other_drive)
    ref = ImageData(square_frames(), output_dir=output_dir + "_movie", file_name="m")
    stored = _run_wrapper("lccd", ref, output_dir)["edit_roi_data"].images
    assert isinstance(stored, np.ndarray)


def test_the_error_survives_the_trip_back_from_the_commit_worker():
    error = EditImagesUnavailableError("The movie ... is missing (mc_images.tif)")
    assert str(pickle.loads(pickle.dumps(error))) == str(error)
