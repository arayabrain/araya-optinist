import asyncio
import os
import pickle
import shutil
from types import SimpleNamespace

import numpy as np
import pytest

from studio.app.common.core.experiment.experiment import ExptConfig, ExptFunction
from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
from studio.app.common.core.experiment.experiment_writer import (
    ExptConfigWriter,
    ExptDataWriter,
)
from studio.app.common.core.storage.remote_storage_controller import RemoteStorageType
from studio.app.common.core.utils.datetime_utils import (
    get_current_datetime,
    parse_datetime_for_timezone,
)
from studio.app.common.core.workflow.workflow import Edge, Node, NodeData, RunItem
from studio.app.common.core.workflow.workflow_writer import WorkflowConfigWriter
from studio.app.const import DATE_FORMAT
from studio.app.dir_path import DIRPATH

workspace_id = "default2"
unique_id = "expt_test"

node_data = NodeData(label="a", param={}, path="", type="")

nodeDict = {
    "test1": Node(
        id="test1",
        type="a",
        data=node_data,
        position={"x": 0, "y": 0},
        style={
            "border": None,
            "borderRadius": 0,
            "height": 100,
            "padding": 0,
            "width": 180,
        },
    )
}

edgeDict = {
    "test2": Edge(
        id="test2",
        type="a",
        animated=False,
        source="",
        sourceHandle="",
        target="",
        targetHandle="",
        style={},
    )
}


def test_create_config() -> ExptConfig:
    runItem = RunItem(
        name="New Flow",
        nodeDict=nodeDict,
        edgeDict=edgeDict,
        snakemakeParam={},
        nwbParam={},
        forceRunList=[],
    )

    expt_config = ExptConfigWriter(
        workspace_id=workspace_id,
        unique_id=unique_id,
        name=runItem.name,
    ).create_config()

    assert isinstance(expt_config, ExptConfig)
    assert isinstance(expt_config.function, dict)
    assert len(expt_config.function) == 0

    assert expt_config


def test_add_run_info():
    expt_config = ExptConfigWriter(
        workspace_id="",
        unique_id="",
        name="",
    ).add_run_info()

    assert expt_config.success == "running"


def test_add_run_info_rewrites_the_recorded_timezone():
    """A rerun rewrites started_at in the new run's zone, so the recorded
    timezone must follow it: a legacy config with no timezone would otherwise
    have its local-time started_at read back as UTC."""
    existing = ExptConfigReader.create_empty_experiment_config()
    existing.timezone = None
    existing.started_at = "2020/01/01 00:00:00"

    writer = ExptConfigWriter(
        workspace_id="",
        unique_id="",
        name="",
        timezone="Asia/Tokyo",
    )
    writer.builder.set_config(existing)
    expt_config = writer.add_run_info()

    assert expt_config.timezone == "Asia/Tokyo"

    started = parse_datetime_for_timezone(
        expt_config.started_at, DATE_FORMAT, expt_config.timezone
    )
    elapsed = get_current_datetime().timestamp() - started.timestamp()
    assert abs(elapsed) < 60, f"started_at read back skewed by {elapsed} sec"


dirpath = f"{DIRPATH.OUTPUT_DIR}/{workspace_id}/{unique_id}"


def test_function_from_nodeDict():
    WorkflowConfigWriter(
        workspace_id=workspace_id,
        unique_id=unique_id,
        nodeDict=nodeDict,
        edgeDict=edgeDict,
    ).write()

    expt_config = ExptConfigWriter(
        workspace_id=workspace_id,
        unique_id=unique_id,
        name="name",
    ).build_function_from_nodeDict()

    assert isinstance(expt_config.function, dict)
    assert isinstance(expt_config.function["test1"], ExptFunction)


def test_new_write():
    if os.path.exists(dirpath):
        shutil.rmtree(dirpath)

    WorkflowConfigWriter(
        workspace_id=workspace_id,
        unique_id=unique_id,
        nodeDict=nodeDict,
        edgeDict=edgeDict,
    ).write()

    ExptConfigWriter(
        workspace_id=workspace_id,
        unique_id=unique_id,
        name="name",
    ).write()

    assert os.path.exists(f"{dirpath}/experiment.yaml")


def test_write_add():
    ExptConfigWriter(
        workspace_id=workspace_id,
        unique_id=unique_id,
        name="name",
    ).write()

    assert os.path.exists(f"{dirpath}/experiment.yaml")


def rewrite_a_copied_file(tmp_path, data, suffix, method, old_id, new_id):
    """Save data in the source, copy it, rewrite the copy, delete the source,
    and load the copy back."""
    src_file = tmp_path / old_id / "suite2p_roi" / f"suite2p_roi.{suffix}"
    src_file.parent.mkdir(parents=True)
    if suffix == "pkl":
        with open(src_file, "wb") as f:
            pickle.dump(data, f)
    else:
        np.save(src_file, data, allow_pickle=True)
    shutil.copytree(tmp_path / old_id, tmp_path / new_id)
    new_file = tmp_path / new_id / "suite2p_roi" / f"suite2p_roi.{suffix}"

    writer = ExptDataWriter("", workspace_id, old_id)
    getattr(writer, f"_ExptDataWriter__copy_data_update_{method}")(
        str(new_file), old_id, new_id
    )
    shutil.rmtree(tmp_path / old_id)

    if suffix == "pkl":
        with open(new_file, "rb") as f:
            return pickle.load(f)
    return np.load(new_file, allow_pickle=True).item()


@pytest.mark.parametrize("suffix, method", [("pkl", "pickle"), ("npy", "npy")])
def test_copy_data_rewrites_every_occurrence_of_a_shared_path(tmp_path, suffix, method):
    old_id, new_id = "aaaa1111", "bbbb2222"
    paths = [f"{tmp_path}/{old_id}/suite2p_roi/data_{i}.tif" for i in range(2)]
    cycle = []
    cycle.append(cycle)
    items = []
    tuple_cycle = (items,)
    items.append(tuple_cycle)
    self_array = np.empty((), dtype=object)
    self_array[()] = self_array
    self_dict = {}
    self_dict["self"] = self_dict
    self_node = SimpleNamespace()
    self_node.self = self_node
    data = {
        "ops": {"filelist": paths},
        "images": SimpleNamespace(path=paths),
        "copied": list(paths),
        "grid": np.array([[[p]] for p in paths]),
        "cycle": cycle,
        "tuple_cycle": tuple_cycle,
        "self_array": self_array,
        "self_dict": self_dict,
        "self_node": self_node,
        "masked": np.ma.masked_array([1.0, 2.0], mask=[False, True]),
    }

    loaded = rewrite_a_copied_file(tmp_path, data, suffix, method, old_id, new_id)
    expected = [p.replace(old_id, new_id) for p in paths]
    assert loaded["ops"]["filelist"] == expected
    assert loaded["images"].path == expected
    assert loaded["copied"] == expected
    assert loaded["grid"].ravel().tolist() == expected
    assert loaded["images"].path is loaded["ops"]["filelist"]
    assert loaded["cycle"][0] is loaded["cycle"]
    assert loaded["tuple_cycle"][0][0] is loaded["tuple_cycle"]
    assert loaded["self_array"][()] is loaded["self_array"]
    assert loaded["self_dict"]["self"] is loaded["self_dict"]
    assert loaded["self_node"].self is loaded["self_node"]
    assert loaded["masked"].mask.tolist() == [False, True]


def object_array(items, shape=None):
    array = np.empty((len(items),) if shape is None else shape, dtype=object)
    for index, item in zip(np.ndindex(array.shape), items):
        array[index] = item
    return array


OBJECT_ARRAYS = {
    # suite2p's Vmap: maps of different sizes, which np.array() rejects
    "ragged": lambda path: object_array([np.full((n, n), 1.0) for n in (8, 4, 2)]),
    # equal lengths, which np.array() merges into one float array
    "equal_length": lambda path: object_array([np.arange(3.0), np.arange(3.0, 6.0)]),
    "paths_2d": lambda path: object_array([path, path], shape=(1, 2)),
    "zero_d": lambda path: object_array([path], shape=()),
    # a masked element's raw value holds an id too
    "masked": lambda path: np.ma.masked_array(
        object_array([path, path]), mask=[False, True]
    ),
}


@pytest.mark.parametrize("suffix, method", [("pkl", "pickle"), ("npy", "npy")])
@pytest.mark.parametrize("case", OBJECT_ARRAYS)
def test_copy_data_keeps_object_arrays_as_they_are(tmp_path, suffix, method, case):
    old_id, new_id = "aaaa1111", "bbbb2222"
    path = f"{tmp_path}/{old_id}/suite2p_roi/data.bin"
    array = OBJECT_ARRAYS[case](path)

    loaded = rewrite_a_copied_file(
        tmp_path, {"reg_file": path, "array": array}, suffix, method, old_id, new_id
    )

    assert loaded["reg_file"] == path.replace(old_id, new_id)
    rewritten = loaded["array"]
    assert type(rewritten) is type(array)
    assert rewritten.dtype == object
    assert rewritten.shape == array.shape
    np.testing.assert_array_equal(
        np.ma.getmaskarray(rewritten), np.ma.getmaskarray(array)
    )
    # plain views: a masked element reads as `masked`, not as its raw value
    raw_rewritten, raw_array = rewritten.view(np.ndarray), array.view(np.ndarray)
    for index in np.ndindex(array.shape):
        expected = raw_array[index]
        if isinstance(expected, str):
            expected = expected.replace(old_id, new_id)
        assert type(raw_rewritten[index]) is type(expected)
        np.testing.assert_array_equal(raw_rewritten[index], expected)


COPY_SOURCE_ID, COPY_ID = "aaaa1111", "bbbb2222"


@pytest.fixture
def copy_source(monkeypatch):
    """A saved experiment to copy, and where its copy goes."""
    monkeypatch.setenv("REMOTE_STORAGE_TYPE", RemoteStorageType.NO_USE.value)
    source = f"{DIRPATH.OUTPUT_DIR}/{workspace_id}/{COPY_SOURCE_ID}"
    copied = f"{DIRPATH.OUTPUT_DIR}/{workspace_id}/{COPY_ID}"
    for path in (source, copied):
        shutil.rmtree(path, ignore_errors=True)
    WorkflowConfigWriter(
        workspace_id=workspace_id,
        unique_id=COPY_SOURCE_ID,
        nodeDict=nodeDict,
        edgeDict=edgeDict,
    ).write()
    ExptConfigWriter(workspace_id, COPY_SOURCE_ID, name="source").write()
    yield source, copied
    for path in (source, copied):
        shutil.rmtree(path, ignore_errors=True)


def copy_the_source():
    writer = ExptDataWriter("bucket", workspace_id, COPY_SOURCE_ID)
    return asyncio.run(writer.copy_data(COPY_ID, "copy"))


def test_copy_data_copies_a_saved_experiment(copy_source):
    source, copied = copy_source

    assert copy_the_source()

    assert os.path.isdir(copied)
    assert ExptConfigReader.read(workspace_id, COPY_ID).name == "copy"
    assert os.path.exists(source)


@pytest.mark.parametrize(
    "name", ["suite2p_roi.pkl", "suite2p_roi.pkl.orig", "ops.npy", "params.yaml"]
)
def test_a_failed_rewrite_fails_the_copy_and_removes_it(copy_source, name):
    source, copied = copy_source
    os.makedirs(f"{source}/suite2p_roi")
    with open(f"{source}/suite2p_roi/{name}", "wb") as f:
        f.write(b"\xff\xfe neither a pickle, an npy nor UTF-8")

    assert not copy_the_source()

    assert not os.path.exists(copied)
    assert os.path.exists(f"{source}/suite2p_roi/{name}")


def test_copy_data_keeps_a_destination_it_did_not_create(copy_source):
    _, copied = copy_source
    os.makedirs(copied)
    open(f"{copied}/other.txt", "w").close()

    assert not copy_the_source()

    assert os.listdir(copied) == ["other.txt"]


def test_copy_data_runs_the_file_work_off_the_event_loop(copy_source, monkeypatch):
    loops = []

    def recording(func):
        def wrapper(*args, **kwargs):
            try:
                loops.append(asyncio.get_running_loop())
            except RuntimeError:  # no event loop runs in this thread
                loops.append(None)
            return func(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(shutil, "copytree", recording(shutil.copytree))
    rewrite = "_ExptDataWriter__copy_data_replace_unique_id"
    monkeypatch.setattr(
        ExptDataWriter, rewrite, recording(getattr(ExptDataWriter, rewrite))
    )

    assert copy_the_source()

    assert loops == [None, None]
