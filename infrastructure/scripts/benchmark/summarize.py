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


def bench_result(row, run):
    """The run's BENCH_RESULT line from run-N.log, or {}."""
    path = os.path.join(row, f"run-{run}.log")
    if not os.path.exists(path):
        return {}
    with open(path, errors="replace") as f:
        for line in f:
            if line.startswith("BENCH_RESULT "):
                return json.loads(line[len("BENCH_RESULT ") :])
    return {}


def fmt_s(seconds):
    if seconds is None:
        return "–"
    return f"{seconds / 60:.1f} min" if seconds >= 120 else f"{seconds:.0f} s"


def rules_table(path, functions):
    """`functions`: the run's node results by node id, from BENCH_RESULT. A rule
    process ends ok when its function fails (the error is its output), so the
    node result is the one that shows a failure."""
    rows = [json.loads(line) for line in open(path) if line.strip()]
    rows.sort(key=lambda r: r.get("t_proc_start") or 0)
    out = [
        "| Rule | Node | Process | Peak RSS (MB) | Children peak (MB) | "
        "Written (MB) | Start→imports | Imports→end |",
        "|---|---|---|---|---|---|---|---|",
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
        node = functions.get(r.get("node_id")) or "–"
        out.append(
            f"| {r['rule']} | {node} | {status} | {r['peak_rss_mb']:.0f} | "
            f"{r['children_peak_rss_mb']:.0f} | {written:.0f} | "
            f"{fmt_s(imports)} | {fmt_s(work)} |"
        )
    return "\n".join(out)


def phases_tables(path):
    """Per-step peaks for rules recorded with BenchmarkRecorder.phase()."""
    out = []
    for line in open(path):
        if not line.strip():
            continue
        r = json.loads(line)
        if not r.get("phases"):
            continue
        out += [
            f"\n**{r['rule']}** — peak {r['peak_rss_mb']:.0f} MB\n",
            "| Step | Peak RSS (MB) | RSS at end (MB) | Seconds |",
            "|---|---|---|---|",
        ]
        for ph in r["phases"]:
            indent = "  " * ph.get("depth", 0)
            out.append(
                f"| {indent}{ph['name']} | {ph['peak_rss_mb']:.0f} | "
                f"{ph['rss_at_end_mb']:.0f} | {ph['seconds']:.1f} |"
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
    baseline_anon = to_int(before[-1]["anon"]) if before else None
    peak_anon = max((to_int(r["anon"]) or 0) for r in rows)
    rise_anon = None if baseline_anon is None else peak_anon - baseline_anon
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
            f"| API baseline before run, memory.current incl. page cache (MB) | "
            f"{mb(baseline)} |",
            f"| Peak memory.current, 1 s samples (MB) | {mb(peak_cur)} |",
            f"| memory.peak, kernel high-water, incl. page cache (MB) | "
            f"{mb(peak_hw)} |",
            f"| API baseline before run, anon (MB) | {mb(baseline_anon)} |",
            f"| **Peak anon — demand, excl. page cache (MB)** | {mb(peak_anon)} |",
            f"| Rise in anon over the baseline (MB) | {mb(rise_anon)} |",
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
    if prov["workflow"].get("benchmark_env"):
        print(f"- benchmark env: {', '.join(prov['workflow']['benchmark_env'])}")
    if prov["workflow"]["param_overrides"]:
        print(f"- overrides: {', '.join(prov['workflow']['param_overrides'])}")
    comparable = prov["timing"].get("wall_clock_comparable_with_production")
    print(
        f"- wall clock of the runs: {fmt_s(t_end - t_start)}"
        + ("" if comparable else " (emulated: not comparable with a deployment)")
    )
    print(f"- container after runs: {prov['container']['state_after_runs']}")
    start = prov["workflow"].get("start", "exec")
    print(
        "- start: "
        + (
            "api (POST /run; the snakemake parent is a uvicorn worker)"
            if start == "api"
            else "exec (a driver process per run; a deployed run has none)"
        )
    )
    harness_mb = 0.0
    for rc_file in sorted(glob.glob(os.path.join(row, "run-*.rc"))):
        run = os.path.basename(rc_file)[len("run-") : -len(".rc")]
        harness = bench_result(row, run).get("harness_process") or {}
        harness_mb += harness.get("rss_anon_mb", 0.0)
        memory = (
            f" · harness process RssAnon {harness['rss_anon_mb']:,.0f} MB"
            f" (peak RSS {harness.get('hwm_mb', 0):,.0f} MB)"
            if "rss_anon_mb" in harness
            else ""
        )
        print(f"- run-{run}: exit {open(rc_file).read().strip()}{memory}")

    print("\n## Container (cgroup)\n")
    print(cgroup_summary(read_tsv(os.path.join(row, "cgroup.tsv")), t_start, t_end))
    if harness_mb:
        note = (
            " (a deployed run's uvicorn worker would hold part of it)"
            if start == "exec"
            else ""
        )
        print(
            f"\nThe harness's own processes held {harness_mb:,.0f} MB RssAnon at the"
            f" end of the runs, included in peak anon{note}."
        )
    print("\n## API probe\n")
    print(probe_summary(read_tsv(os.path.join(row, "probe.tsv")), t_start, t_end))

    algos = prov["workflow"]["algo"].split(",")
    for path in sorted(glob.glob(os.path.join(row, "rules-*.jsonl"))):
        # Several fixtures: run N used the N-th
        run = int(os.path.basename(path)[len("rules-") : -len(".jsonl")])
        mixed = len(algos) > 1 and run <= len(algos)
        fixture = f" ({algos[run - 1]})" if mixed else ""
        print(f"\n## Per rule — {os.path.basename(path)}{fixture}\n")
        functions = bench_result(row, run).get("functions") or {}
        print(rules_table(path, functions))
        phases = phases_tables(path)
        if phases:
            print(f"\n### Steps within rules — {os.path.basename(path)}")
            print(phases)
    for path in sorted(glob.glob(os.path.join(row, "du-*.txt"))):
        print(f"\n## Output size — {os.path.basename(path)} (KB)\n")
        print("```\n" + open(path).read().rstrip() + "\n```")


if __name__ == "__main__":
    main()
