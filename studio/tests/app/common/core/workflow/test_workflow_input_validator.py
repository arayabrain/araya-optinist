"""An HDF5/Matlab dataset wired into an input of an incompatible rank must be
refused at submission with a message naming the dataset, not fail inside the
target node. Shapes come from the per-workspace structure cache first (works
for remote-only inputs), then the local file, else the check is skipped.
"""
import json
import os
import shutil
import uuid
from types import SimpleNamespace

import h5py
import numpy as np
import pytest
import scipy.io

from studio.app.common.core.auth.auth_dependencies import (
    get_current_user,
    get_user_remote_bucket_name,
)
from studio.app.common.core.utils.filepath_creater import join_filepath
from studio.app.common.core.workflow import workflow_input_validator as validator
from studio.app.common.core.workflow.workflow import Edge, Node, NodeData
from studio.app.common.core.workflow.workflow_input_validator import (
    WorkflowValidationError,
    validate_input_edges,
)
from studio.app.common.routers import run as run_router
from studio.app.const import MetadataCacheFile
from studio.app.dir_path import DIRPATH

STYLE = {"border": None, "borderRadius": 0, "height": 100, "padding": 0, "width": 180}


@pytest.fixture
def workspace():
    ws = f"validator_{uuid.uuid4().hex[:8]}"
    os.makedirs(join_filepath([DIRPATH.INPUT_DIR, ws]))
    yield ws
    shutil.rmtree(join_filepath([DIRPATH.INPUT_DIR, ws]), ignore_errors=True)
    shutil.rmtree(join_filepath([DIRPATH.OUTPUT_DIR, ws]), ignore_errors=True)


def _write_cache(ws, cache_file, file_name, dataset, shape):
    tree = [
        {
            "isDir": True,
            "name": "g",
            "path": "g",
            "nodes": [
                {
                    "isDir": False,
                    "name": "data",
                    "path": dataset,
                    "nodes": [],
                    "shape": shape,
                }
            ],
        }
    ]
    with open(join_filepath([DIRPATH.INPUT_DIR, ws, cache_file]), "w") as f:
        json.dump({file_name: tree}, f)


def _node(node_id, node_type, label, path, **extra):
    return Node(
        id=node_id,
        type=node_type,
        data=NodeData(label=label, param={}, path=path, type="", **extra),
        position={"x": 0, "y": 0},
        style=STYLE,
    )


def _edge(source, source_type, target, arg, arg_type):
    return Edge(
        id="e",
        type="buttonedge",
        animated=False,
        source=source,
        sourceHandle=f"{source}--hdf5--{source_type}",
        target=target,
        targetHandle=f"{target}--{arg}--{arg_type}",
        style=STYLE,
    )


def _graph(ws, shape, target_label, target_path, arg, arg_type, file_name="f.h5"):
    _write_cache(ws, MetadataCacheFile.HDF5_STRUCTURE, file_name, "g/data", shape)
    nodes = {
        "in": _node("in", "HDF5FileNode", file_name, file_name, hdf5Path="g/data"),
        "algo": _node("algo", "AlgorithmNode", target_label, target_path),
    }
    edges = {"e": _edge("in", "HDF5Data", "algo", arg, arg_type)}
    return nodes, edges


def test_2d_into_image_arg_is_refused_with_a_useful_message(workspace):
    nodes, edges = _graph(
        workspace,
        [50, 7],
        "suite2p_file_convert",
        "suite2p/suite2p_file_convert",
        "image",
        "ImageData",
    )
    with pytest.raises(WorkflowValidationError) as exc:
        validate_input_edges(workspace, nodes, edges)
    msg = str(exc.value)
    assert "g/data" in msg and "(50, 7)" in msg
    assert "suite2p_file_convert.image expects ImageData" in msg


def test_3d_into_image_arg_passes(workspace):
    nodes, edges = _graph(
        workspace,
        [500, 128, 128],
        "suite2p_file_convert",
        "suite2p/suite2p_file_convert",
        "image",
        "ImageData",
    )
    validate_input_edges(workspace, nodes, edges)


@pytest.mark.parametrize("shape", [[50, 7], [7]])
def test_1d_and_2d_into_fluo_arg_pass(workspace, shape):
    nodes, edges = _graph(
        workspace, shape, "fluo_from_hdf5", "utils/fluo_from_hdf5", "fluo", "FluoData"
    )
    validate_input_edges(workspace, nodes, edges)


def test_3d_into_fluo_arg_is_refused(workspace):
    nodes, edges = _graph(
        workspace,
        [500, 128, 128],
        "fluo_from_hdf5",
        "utils/fluo_from_hdf5",
        "fluo",
        "FluoData",
    )
    with pytest.raises(WorkflowValidationError, match="expects FluoData"):
        validate_input_edges(workspace, nodes, edges)


def test_basedata_arg_is_skipped(workspace):
    nodes, edges = _graph(
        workspace,
        [500, 128, 128],
        "data_transpose",
        "utils/data_transpose",
        "data",
        "BaseData",
    )
    validate_input_edges(workspace, nodes, edges)


def test_unknown_function_and_maintenance_nodes_are_skipped(workspace):
    for path in ("nope/not_a_node", "maintenance/setup_conda/setup_conda_suite2p"):
        nodes, edges = _graph(workspace, [50, 7], "x", path, "image", "ImageData")
        validate_input_edges(workspace, nodes, edges)


def test_missing_cache_and_file_is_skipped(workspace):
    nodes = {
        "in": _node("in", "HDF5FileNode", "f.h5", "f.h5", hdf5Path="g/data"),
        "algo": _node(
            "algo",
            "AlgorithmNode",
            "suite2p_file_convert",
            "suite2p/suite2p_file_convert",
        ),
    }
    edges = {"e": _edge("in", "HDF5Data", "algo", "image", "ImageData")}
    validate_input_edges(workspace, nodes, edges)


def test_local_file_is_read_when_cache_is_missing(workspace):
    with h5py.File(join_filepath([DIRPATH.INPUT_DIR, workspace, "f.h5"]), "w") as f:
        f["g/data"] = np.zeros((50, 7))
    nodes = {
        "in": _node("in", "HDF5FileNode", "f.h5", "f.h5", hdf5Path="g/data"),
        "algo": _node(
            "algo",
            "AlgorithmNode",
            "suite2p_file_convert",
            "suite2p/suite2p_file_convert",
        ),
    }
    edges = {"e": _edge("in", "HDF5Data", "algo", "image", "ImageData")}
    with pytest.raises(WorkflowValidationError):
        validate_input_edges(workspace, nodes, edges)


def test_local_matlab_file_is_read_when_cache_is_missing(workspace):
    scipy.io.savemat(
        join_filepath([DIRPATH.INPUT_DIR, workspace, "f.mat"]),
        {"data": {"behavior": np.zeros((50, 7))}},
    )
    nodes = {
        "in": _node("in", "MatlabFileNode", "f.mat", "f.mat", matPath="data/behavior"),
        "algo": _node(
            "algo",
            "AlgorithmNode",
            "suite2p_file_convert",
            "suite2p/suite2p_file_convert",
        ),
    }
    edges = {"e": _edge("in", "MatlabData", "algo", "image", "ImageData")}
    with pytest.raises(WorkflowValidationError, match=r"\(50, 7\)"):
        validate_input_edges(workspace, nodes, edges)


def test_a_path_escaping_the_workspace_is_not_opened(workspace):
    other = f"{workspace}_other"
    os.makedirs(join_filepath([DIRPATH.INPUT_DIR, other]))
    try:
        with h5py.File(join_filepath([DIRPATH.INPUT_DIR, other, "f.h5"]), "w") as f:
            f["g/data"] = np.zeros((50, 7))
        nodes = {
            "in": _node(
                "in", "HDF5FileNode", "f.h5", f"../{other}/f.h5", hdf5Path="g/data"
            ),
            "algo": _node(
                "algo",
                "AlgorithmNode",
                "suite2p_file_convert",
                "suite2p/suite2p_file_convert",
            ),
        }
        edges = {"e": _edge("in", "HDF5Data", "algo", "image", "ImageData")}
        validate_input_edges(workspace, nodes, edges)
    finally:
        shutil.rmtree(join_filepath([DIRPATH.INPUT_DIR, other]), ignore_errors=True)


def test_matlab_uses_its_own_cache(workspace):
    _write_cache(workspace, MetadataCacheFile.MAT_STRUCTURE, "f.mat", "g/data", [50, 7])
    nodes = {
        "in": _node("in", "MatlabFileNode", "f.mat", "f.mat", matPath="g/data"),
        "algo": _node(
            "algo",
            "AlgorithmNode",
            "suite2p_file_convert",
            "suite2p/suite2p_file_convert",
        ),
    }
    edges = {"e": _edge("in", "MatlabData", "algo", "image", "ImageData")}
    with pytest.raises(WorkflowValidationError, match="MatlabFileNode"):
        validate_input_edges(workspace, nodes, edges)


def test_run_route_returns_422_and_writes_no_experiment(client, workspace, monkeypatch):
    async def no_quota_check(user_id):
        return None

    monkeypatch.setattr(run_router, "_check_storage_quota", no_quota_check)
    overrides = client.app.dependency_overrides
    previous_user = overrides.get(get_current_user)
    overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    overrides[get_user_remote_bucket_name] = lambda: ""
    try:
        _write_cache(
            workspace, MetadataCacheFile.HDF5_STRUCTURE, "f.h5", "g/data", [50, 7]
        )
        body = {
            "name": "bad",
            "nodeDict": {
                "in": {
                    "id": "in",
                    "type": "HDF5FileNode",
                    "data": {
                        "label": "f.h5",
                        "param": {},
                        "path": "f.h5",
                        "type": "input",
                        "hdf5Path": "g/data",
                    },
                    "position": {"x": 0, "y": 0},
                    "style": STYLE,
                },
                "algo": {
                    "id": "algo",
                    "type": "AlgorithmNode",
                    "data": {
                        "label": "suite2p_file_convert",
                        "param": {},
                        "path": "suite2p/suite2p_file_convert",
                        "type": "algorithm",
                    },
                    "position": {"x": 0, "y": 0},
                    "style": STYLE,
                },
            },
            "edgeDict": {
                "e": {
                    "id": "e",
                    "type": "buttonedge",
                    "animated": False,
                    "source": "in",
                    "sourceHandle": "in--hdf5--HDF5Data",
                    "target": "algo",
                    "targetHandle": "algo--image--ImageData",
                    "style": STYLE,
                }
            },
            "snakemakeParam": {},
            "nwbParam": {},
            "forceRunList": [],
        }

        response = client.post(f"/run/{workspace}", json=body)

        assert response.status_code == 422
        assert "expects ImageData" in response.json()["detail"]
        assert not os.path.exists(join_filepath([DIRPATH.OUTPUT_DIR, workspace]))
    finally:
        overrides.pop(get_user_remote_bucket_name, None)
        overrides[get_current_user] = previous_user


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "node_type,expected",
    [
        ("HDF5FileNode", [MetadataCacheFile.HDF5_STRUCTURE]),
        ("MatlabFileNode", [MetadataCacheFile.MAT_STRUCTURE]),
        ("ImageFileNode", []),
    ],
)
async def test_only_the_caches_a_workflow_can_need_are_fetched(
    workspace, monkeypatch, node_type, expected
):
    """A tiff-only workflow must not pay for an S3 round trip."""
    calls = []

    async def record(bucket, ws, cache_file):
        calls.append(cache_file)

    monkeypatch.setattr(validator, "download_structure_cache", record)
    nodes = {"in": _node("in", node_type, "f", "f")}

    await validator.ensure_structure_caches("bucket", workspace, nodes)

    assert calls == expected
