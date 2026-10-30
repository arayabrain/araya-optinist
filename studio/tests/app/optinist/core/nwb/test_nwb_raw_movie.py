import numpy as np
import pytest
import tifffile
from pynwb import NWBHDF5IO, validate

from studio.app.common.core.utils.config_handler import ConfigReader
from studio.app.common.dataclass import ImageData
from studio.app.common.dataclass import image as image_module
from studio.app.dir_path import CORE_PARAM_PATH
from studio.app.optinist.core.nwb import nwb_creater
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.core.nwb.nwb_creater import save_nwb

H, W = 4, 3
NODE_CONFIG = {NWBDATASET.POSTPROCESS: {"node": {"image": np.ones((H, W))}}}


@pytest.fixture(autouse=True)
def two_frame_batches(monkeypatch):
    """Batches of 2 frames of the largest dtype here, so 5 frames take 3."""
    batch = 2 * H * W * np.dtype(np.float32).itemsize
    monkeypatch.setattr(image_module, "BATCH_BYTES", batch)
    monkeypatch.setattr(nwb_creater, "BATCH_BYTES", batch, raising=False)


def tiffs(tmp_path, dtype, frames_per_file):
    """One TIFF stack per entry, and the movie they make together."""
    rng = np.random.default_rng(0)
    paths, parts = [], []
    for i, frames in enumerate(frames_per_file):
        part = (rng.random((frames, H, W)) * 1000).astype(dtype)
        path = str(tmp_path / f"part{i}.tif")
        tifffile.imwrite(path, part)
        paths.append(path)
        parts.append(part)
    return paths, np.concatenate(parts)


def write_raw(tmp_path, external_file):
    """A node NWB written with save_raw_image_to_nwb on; returns its path."""
    input_config = ConfigReader.read(CORE_PARAM_PATH.nwb.value)
    input_config["image_series"]["external_file"] = external_file
    input_config["image_series"]["save_raw_image_to_nwb"] = True
    path = str(tmp_path / "func.nwb")
    save_nwb(path, input_config, NODE_CONFIG)
    return path


def stored_movie(path):
    with NWBHDF5IO(path, "r") as io:
        series = io.read().acquisition["TwoPhotonSeries"]
        assert series.external_file is None
        return series.data[:]


@pytest.mark.parametrize("dtype", [np.uint16, np.float32])
@pytest.mark.parametrize("frames_per_file", [[5], [3, 2]], ids=["one", "two_files"])
def test_the_raw_movie_is_stored_as_the_input_frames(tmp_path, dtype, frames_per_file):
    paths, movie = tiffs(tmp_path, dtype, frames_per_file)

    path = write_raw(tmp_path, ImageData(paths, ""))

    errors, status = validate(paths=[path])
    assert status == 0
    assert [str(e) for e in errors if "TwoPhotonSeries" in str(e)] == []
    stored = stored_movie(path)
    assert stored.dtype == movie.dtype
    np.testing.assert_array_equal(stored, movie)


def test_the_raw_movie_is_never_read_whole(tmp_path, monkeypatch):
    paths, movie = tiffs(tmp_path, np.uint16, [3, 2])

    def whole_read(self):
        raise AssertionError("the movie was read whole")

    monkeypatch.setattr(ImageData, "data", property(whole_read))

    np.testing.assert_array_equal(
        stored_movie(write_raw(tmp_path, ImageData(paths, ""))), movie
    )


def test_an_input_that_is_not_a_tiff_stack_is_read_whole(tmp_path):
    class Frames:
        """An input such as a microscope file: frames through `data` only."""

        path = str(tmp_path / "movie.nd2")
        data = np.arange(5 * H * W, dtype=np.uint16).reshape(5, H, W)

    np.testing.assert_array_equal(
        stored_movie(write_raw(tmp_path, Frames())), Frames.data
    )
