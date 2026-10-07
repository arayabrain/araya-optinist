import json
import os
import shutil
import uuid

import h5py
import numpy as np
import pytest

from studio.app.common.routers.files import (
    STRUCTURE_VERSION_KEY,
    _structure_node_to_dict,
)
from studio.app.const import MetadataCacheFile
from studio.app.dir_path import DIRPATH
from studio.app.optinist.routers.hdf5 import HDF5Getter, _dict_to_hdf5_node
from studio.app.optinist.schemas.hdf5 import HDF5Node

input_filepath = "files/test.nwb"
workspace_id = "1"


def test_hdf5(client):
    response = client.get(f"/hdf5/{input_filepath}?workspace_id={workspace_id}")
    data = response.json()

    assert response.status_code == 200
    assert isinstance(data, list)
    assert isinstance(data[0], dict)


def test_HDF5Getter():
    output = HDF5Getter.get(f"{DIRPATH.DATA_DIR}/input/1/files/test.nwb")

    assert isinstance(output, list)
    assert isinstance(output[0], HDF5Node)


TABLE_DTYPE = [
    ("time", "f8"),
    ("lick", "i4"),
    ("moving", "?"),
    ("label", "S4"),
    ("ratio", "c16"),
    ("a/b", "f8"),
    ("vec", "f8", (3,)),
]


def _table_file(path):
    with h5py.File(path, "w") as f:
        f["g/table"] = np.zeros(5, dtype=TABLE_DTYPE)
    return path


def test_compound_dataset_lists_its_numeric_columns(tmp_path):
    output = HDF5Getter.get(str(_table_file(tmp_path / "t.h5")))

    table_node = output[0].nodes[0]
    assert table_node.isDir is True
    assert table_node.path == "g/table"
    assert [n.name for n in table_node.nodes] == ["time", "lick", "moving"]
    assert all(n.path == f"g/table/{n.name}" for n in table_node.nodes)
    assert all(n.shape == (5,) for n in table_node.nodes)


def test_compound_dataset_without_loadable_columns_is_not_listed(tmp_path):
    with h5py.File(tmp_path / "t.h5", "w") as f:
        f["g/labels"] = np.zeros(5, dtype=[("name", "S4"), ("code", "S2")])
        f["g/data"] = np.zeros(5)

    output = HDF5Getter.get(str(tmp_path / "t.h5"))

    assert [n.name for n in output[0].nodes] == ["data"]


def test_compound_tree_survives_the_cache_round_trip(tmp_path):
    output = HDF5Getter.get(str(_table_file(tmp_path / "t.h5")))

    cached = json.loads(json.dumps([_structure_node_to_dict(n) for n in output]))
    restored = [_dict_to_hdf5_node(n) for n in cached]

    table_node = restored[0].nodes[0]
    assert table_node.isDir is True
    assert [n.path for n in table_node.nodes] == [
        "g/table/time",
        "g/table/lick",
        "g/table/moving",
    ]
    assert table_node.nodes[0].shape == (5,)


@pytest.fixture
def workspace():
    ws = f"hdf5_{uuid.uuid4().hex[:8]}"
    os.makedirs(f"{DIRPATH.INPUT_DIR}/{ws}")
    yield ws
    shutil.rmtree(f"{DIRPATH.INPUT_DIR}/{ws}", ignore_errors=True)


def _old_cache(ws, *file_names):
    leaf = {"isDir": False, "name": "table", "path": "g/table", "shape": [5]}
    tree = [{"isDir": True, "name": "g", "path": "g", "nodes": [leaf]}]
    cache = f"{DIRPATH.INPUT_DIR}/{ws}/{MetadataCacheFile.HDF5_STRUCTURE}"
    with open(cache, "w") as f:
        json.dump({name: tree for name in file_names}, f)
    return cache


def test_pre_version_cache_is_rebuilt_once_for_every_local_file(client, workspace):
    _table_file(f"{DIRPATH.INPUT_DIR}/{workspace}/t.h5")
    _table_file(f"{DIRPATH.INPUT_DIR}/{workspace}/u.h5")
    cache = _old_cache(workspace, "t.h5", "u.h5", "remote.h5")

    data = client.get(f"/hdf5/t.h5?workspace_id={workspace}").json()

    table_node = data[0]["nodes"][0]
    assert table_node["isDir"] is True
    assert [n["name"] for n in table_node["nodes"]] == ["time", "lick", "moving"]
    with open(cache) as f:
        rebuilt = json.load(f)
    assert rebuilt[STRUCTURE_VERSION_KEY] == 2
    assert rebuilt["u.h5"][0]["nodes"][0]["isDir"] is True
    assert rebuilt["remote.h5"][0]["nodes"][0]["isDir"] is False


def test_pre_version_cache_is_served_as_is_for_a_remote_only_file(client, workspace):
    _old_cache(workspace, "remote.h5")

    data = client.get(f"/hdf5/remote.h5?workspace_id={workspace}").json()

    assert data[0]["nodes"][0] == {
        "isDir": False,
        "name": "table",
        "path": "g/table",
        "nodes": None,
        "shape": [5],
        "nbytes": None,
        "dataType": None,
    }
