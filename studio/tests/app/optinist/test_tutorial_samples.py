"""The tutorial samples carry the shipped default params, so a reproduced
tutorial runs what a newly added node would run.
"""
from pathlib import Path

import pytest

from studio.app.common.core.utils.config_handler import ConfigReader
from studio.app.common.core.workflow.workflow_params import (
    nest2dict,
    read_default_params,
)
from studio.app.dir_path import DIRPATH

TUTORIAL_DIR = Path(DIRPATH.ROOT_DIR) / "sample_data" / "tutorial" / "output"

# values chosen for a tutorial's own data, not left over from an older yaml
DELIBERATE = {
    ("tutorial4", "cca"): {("I/O", "target_index")},
}


def _leaves(tree, prefix=()):
    for key, value in tree.items():
        if isinstance(value, dict):
            yield from _leaves(value, prefix + (key,))
        else:
            yield prefix + (key,), value


def _algorithm_nodes():
    for workflow in sorted(TUTORIAL_DIR.glob("*/workflow.yaml")):
        for node in ConfigReader.read(str(workflow))["nodeDict"].values():
            if node["data"]["type"] == "algorithm":
                yield workflow.parent.name, node


NODES = list(_algorithm_nodes())
NODE_IDS = [f"{tutorial}-{node['data']['label']}" for tutorial, node in NODES]


def test_every_tutorial_sample_is_checked():
    tutorials = {p.name for p in TUTORIAL_DIR.iterdir() if p.is_dir()}
    assert tutorials
    assert {tutorial for tutorial, _ in NODES} == tutorials


@pytest.mark.parametrize("tutorial, node", NODES, ids=NODE_IDS)
def test_tutorial_node_params_match_the_shipped_yaml(tutorial, node):
    label = node["data"]["label"]
    saved = dict(_leaves(nest2dict(node["data"]["param"])))
    default = dict(_leaves(read_default_params(label)))

    # a key missing here is filled in, and reported, when the tutorial is reproduced
    assert saved.keys() == default.keys()
    differing = {path for path in default if saved[path] != default[path]}
    assert differing == DELIBERATE.get((tutorial, label), set()), (
        f"{tutorial} {label}: set the sample to the yaml's values, "
        "or list a value chosen for the tutorial's data in DELIBERATE"
    )


@pytest.mark.parametrize("tutorial, node", NODES, ids=NODE_IDS)
def test_tutorial_snakemake_rule_matches_its_workflow_node(tutorial, node):
    rules = ConfigReader.read(str(TUTORIAL_DIR / tutorial / "snakemake.yaml"))
    assert rules["rules"][node["id"]]["params"] == nest2dict(node["data"]["param"])
