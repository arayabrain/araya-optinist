"""microscope_to_img keeps a reference to the converted stack in the
MicroscopeData that is also the NWB input's external_file, so node pickles,
which all carry the NWB input, no longer carry the frames."""
import copy
import pickle

import numpy as np
import pytest
from pynwb import NWBHDF5IO

from studio.app.common.core.rules.file_writer import FileWriter
from studio.app.common.core.snakemake.smk import Rule
from studio.app.common.core.utils.config_handler import ConfigReader
from studio.app.dir_path import CORE_PARAM_PATH
from studio.app.optinist.core.nwb.nwb_creater import save_nwb
from studio.app.optinist.dataclass.microscope import MicroscopeData
from studio.app.optinist.wrappers.data_utils.microscope_to_img import microscope_to_img

STACK = np.random.default_rng(0).integers(0, 4096, (2, 30, 64, 48), dtype=np.uint16)


@pytest.fixture
def fake_reader(monkeypatch):
    class FakeReader:
        def get_image_stacks(self):  # (ch, t, y, x)
            return STACK

    monkeypatch.setattr(MicroscopeData, "reader", property(lambda self: FakeReader()))


@pytest.fixture
def ran(tmp_path, fake_reader):
    """The input node's pickle, read back, then microscope_to_img on it, as the
    runner does; returns the node's output with the NWB input it carries."""
    rule = Rule(
        input=str(tmp_path / "m.nd2"),
        return_arg="input_0",
        params={},
        output=str(tmp_path / "input_0" / "m.pkl"),
        type="microscope",
        nwbfile=copy.deepcopy(ConfigReader.read(CORE_PARAM_PATH.nwb.value)),
    )
    data = pickle.loads(pickle.dumps(FileWriter.microscope(rule)))
    output_dir = tmp_path / "output" / "default" / "uid" / "microscope_to_img_x"
    output_dir.mkdir(parents=True)
    nwbfile = data["nwbfile"]
    info = microscope_to_img(
        data["input_0"], str(output_dir), params={"ch": 1}, nwbfile=nwbfile["input"]
    )
    return {**info, "nwbfile": nwbfile}


def test_the_node_pickle_carries_no_frames(ran):
    external_file = ran["nwbfile"]["input"]["image_series"]["external_file"]
    assert external_file.image is ran["microscope_image"]
    assert len(pickle.dumps(ran)) < STACK[1].nbytes / 10


def test_data_reads_the_converted_stack(ran):
    external_file = ran["nwbfile"]["input"]["image_series"]["external_file"]
    np.testing.assert_array_equal(external_file.data, STACK[1])
    assert external_file.data.dtype == STACK.dtype


@pytest.mark.parametrize("raw", [True, False])
def test_nwb_input_is_unchanged(ran, tmp_path, raw):
    input_config = ran["nwbfile"]["input"]
    input_config["image_series"]["save_raw_image_to_nwb"] = raw
    path = str(tmp_path / "func.nwb")
    save_nwb(path, input_config, {})
    with NWBHDF5IO(path, "r") as io:
        series = io.read().acquisition["TwoPhotonSeries"]
        if raw:
            np.testing.assert_array_equal(series.data[:], STACK[1])
        else:
            assert list(series.external_file[:]) == [str(tmp_path / "m.nd2")]


def test_an_older_pickle_keeps_its_frames():
    """Before references, set_data stored the frames in the object."""
    older = MicroscopeData("m.nd2")
    older.__dict__["data"] = STACK[0]
    restored = pickle.loads(pickle.dumps(older))
    np.testing.assert_array_equal(restored.data, STACK[0])
    # experiment copy sets every attribute it rewrites
    setattr(restored, "data", STACK[1])
    np.testing.assert_array_equal(restored.data, STACK[1])
