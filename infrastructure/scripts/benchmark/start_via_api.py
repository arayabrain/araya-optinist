"""
Start one benchmark run through POST /run, as the web UI does, and wait for it
(start mode api).

The API's uvicorn worker builds the run and becomes its snakemake parent, as in
a deployed task. This process imports no application code (stdlib and PyYAML
only), so it adds little to the container's memory; BENCH_RESULT records it.
It polls the run's experiment.yaml, not /run/result, so the API does no
result-polling work a browser would cause. On SIGTERM (the run's timeout) it
cancels the run through the API until the run has stopped; BENCH_RESULT records
whether it did (still_running).

Usage (inside the container; the RunItem from run_workflow.py --print-run-item):
  python infrastructure/scripts/benchmark/start_via_api.py \
      --run-item /bench_out/runitem-1.json --workspace bench
"""

import argparse
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request

import yaml
from process_memory import own_memory_mb


class Cancelled(Exception):
    pass


# How long a timed-out run is given to stop; later, the row records it as
# still running (the container is removed when the row ends)
STOP_DEADLINE_S = 120


def post(url: str, body: bytes = b"", timeout: float = 120):
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def read_experiment(path: str) -> dict:
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        # Not written yet, or caught mid-write; the next poll reads it again
        return {}


def is_running(experiment: dict) -> bool:
    return experiment.get("success") in (None, "running")


def stop(api: str, workspace: str, unique_id: str, experiment_path: str):
    """Cancel a timed-out run until it has stopped, for up to STOP_DEADLINE_S.
    POST /run/cancel stops the rule process running now and answers 404 while
    none runs (between rules, during conda activation), so it is repeated
    until the run's record is final. Returns (any cancel accepted, record)."""
    deadline = time.time() + STOP_DEADLINE_S
    accepted = False
    experiment = read_experiment(experiment_path)
    while is_running(experiment) and time.time() < deadline:
        try:
            url = f"{api}/run/cancel/{workspace}/{unique_id}"
            accepted = bool(post(url, timeout=10)) or accepted
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
        experiment = read_experiment(experiment_path)
    return accepted, experiment


def on_sigterm(signum, frame):
    raise Cancelled()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-item", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--poll", type=float, default=1.0, help="seconds")
    args = parser.parse_args()

    with open(args.run_item, "rb") as f:
        run_item = f.read()
    signal.signal(signal.SIGTERM, on_sigterm)

    # A timeout during submission takes effect once the run's id is known, so
    # an accepted run can still be cancelled
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
    t_start = time.time()
    unique_id = post(f"{args.api}/run/{args.workspace}", run_item)
    print(f"BENCH_UID {unique_id}", flush=True)
    experiment_path = os.path.join(
        os.environ["OPTINIST_DIR"],
        "output",
        args.workspace,
        unique_id,
        "experiment.yaml",
    )

    experiment = {}
    cancelled = still_running = None
    try:
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})
        while is_running(experiment):
            time.sleep(args.poll)
            experiment = read_experiment(experiment_path)
        success = experiment["success"]
    except Cancelled:
        t_end = time.time()
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        cancelled, experiment = stop(
            args.api, args.workspace, unique_id, experiment_path
        )
        still_running = is_running(experiment)
        success = "timeout"
    else:
        t_end = time.time()

    functions = experiment.get("function") or {}
    result = {
        "unique_id": unique_id,
        "success": success,
        "functions": {k: (v or {}).get("success") for k, v in functions.items()},
        "t_start": round(t_start, 3),
        "t_end": round(t_end, 3),
        "elapsed_s": round(t_end - t_start, 1),
        "start": "api",
        "harness_process": own_memory_mb(),
    }
    if cancelled is not None:
        result["cancelled"] = cancelled
        result["still_running"] = still_running
        result["t_stopped"] = round(time.time(), 3)
    print("BENCH_RESULT " + json.dumps(result), flush=True)
    if success == "timeout":
        return 124
    return 0 if success == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
