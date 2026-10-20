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


class PathBackedStack(np.memmap):
    """Pickles only the path of the .npy file it maps."""

    @classmethod
    def open(cls, path):
        obj = np.load(path, mmap_mode="r").view(cls)
        obj.stack_path = path
        return obj

    def __reduce__(self):
        return (PathBackedStack.open, (self.stack_path,))


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
    stack_path = f"{tmp_path}/{old_id}/suite2p_roi/roi_mask_stack.npy"
    os.makedirs(os.path.dirname(stack_path))
    np.save(stack_path, np.arange(6.0).reshape(1, 2, 3))
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
        "stack": PathBackedStack.open(stack_path),
    }

    src_file = tmp_path / old_id / "suite2p_roi" / f"suite2p_roi.{suffix}"
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
            loaded = pickle.load(f)
    else:
        loaded = np.load(new_file, allow_pickle=True).item()
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
    assert loaded["stack"].stack_path == stack_path.replace(old_id, new_id)
    assert loaded["stack"].tolist() == np.arange(6.0).reshape(1, 2, 3).tolist()
