"""
Summarise one benchmark row directory as Markdown (stdout). Host side, stdlib only.
Usage: summarize.py <row dir>
"""

import csv
import glob
import json
import os
import sys

MB = 1024 * 1024


def read_tsv(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return list(csv.DictReader(f, delimiter="\t"))


def to_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def fmt_s(seconds):
    if seconds is None:
        return "–"
    return f"{seconds / 60:.1f} min" if seconds >= 120 else f"{seconds:.0f} s"


def rules_table(path):
    rows = [json.loads(line) for line in open(path) if line.strip()]
    rows.sort(key=lambda r: r.get("t_proc_start") or 0)
    out = [
        "| Rule | Status | Peak RSS (MB) | Children peak (MB) | Written (MB) | "
        "Start→imports | Imports→end |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        t_proc = r.get("t_proc_start")
        t_imports = r.get("t_imports_done")
        t_done = r.get("t_end")
        imports = (t_imports - t_proc) if (t_proc and t_imports) else None
        if t_imports and t_done:
            work = t_done - t_imports
        else:
            work = (t_done - t_proc) if (t_proc and t_done) else None
        written = (r.get("write_bytes") or 0) / MB
        status = r["status"] + ("" if r.get("output_exists") else " (no output)")
        out.append(
            f"| {r['rule']} | {status} | {r['peak_rss_mb']:.0f} | "
            f"{r['children_peak_rss_mb']:.0f} | {written:.0f} | "
            f"{fmt_s(imports)} | {fmt_s(work)} |"
        )
    return "\n".join(out)


def cgroup_summary(rows, t_start, t_end):
    if not rows:
        return "No cgroup samples."
    before = [r for r in rows if to_int(r["epoch"]) and to_int(r["epoch"]) < t_start]
    during = [
        r for r in rows if to_int(r["epoch"]) and t_start <= to_int(r["epoch"]) <= t_end
    ]
    baseline = to_int(before[-1]["mem_current"]) if before else None
    peak_cur = max((to_int(r["mem_current"]) or 0) for r in during) if during else None
    peak_hw = max((to_int(r["mem_peak"]) or 0) for r in rows)
    # anon = process memory; file = page cache, reclaimable, so not demand
    peak_anon = max((to_int(r["anon"]) or 0) for r in rows)
    swap = max((to_int(r["swap_current"]) or 0) for r in rows)
    oom = max((to_int(r["oom_kill"]) or 0) for r in rows)
    first, last = rows[0], rows[-1]
    written = (to_int(last["io_wbytes"]) or 0) - (to_int(first["io_wbytes"]) or 0)

    def mb(v):
        return "–" if v is None else f"{v / MB:,.0f}"

    return "\n".join(
        [
            "| Measure | Value |",
            "|---|---|",
            f"| API baseline before run (MB) | {mb(baseline)} |",
            f"| Peak memory.current, 1 s samples (MB) | {mb(peak_cur)} |",
            f"| memory.peak, kernel high-water, incl. page cache (MB) | "
            f"{mb(peak_hw)} |",
            f"| **Peak anon — demand, excl. page cache (MB)** | {mb(peak_anon)} |",
            f"| Peak swap (MB) | {mb(swap)} |",
            f"| OOM kills in cgroup | {oom} |",
            # Docker Desktop does not count bind-mount writes; see per-rule figures
            f"| Bytes written by cgroup, excl. bind mounts (MB) | {mb(written)} |",
            f"| Samples | {len(rows)} |",
        ]
    )


def probe_summary(rows, t_start, t_end):
    during = [
        r for r in rows if to_int(r["epoch"]) and t_start <= to_int(r["epoch"]) <= t_end
    ]
    if not during:
        return "No probe samples during the run."
    failed = [r for r in during if r["http_code"] != "200"]
    times = sorted(float(r["time_total_s"]) for r in during if r["http_code"] == "200")
    p95 = times[int(len(times) * 0.95) - 1] if times else None
    return (
        f"{len(failed)} of {len(during)} probes failed "
        f"({100 * len(failed) / len(during):.1f} %); "
        f"p95 latency of successes {p95 * 1000:.0f} ms"
        if p95 is not None
        else f"{len(failed)} of {len(during)} probes failed"
    )


def main():
    row = sys.argv[1]
    prov = json.load(open(os.path.join(row, "provenance.json")))
    t_start, t_end = prov["timing"]["t_runs_start"], prov["timing"]["t_runs_end"]

    print(f"# {prov['run_id']}\n")
    print(
        f"- {prov['workflow']['algo']} · input {prov['input']['name']} "
        f"({prov['input']['bytes'] / MB:,.0f} MB) · "
        f"variant {prov['container']['variant']} "
        f"({prov['container']['memory_flags']}; "
        f"{prov['container']['cpu_flags']}) · "
        f"concurrency {prov['concurrency']}"
    )
    if prov["workflow"]["param_overrides"]:
        print(f"- overrides: {', '.join(prov['workflow']['param_overrides'])}")
    print(
        f"- wall clock of the runs: {fmt_s(t_end - t_start)} "
        "(not comparable with production)"
    )
    print(f"- container after runs: {prov['container']['state_after_runs']}")
    for rc_file in sorted(glob.glob(os.path.join(row, "run-*.rc"))):
        print(
            f"- {os.path.basename(rc_file)[:-3]}: exit {open(rc_file).read().strip()}"
        )

    print("\n## Container (cgroup)\n")
    print(cgroup_summary(read_tsv(os.path.join(row, "cgroup.tsv")), t_start, t_end))
    print("\n## API probe\n")
    print(probe_summary(read_tsv(os.path.join(row, "probe.tsv")), t_start, t_end))

    for path in sorted(glob.glob(os.path.join(row, "rules-*.jsonl"))):
        print(f"\n## Per rule — {os.path.basename(path)}\n")
        print(rules_table(path))
    for path in sorted(glob.glob(os.path.join(row, "du-*.txt"))):
        print(f"\n## Output size — {os.path.basename(path)} (KB)\n")
        print("```\n" + open(path).read().rstrip() + "\n```")


if __name__ == "__main__":
    main()
