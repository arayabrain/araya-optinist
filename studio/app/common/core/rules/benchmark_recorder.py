"""
Per-rule resource recording for the workflow benchmark (#893).

Active only when OPTINIST_BENCHMARK=1. Appends one JSON line per rule to
<workflow dir>/bench/rules.jsonl. Standard library only, because rule scripts
run inside the algorithm conda environments.

Peak memory is read from the kernel's high-water mark (ru_maxrss), not sampled,
so short allocation spikes are not averaged away.
"""

import json
import os
import resource
import socket
import time
from contextlib import contextmanager
from pathlib import Path

ENV_FLAG = "OPTINIST_BENCHMARK"
OUTPUT_DIRNAME = "bench"
OUTPUT_FILENAME = "rules.jsonl"


def is_enabled() -> bool:
    return os.environ.get(ENV_FLAG) == "1"


def _process_start_epoch() -> float:
    """Start time of this process (epoch seconds), from /proc."""
    with open("/proc/self/stat") as f:
        # Field 22 (starttime, clock ticks since boot); comm may contain spaces
        fields = f.read().rsplit(")", 1)[1].split()
    start_ticks = int(fields[19])
    with open("/proc/stat") as f:
        btime = next(int(line.split()[1]) for line in f if line.startswith("btime"))
    return btime + start_ticks / os.sysconf("SC_CLK_TCK")


def _proc_io() -> dict:
    io = {}
    with open("/proc/self/io") as f:
        for line in f:
            key, value = line.split(":")
            io[key.strip()] = int(value)
    return io


class BenchmarkRecorder:
    _marks = {}

    @classmethod
    def mark(cls, name: str) -> None:
        if is_enabled():
            cls._marks[name] = time.time()

    @classmethod
    @contextmanager
    def record(cls, smk):
        """Wrap a rule script's main(). `smk` is the snakemake script object."""
        if not is_enabled():
            yield
            return

        t_start = time.time()
        error = None
        try:
            yield
        except BaseException as e:
            error = repr(e)
            raise
        finally:
            cls._write(smk, t_start, error)

    @classmethod
    def _write(cls, smk, t_start: float, error) -> None:
        try:
            output = str(smk.output[0])
            details = smk.params.name
            self_usage = resource.getrusage(resource.RUSAGE_SELF)
            child_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
            io = _proc_io()

            record = {
                "rule": smk.rule,
                "node_id": Path(output).parent.name,
                "type": details.get("type") if isinstance(details, dict) else None,
                "status": "error" if error else "ok",
                "output_exists": os.path.exists(output),
                "error": error,
                # ru_maxrss is KiB on Linux
                "peak_rss_mb": round(self_usage.ru_maxrss / 1024, 1),
                "children_peak_rss_mb": round(child_usage.ru_maxrss / 1024, 1),
                "read_bytes": io.get("read_bytes"),
                "write_bytes": io.get("write_bytes"),
                "rchar": io.get("rchar"),
                "wchar": io.get("wchar"),
                "cpu_user_s": round(self_usage.ru_utime + child_usage.ru_utime, 1),
                "cpu_sys_s": round(self_usage.ru_stime + child_usage.ru_stime, 1),
                "t_proc_start": round(_process_start_epoch(), 3),
                "t_main_start": round(t_start, 3),
                "t_imports_done": cls._marks.get("imports_done"),
                "t_end": round(time.time(), 3),
                "pid": os.getpid(),
                "host": socket.gethostname(),
            }

            out_dir = Path(output).parent.parent / OUTPUT_DIRNAME
            out_dir.mkdir(exist_ok=True)
            with open(out_dir / OUTPUT_FILENAME, "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception:
            # Recording must never change the rule's outcome
            pass
