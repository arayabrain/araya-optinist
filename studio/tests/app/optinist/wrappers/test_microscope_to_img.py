"""microscope_to_img keeps a reference to the converted stack in the
MicroscopeData that is also the NWB input's external_file, so node pickles,
which all carry the NWB input, no longer carry the frames."""
import copy
import os
import shutil

import numpy as np
import pytest
from pynwb import NWBHDF5IO

from studio.app.common.core.experiment.experiment_writer import ExptDataWriter
from studio.app.common.core.rules.file_writer import FileWriter
from studio.app.common.core.rules.runner import Runner
from studio.app.common.core.snakemake.smk import Rule
from studio.app.common.core.utils.config_handler import ConfigReader
from studio.app.common.core.utils.pickle_handler import PickleReader, PickleWriter
from studio.app.dir_path import CORE_PARAM_PATH
from studio.app.optinist.core.nwb.nwb_creater import save_nwb
from studio.app.optinist.dataclass import microscope as microscope_module
from studio.app.optinist.dataclass.microscope import MicroscopeData
from studio.app.optinist.wrappers.data_utils.microscope_to_img import microscope_to_img

STACK = np.random.default_rng(0).integers(0, 4096, (2, 30, 64, 48), dtype=np.uint16)
OLD_ID, NEW_ID = "aaaa1111", "bbbb2222"


@pytest.fixture
def fake_reader(monkeypatch):
    class FakeReader:
        def get_image_stacks(self):  # (ch, t, y, x)
            return STACK

    monkeypatch.setattr(MicroscopeData, "reader", property(lambda self: FakeReader()))


def run_node(rule, input_info, func):
    """Runner.run's steps around the wrapper call, then its pickle write."""
    os.makedirs(os.path.dirname(rule.output), exist_ok=True)
    input_info = Runner._Runner__align_input_info_content_keys(input_info, rule)
    nwbfile = input_info["nwbfile"]
    output_info = func(input_info, nwbfile.get("input"), os.path.dirname(rule.output))
    output_info["nwbfile"] = Runner._Runner__save_func_nwb(
        f"{os.path.splitext(rule.output)[0]}.nwb", rule.type, nwbfile, output_info
    )
    PickleWriter.write(rule.output, output_info)


@pytest.fixture
def experiment(tmp_path, fake_reader):
    """input → microscope_to_img → one node below it, run as the runner runs
    them; returns the experiment directory and its node pickles' paths."""
    root = tmp_path / "output" / "default" / OLD_ID
    pickles = {
        "input": str(root / "input_0" / "m.pkl"),
        "node": str(root / "microscope_to_img_x" / "microscope_to_img.pkl"),
        "down": str(root / "down_x" / "down.pkl"),
    }
    os.makedirs(os.path.dirname(pickles["input"]))
    rule = Rule(
        input=str(tmp_path / "m.nd2"),
        return_arg="input_0",
        params={},
        output=pickles["input"],
        type="microscope",
        nwbfile=copy.deepcopy(ConfigReader.read(CORE_PARAM_PATH.nwb.value)),
    )
    PickleWriter.write(pickles["input"], FileWriter.microscope(rule))
    run_node(
        Rule(
            [pickles["input"]],
            {"input_0@input_0": "microscope"},
            {},
            pickles["node"],
            "mti",
        ),
        {"input_0": PickleReader.read(pickles["input"])},
        lambda info, nwb, out_dir: microscope_to_img(
            info["microscope"], out_dir, params={"ch": 1}, nwbfile=nwb
        ),
    )
    run_node(
        Rule(
            [pickles["node"]],
            {"microscope_image@microscope_to_img_x": "image"},
            {},
            pickles["down"],
            "down",
        ),
        {"microscope_to_img_x": PickleReader.read(pickles["node"])},
        lambda info, nwb, out_dir: {"image_out": info["image"]},
    )
    return root, pickles


def external_file(output_info):
    return output_info["nwbfile"]["input"]["image_series"]["external_file"]


def test_the_node_pickles_carry_no_frames(experiment):
    _, pickles = experiment
    node = PickleReader.read(pickles["node"])
    assert external_file(node).image is node["microscope_image"]
    for name in ["node", "down"]:  # every pickle below carries the NWB input
        assert os.path.getsize(pickles[name]) < STACK[1].nbytes / 10


def test_data_reads_the_converted_stack(experiment):
    _, pickles = experiment
    data = external_file(PickleReader.read(pickles["down"])).data
    np.testing.assert_array_equal(data, STACK[1])
    assert data.dtype == STACK.dtype


@pytest.mark.parametrize("raw", [True, False])
def test_nwb_input_is_unchanged(experiment, tmp_path, raw):
    _, pickles = experiment
    input_config = PickleReader.read(pickles["down"])["nwbfile"]["input"]
    input_config["image_series"]["save_raw_image_to_nwb"] = raw
    path = str(tmp_path / "func.nwb")
    save_nwb(path, input_config, {})
    with NWBHDF5IO(path, "r") as io:
        series = io.read().acquisition["TwoPhotonSeries"]
        if raw:
            np.testing.assert_array_equal(series.data[:], STACK[1])
        else:
            assert list(series.external_file[:]) == [str(tmp_path / "m.nd2")]


def copy_experiment(root):
    """As ExptDataWriter.copy_data does: copytree, then the unique id rewrite."""
    copied = root.parent / NEW_ID
    shutil.copytree(root, copied)
    writer = ExptDataWriter("bucket", "default", OLD_ID)
    assert writer._ExptDataWriter__copy_data_replace_unique_id(copied, OLD_ID, NEW_ID)
    return copied


def test_experiment_copy_rewrites_the_reference(experiment):
    root, pickles = experiment
    copied = copy_experiment(root)
    shutil.rmtree(root)  # the copy must not depend on the source

    node = PickleReader.read(pickles["node"].replace(OLD_ID, NEW_ID))
    assert external_file(node).image is node["microscope_image"]
    assert f"/{NEW_ID}/" in external_file(node).image.path[0]
    down = PickleReader.read(str(copied / "down_x" / "down.pkl"))
    np.testing.assert_array_equal(external_file(down).data, STACK[1])


def test_an_older_pickle_keeps_its_frames_through_experiment_copy(tmp_path):
    """Before references, set_data(frames) stored the frames in the object."""
    root = tmp_path / "output" / "default" / OLD_ID
    older = MicroscopeData(str(root / "input_0" / "m.nd2"))
    older.__dict__["data"] = STACK[0]
    PickleWriter.write(str(root / "down_x" / "down.pkl"), {"microscope": older})

    copied = copy_experiment(root)

    restored = PickleReader.read(str(copied / "down_x" / "down.pkl"))["microscope"]
    np.testing.assert_array_equal(restored.data, STACK[0])
    assert restored.data.dtype == STACK.dtype
    assert f"/{NEW_ID}/" in restored.path


def test_a_thorlabs_archive_reaches_its_reader(tmp_path, monkeypatch):
    """ ".thor.zip" has two suffixes; ThorlabsReader takes the archive itself."""
    loaded = []

    class StubThorlabsReader:
        def load(self, path):
            loaded.append(path)

    monkeypatch.setattr(microscope_module, "ThorlabsReader", StubThorlabsReader)
    path = str(tmp_path / "input" / "1" / "scan.thor.zip")
    microscope = MicroscopeData(path)
    assert isinstance(microscope.reader, StubThorlabsReader)
    assert loaded == [path]
    assert microscope.path == path
