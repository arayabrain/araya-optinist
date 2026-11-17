"""
Report the workflow processes still running in the benchmark container once the
runs have ended, and wait up to --wait seconds for them to stop. Prints JSON:
{"count_at_end": N, "count": M, "waited_s": S, "processes": [...]}, where M is
what is left after the wait. A process still running after the runs ended (a
timed-out run that is still stopping) holds memory that the row's figures
attribute to the other runs.

A workflow process is a snakemake rule job (its command line runs a script under
.snakemake/: the conda activation and the rule script) or, in start mode exec,
the driver and the snakemake parent it forks (run_workflow.py). In start mode
api the snakemake parent is a forked uvicorn worker, which the command line does
not tell apart; start_via_api.py records whether its run stopped.

--stop <unique id> ends a timed-out exec run instead: timeout's SIGTERM reaches
the run's process group and stops its rules, but the snakemake parent the driver
forked outlives it. Every process whose command line holds the id gets SIGTERM,
and SIGKILL 10 s later if still running. Stdlib only.

Usage (inside the container):
  python workflow_processes.py [--wait 120]
  python workflow_processes.py --stop <unique id>
"""

import argparse
import json
import os
import signal
import time

MARKERS = ("/.snakemake/", "run_workflow.py")


def matching(markers) -> list:
    """(pid, command line) of every other process whose command line holds one of
    the markers. A zombie has an empty command line, so it never matches."""
    found = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        if int(pid) == os.getpid():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                args = f.read().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue  # exited meanwhile
        args = args.strip()
        # Padded, so that " --unique-id r-1 " does not match r-10's
        if any(marker in f" {args} " for marker in markers):
            found.append((int(pid), args))
    return found


def workflow_processes() -> list:
    return [f"{pid} {args[:200]}" for pid, args in matching(MARKERS)]


def stop(unique_id: str) -> int:
    """SIGTERM, then SIGKILL after 10 s, to every process of one run."""
    markers = [f" --unique-id {unique_id} "]
    targets = [pid for pid, _ in matching(markers)]
    for sig, grace in ((signal.SIGTERM, 10), (signal.SIGKILL, 0)):
        for pid in targets:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        deadline = time.time() + grace
        while matching(markers) and time.time() < deadline:
            time.sleep(0.5)
    return len(targets)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait", type=float, default=0, help="seconds")
    parser.add_argument("--stop", metavar="UNIQUE_ID")
    args = parser.parse_args()

    if args.stop:
        print(json.dumps({"stopped": stop(args.stop)}))
        return

    t0 = time.time()
    at_end = remaining = workflow_processes()
    while remaining and time.time() - t0 < args.wait:
        time.sleep(1)
        remaining = workflow_processes()
    print(
        json.dumps(
            {
                "count_at_end": len(at_end),
                "count": len(remaining),
                "waited_s": round(time.time() - t0, 1) if at_end else 0,
                "processes": (remaining or at_end)[:10],
            }
        )
    )


if __name__ == "__main__":
    main()
