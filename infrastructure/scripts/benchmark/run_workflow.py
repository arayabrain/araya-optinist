"""
Run one benchmark workflow headlessly, through the same path as POST /run.

Runs inside the benchmark container (see run_bench.sh), so the workflow shares
the API's cgroup as it does in production. POST /run itself cannot be used in
standalone mode, so this builds the RunItem and calls WorkflowRunner directly.

Algorithm parameters are the shipped defaults, plus --set overrides
(dotted path into the default yaml, e.g. advanced.patch_params.n_processes=4).

Usage (inside the container):
  python infrastructure/scripts/benchmark/run_workflow.py \
      --fixture infrastructure/scripts/benchmark/fixtures/suite2p.json \
      --input neurofinder.00.10_stack.tif --workspace bench --unique-id r3 \
      [--set caiman_cnmf.advanced.patch_params.n_processes=4]
"""

import argparse
import json
import sys
import time

import yaml
from fastapi import BackgroundTasks

from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
from studio.app.common.core.workflow.workflow import RunItem
from studio.app.common.core.workflow.workflow_params import read_default_params
from studio.app.common.core.workflow.workflow_runner import WorkflowRunner

STYLE = {"border": None, "height": None, "padding": None, "width": None}


def dict2nest(params: dict, prefix: str = "") -> dict:
    """Inverse of workflow_params.nest2dict: the shape the frontend sends."""
    nested = {}
    for key, value in params.items():
        path = f"{prefix}/{key}" if prefix else key
        if isinstance(value, dict):
            nested[key] = {"type": "parent", "children": dict2nest(value, path)}
        else:
            nested[key] = {"type": "child", "value": value, "path": path}
    return nested


def apply_override(params: dict, dotted: str, raw_value: str) -> None:
    keys = dotted.split(".")
    target = params
    for key in keys[:-1]:
        target = target[key]
    if keys[-1] not in target:
        raise KeyError(f"override path not in defaults: {dotted}")
    target[keys[-1]] = yaml.safe_load(raw_value)


def build_run_item(fixture: dict, input_filename: str, overrides: dict) -> RunItem:
    node_dict = {
        "input_bench": {
            "id": "input_bench",
            "type": "ImageFileNode",
            "data": {
                "label": input_filename,
                "param": {},
                "path": [input_filename],
                "type": "input",
                "fileType": "image",
            },
            "position": {"x": 0, "y": 0},
            "style": STYLE,
        }
    }
    for node in fixture["nodes"]:
        params = read_default_params(node["label"]) or {}
        for dotted, value in overrides.get(node["label"], {}).items():
            apply_override(params, dotted, value)
        node_dict[node["id"]] = {
            "id": node["id"],
            "type": "AlgorithmNode",
            "data": {
                "label": node["label"],
                "param": dict2nest(params),
                "path": node["path"],
                "type": "algorithm",
            },
            "position": {"x": 0, "y": 0},
            "style": STYLE,
        }

    edge_dict = {}
    for src, src_arg, src_type, dst, dst_arg, dst_type in fixture["edges"]:
        edge_id = f"edge-{src}-{dst}-{dst_arg}"
        edge_dict[edge_id] = {
            "id": edge_id,
            "type": "buttonedge",
            "animated": False,
            "source": src,
            "sourceHandle": f"{src}--{src_arg}--{src_type}",
            "target": dst,
            "targetHandle": f"{dst}--{dst_arg}--{dst_type}",
            "style": STYLE,
        }

    return RunItem(
        name=fixture["name"],
        nodeDict=node_dict,
        edgeDict=edge_dict,
        snakemakeParam={},
        nwbParam={},
        forceRunList=[],
    )


def parse_overrides(items) -> dict:
    """['caiman_cnmf.a.b=4'] -> {'caiman_cnmf': {'a.b': '4'}}"""
    overrides = {}
    for item in items or []:
        path, value = item.split("=", 1)
        label, dotted = path.split(".", 1)
        overrides.setdefault(label, {})[dotted] = value
    return overrides


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--input", required=True, help="filename in input/<ws>/")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--unique-id", required=True)
    parser.add_argument("--set", action="append", dest="overrides")
    args = parser.parse_args()

    with open(args.fixture) as f:
        fixture = json.load(f)
    overrides = parse_overrides(args.overrides)
    run_item = build_run_item(fixture, args.input, overrides)

    t_start = time.time()
    runner = WorkflowRunner(
        remote_bucket_name="",
        workspace_id=args.workspace,
        unique_id=args.unique_id,
        runItem=run_item,
    )
    background_tasks = BackgroundTasks()
    runner.run_workflow(background_tasks)
    # Same call the API makes after the response; run it in the foreground here
    for task in background_tasks.tasks:
        task.func(*task.args, **task.kwargs)
    t_end = time.time()

    expt = ExptConfigReader.read(args.workspace, args.unique_id)
    result = {
        "unique_id": args.unique_id,
        "success": expt.success,
        "functions": {k: v.success for k, v in expt.function.items()},
        "overrides": overrides,
        "t_start": round(t_start, 3),
        "t_end": round(t_end, 3),
        "elapsed_s": round(t_end - t_start, 1),
    }
    print("BENCH_RESULT " + json.dumps(result), flush=True)
    return 0 if expt.success == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
