import os

import pytest
import yaml

from studio.app.dir_path import DIRPATH
from studio.app.wrappers import wrapper_dict

SETUP_CONDA = wrapper_dict["maintenance"]["setup_conda"]
SAMPLE_DIR = os.path.join(DIRPATH.ROOT_DIR, "sample_data", "maintenance")
SAMPLE_INPUT_DIR = os.path.join(SAMPLE_DIR, "input")
SAMPLE_OUTPUT_DIR = os.path.join(SAMPLE_DIR, "output")


def _leaf_wrappers(tree):
    for value in tree.values():
        if "function" in value:
            yield value
        else:
            yield from _leaf_wrappers(value)


def _declared_conda_names():
    return sorted(
        {w["conda_name"] for w in _leaf_wrappers(wrapper_dict) if w.get("conda_name")}
    )


def _read_yaml(uid, filename):
    with open(os.path.join(SAMPLE_OUTPUT_DIR, uid, filename)) as f:
        return yaml.safe_load(f)


@pytest.mark.parametrize("conda_name", _declared_conda_names())
def test_every_conda_env_has_a_setup_conda_wrapper(conda_name):
    """The frontend's "Create env" dialog runs setup_conda_<conda_name>."""
    uid = f"setup_conda_{conda_name}"

    assert uid in SETUP_CONDA
    assert SETUP_CONDA[uid]["conda_name"] == conda_name


@pytest.mark.parametrize("uid", sorted(SETUP_CONDA))
def test_every_setup_conda_wrapper_has_a_sample_workflow(uid):
    """The dialog imports and reproduces the sample workflow named <uid>."""
    path = f"maintenance/setup_conda/{uid}"

    experiment = _read_yaml(uid, "experiment.yaml")
    assert experiment["unique_id"] == uid

    workflow = _read_yaml(uid, "workflow.yaml")
    nodes = workflow["nodeDict"]
    (input_id,) = [k for k, n in nodes.items() if n["data"]["type"] == "input"]
    (algo_id,) = [k for k, n in nodes.items() if n["data"]["path"] == path]
    assert [nodes[input_id]["id"], nodes[algo_id]["id"]] == [input_id, algo_id]

    # The image input must reach the algorithm node under its current id.
    ((edge_key, edge),) = workflow["edgeDict"].items()
    assert edge_key == edge["id"]
    assert (edge["source"], edge["target"], edge["targetHandle"]) == (
        input_id,
        algo_id,
        f"{algo_id}--image--ImageData",
    )

    for filename in nodes[input_id]["data"]["path"]:
        assert os.path.isfile(os.path.join(SAMPLE_INPUT_DIR, filename))

    rules = _read_yaml(uid, "snakemake.yaml")["rules"].values()
    assert [(r["type"], r["path"]) for r in rules if r["path"]] == [(uid, path)]
