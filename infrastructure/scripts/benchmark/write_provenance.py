"""
Write provenance.json for one benchmark row: the configuration the result was
produced under (#893, "configuration provenance"). Host side, stdlib only.
"""

import argparse
import hashlib
import json
import os
import platform
import subprocess
from datetime import datetime, timezone


def sh(*cmd, cwd=None) -> str:
    try:
        return subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        return ""


def input_sha256(path: str) -> str:
    """Identifies the exact input; seconds per GB, recomputed for every row."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    p = argparse.ArgumentParser()
    for name in [
        "out",
        "repo",
        "run-id",
        "algo",
        "input",
        "variant",
        "conc",
        "memory",
        "cpu",
        "image",
        "uvicorn-workers",
        "sample-interval",
        "probe",
        "timeout",
        "t-runs-start",
        "t-runs-end",
        "container-state",
    ]:
        p.add_argument(f"--{name}", default="")
    p.add_argument("--overrides", nargs="*", default=[])
    a = p.parse_args()

    docker_info = sh("docker", "info", "--format", "{{.MemTotal}} {{.NCPU}}").split()
    provenance = {
        "run_id": a.run_id,
        "lane": "local",
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git": {
            "sha": sh("git", "rev-parse", "HEAD", cwd=a.repo),
            "branch": sh("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=a.repo),
            "dirty_files": sh(
                "git", "status", "--porcelain", "--untracked-files=no", cwd=a.repo
            ).splitlines(),
        },
        "workflow": {
            "algo": a.algo,
            "fixture": f"infrastructure/scripts/benchmark/fixtures/{a.algo}.json",
            "param_overrides": a.overrides,
            "params": "shipped defaults unless overridden (see workflow-N.yaml)",
        },
        "input": {
            "name": os.path.basename(a.input),
            "bytes": os.path.getsize(a.input),
            "sha256": input_sha256(a.input),
        },
        "container": {
            "image": a.image,
            "image_id": sh("docker", "image", "inspect", "-f", "{{.Id}}", a.image),
            "variant": a.variant,
            "memory_flags": a.memory or "none",
            "cpu_flags": a.cpu,
            "uvicorn_workers": int(a.uvicorn_workers or 0),
            "state_after_runs": a.container_state,
        },
        "concurrency": int(a.conc or 1),
        "timeout_s": int(a.timeout or 0),
        "sampling": {
            "cgroup_interval_s": float(a.sample_interval or 0),
            "probe": a.probe,
            "per_rule": "ru_maxrss + /proc/self/io at rule end (exact, not sampled)",
        },
        "timing": {
            "t_runs_start": int(a.t_runs_start or 0),
            "t_runs_end": int(a.t_runs_end or 0),
            "wall_clock_comparable_with_production": False,
        },
        "conda_envs": "local volume (production: EFS, pre-#95)",
        "host": {
            "machine": platform.machine(),
            "cpu": sh("sysctl", "-n", "machdep.cpu.brand_string"),
            "ram_bytes": int(sh("sysctl", "-n", "hw.memsize") or 0),
            "docker_vm_mem_bytes": int(docker_info[0]) if docker_info else None,
            "docker_vm_cpus": int(docker_info[1]) if len(docker_info) > 1 else None,
            "emulation": "linux/amd64 image on arm64 host",
        },
    }
    with open(a.out, "w") as f:
        json.dump(provenance, f, indent=2)


if __name__ == "__main__":
    main()
