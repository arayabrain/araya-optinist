import h5py
import numpy as np
import pytest
from pynwb import NWBHDF5IO, validate

from studio.app.common.core.utils.config_handler import ConfigReader
from studio.app.common.dataclass import ImageData
from studio.app.dir_path import CORE_PARAM_PATH
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.core.nwb.nwb_creater import overwrite_nwb, save_nwb

FRAMES = np.arange(5 * 4 * 3, dtype=np.uint16).reshape(5, 4, 3)
NODE_CONFIG = {NWBDATASET.POSTPROCESS: {"node": {"image": np.ones((4, 3))}}}


@pytest.fixture
def node_nwb(tmp_path):
    """Write a node NWB as the node's rule does; returns its path and movie."""

    def write(save_raw_image_to_nwb):
        images = ImageData(FRAMES, output_dir=str(tmp_path), file_name="movie")
        input_config = ConfigReader.read(CORE_PARAM_PATH.nwb.value)
        input_config["image_series"]["external_file"] = images
        input_config["image_series"]["save_raw_image_to_nwb"] = save_raw_image_to_nwb
        path = tmp_path / "func" / "func.nwb"
        path.parent.mkdir()
        save_nwb(str(path), input_config, NODE_CONFIG)
        return path, images

    return write


def rewrite(path):
    """An ROI-edit commit's or the data filter's rewrite of the node NWB."""
    overwrite_nwb(NODE_CONFIG, str(path.parent), path.name)


def assert_valid(path):
    errors, status = validate(paths=[str(path)])
    assert status == 0
    assert [str(e) for e in errors if "TwoPhotonSeries" in str(e)] == []


def test_a_rewrite_keeps_the_frames_stored_with_save_raw_image_to_nwb(node_nwb):
    path, _ = node_nwb(save_raw_image_to_nwb=True)

    rewrite(path)
    rewrite(path)  # a second commit rewrites the rewritten file

    assert_valid(path)
    with NWBHDF5IO(str(path), "r") as io:
        series = io.read().acquisition["TwoPhotonSeries"]
        assert series.external_file is None
        np.testing.assert_array_equal(series.data[:], FRAMES)


def test_a_rewrite_copies_the_stored_frames_into_the_file(node_nwb):
    path, _ = node_nwb(save_raw_image_to_nwb=True)

    rewrite(path)

    # a link would lead back to the path the rewrite replaced: to itself
    with h5py.File(path, "r") as f:
        link = f["acquisition/TwoPhotonSeries"].get("data", getlink=True)
    assert isinstance(link, h5py.HardLink)


def test_a_rewrite_keeps_the_movie_reference_by_default(node_nwb):
    path, images = node_nwb(save_raw_image_to_nwb=False)

    rewrite(path)

    assert_valid(path)
    with NWBHDF5IO(str(path), "r") as io:
        series = io.read().acquisition["TwoPhotonSeries"]
        assert list(series.external_file[:]) == images.path
        assert series.data.size == 0
