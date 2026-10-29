import os

import numpy as np
import pytest
from pynwb import NWBHDF5IO

from studio.app.common.core.utils.config_handler import ConfigReader
from studio.app.common.dataclass import ImageData
from studio.app.dir_path import CORE_PARAM_PATH
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.core.nwb.nwb_creater import (
    overwrite_nwb,
    overwrite_nwbfile,
    save_nwb,
)


def config_with(value):
    return {NWBDATASET.POSTPROCESS: {"node": {"image": np.full((4, 3), value)}}}


def rewrite_whole_nwb(path, config):
    """A terminal node's, an ROI-edit commit's or a data filter's whole.nwb update."""
    overwrite_nwbfile(str(path), config)


def rewrite_node_nwb(path, config):
    """An ROI-edit commit's or a data filter's node-NWB rewrite."""
    overwrite_nwb(config, str(path.parent), path.name)


# each rewrite, with the NWBHDF5IO method that writes its new file
REWRITES = pytest.mark.parametrize(
    "rewrite, method",
    [(rewrite_whole_nwb, "export"), (rewrite_node_nwb, "write")],
    ids=["whole_nwb", "node_nwb"],
)


@pytest.fixture
def nwb_path(tmp_path):
    movie = np.zeros((3, 4, 3), np.uint16)
    input_config = ConfigReader.read(CORE_PARAM_PATH.nwb.value)
    input_config["image_series"]["external_file"] = ImageData(
        movie, output_dir=str(tmp_path), file_name="movie"
    )
    path = tmp_path / "func" / "func.nwb"
    path.parent.mkdir()
    save_nwb(str(path), input_config, config_with(1.0))
    return path


def node_image(path):
    with NWBHDF5IO(str(path), "r") as io:
        return io.read().processing["optinist"]["node_image"].data[:]


@REWRITES
def test_a_rewrite_renames_the_new_file_over_the_old_one(nwb_path, rewrite, method):
    old_inode = os.stat(nwb_path).st_ino

    rewrite(nwb_path, config_with(2.0))

    # a new inode: the old file was not written in place, so no reader and no
    # crash mid-update meets a partly written file
    assert os.stat(nwb_path).st_ino != old_inode
    assert os.listdir(nwb_path.parent) == [nwb_path.name]
    np.testing.assert_array_equal(node_image(nwb_path), np.full((4, 3), 2.0))


@REWRITES
def test_a_failed_rewrite_leaves_the_old_file_and_no_temporary(
    nwb_path, rewrite, method, monkeypatch
):
    def fail(self, *args, **kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(NWBHDF5IO, method, fail)

    with pytest.raises(OSError, match="no space left"):
        rewrite(nwb_path, config_with(2.0))

    assert os.listdir(nwb_path.parent) == [nwb_path.name]
    np.testing.assert_array_equal(node_image(nwb_path), np.full((4, 3), 1.0))
