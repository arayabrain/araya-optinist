"""
The calling process's own memory, for the harness processes that share the
benchmark container with the runs. Stdlib only.
"""


def own_memory_mb() -> dict:
    """RssAnon and VmHWM of this process in MiB; empty where /proc is missing."""
    fields = {"RssAnon": "rss_anon_mb", "VmHWM": "hwm_mb"}
    memory = {}
    try:
        with open("/proc/self/status") as f:
            for line in f:
                key = line.split(":", 1)[0]
                if key in fields:
                    memory[fields[key]] = round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    return memory
