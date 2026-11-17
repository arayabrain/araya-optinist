"""
Build one benchmark workflow's RunItem, and run it headlessly (start mode exec).

Runs inside the benchmark container (see run_bench.sh). In start mode exec this
process calls WorkflowRunner itself and waits for the run, so the snakemake
parent is this process, an extra app-sized process in the container's memory
that a deployed run does not have; BENCH_RESULT records its own memory.
--print-run-item only prints the RunItem, for start_via_api.py (start mode api).

Algorithm parameters are the shipped defaults, plus --set overrides
(<node label>.<dotted path into its default yaml>=<value>, e.g.
caiman_cnmf.advanced.patch_params.n_processes=4). The label nwb overrides the
NWB defaults (e.g. nwb.imaging_plane.imaging_rate=7).

Usage (inside the container):
  python infrastructure/scripts/benchmark/run_workflow.py \
      --fixture infrastructure/scripts/benchmark/fixtures/suite2p.json \
      --input neurofinder.00.10_stack.tif --workspace bench \
      (--unique-id r3 | --print-run-item) \
      [--set caiman_cnmf.advanced.patch_params.n_processes=4]
"""

import argparse
import json
import sys
import time

import yaml
from fastapi import BackgroundTasks
from process_memory import own_memory_mb

from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
from studio.app.common.core.workflow.workflow import RunItem
from studio.app.common.core.workflow.workflow_params import (
    dict2nest,
    read_default_params,
)
from studio.app.common.core.workflow.workflow_runner import WorkflowRunner

STYLE = {"border": None, "height": None, "padding": None, "width": None}


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

    # --set nwb.<dotted>=<value> overrides the NWB defaults (e.g. the imaging rate)
    nwb_param = {}
    if "nwb" in overrides:
        nwb = read_default_params("nwb")
        for dotted, value in overrides["nwb"].items():
            apply_override(nwb, dotted, value)
        nwb_param = dict2nest(nwb)

    return RunItem(
        name=fixture["name"],
        nodeDict=node_dict,
        edgeDict=edge_dict,
        snakemakeParam={},
        nwbParam=nwb_param,
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
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--unique-id")
    mode.add_argument("--print-run-item", action="store_true")
    parser.add_argument("--set", action="append", dest="overrides")
    args = parser.parse_args()

    with open(args.fixture) as f:
        fixture = json.load(f)
    overrides = parse_overrides(args.overrides)
    run_item = build_run_item(fixture, args.input, overrides)
    if args.print_run_item:
        print(run_item.json(), flush=True)
        return 0

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
        "start": "exec",
        "harness_process": own_memory_mb(),
    }
    print("BENCH_RESULT " + json.dumps(result), flush=True)
    return 0 if expt.success == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
