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


def read_jsonl(path):
    """The records in a rules-N.jsonl, or [] when the run wrote none."""
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


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
    rows = read_jsonl(path)
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
    # A rule writes its record as it ends; a killed one (OOM killer, cancel)
    # writes none, and its peak shows only in the container figures
    recorded = {r.get("node_id") for r in rows}
    for node_id, result in functions.items():
        if node_id not in recorded:
            out.append(
                f"| {node_id} | {result or '–'} | **no record**: killed, or never "
                "started | – | – | – | – | – |"
            )
    return "\n".join(out)


def phases_tables(path):
    """Per-step peaks for rules recorded with BenchmarkRecorder.phase()."""
    out = []
    for r in read_jsonl(path):
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


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def in_window(rows, t_start, t_end):
    """Rows whose epoch lies in [t_start, t_end]."""
    out = []
    for r in rows:
        epoch = to_float(r["epoch"])
        if epoch is not None and t_start <= epoch <= t_end:
            out.append(r)
    return out


def highest(rows, value):
    """Highest value(row) over the rows, or None when no row has one: an empty
    cgroup field is missing, not 0."""
    values = [v for v in map(value, rows) if v is not None]
    return max(values) if values else None


def anon_and_swap(row):
    anon = to_int(row["anon"])
    return None if anon is None else anon + (to_int(row["swap_current"]) or 0)


def longest_gap(rows, t_start, t_end):
    """Longest time between consecutive samples over the run window, in s."""
    epochs = sorted(e for e in (to_float(r["epoch"]) for r in rows) if e is not None)
    gaps = [b - a for a, b in zip(epochs, epochs[1:]) if b >= t_start and a <= t_end]
    return max(gaps) if gaps else None


def cgroup_summary(rows, t_start, t_end, interval):
    if not rows:
        return "No cgroup samples."
    before = [r for r in rows if (to_float(r["epoch"]) or float("inf")) < t_start]
    during = in_window(rows, t_start, t_end)
    last_before = before[-1] if before else {}

    baseline = to_int(last_before.get("mem_current"))
    peak_cur = highest(during, lambda r: to_int(r["mem_current"]))
    # memory.peak and oom_kill are kept by the kernel since the container started
    peak_hw = highest(rows, lambda r: to_int(r["mem_peak"]))
    oom = highest(rows, lambda r: to_int(r["oom_kill"]))
    # anon = process memory; file = page cache, reclaimable, so not demand
    baseline_anon = to_int(last_before.get("anon"))
    peak_anon = highest(during, lambda r: to_int(r["anon"]))
    rise_anon = None
    if baseline_anon is not None and peak_anon is not None:
        rise_anon = peak_anon - baseline_anon
    swap = highest(during, lambda r: to_int(r["swap_current"]))
    # Pages swapped out leave anon; per sample, so not the sum of two peaks
    peak_demand = highest(during, anon_and_swap)
    gap = longest_gap(rows, t_start, t_end)
    first_w, last_w = to_int(rows[0]["io_wbytes"]), to_int(rows[-1]["io_wbytes"])
    written = None if first_w is None or last_w is None else last_w - first_w
    # The sampler's earlier awk summed in 32 bits and stopped at 2^31 - 1
    io_capped = last_w == 2**31 - 1
    if io_capped:
        written = None

    def mb(v):
        return "n/a" if v is None else f"{v / MB:,.0f}"

    table = [
        "| Measure | Value |",
        "|---|---|",
        f"| API baseline before run, memory.current incl. page cache (MB) | "
        f"{mb(baseline)} |",
        f"| Peak memory.current during the runs, 1 s samples (MB) | "
        f"{mb(peak_cur)} |",
        f"| memory.peak, kernel high-water since container start, "
        f"incl. page cache (MB) | {mb(peak_hw)} |",
        f"| API baseline before run, anon (MB) | {mb(baseline_anon)} |",
        f"| **Peak anon during the runs — demand, excl. page cache (MB)** | "
        f"{mb(peak_anon)} |",
        f"| Rise in anon over the baseline (MB) | {mb(rise_anon)} |",
        f"| Peak swap during the runs (MB) | {mb(swap)} |",
        f"| Peak anon + swap during the runs, per sample (MB) | {mb(peak_demand)} |",
        f"| OOM kills in cgroup since container start | "
        f"{'n/a' if oom is None else oom} |",
        # Docker Desktop does not count bind-mount writes; see per-rule figures
        f"| Bytes written by cgroup, excl. bind mounts (MB) | {mb(written)} |",
        f"| Samples (all; during the runs) | {len(rows)}; {len(during)} |",
        f"| Longest gap between samples during the runs (s) | "
        f"{'n/a' if gap is None else f'{gap:.1f}'} |",
    ]
    notes = []
    if swap:
        notes.append(
            "Swap was in use: anon leaves out swapped-out pages, so peak anon is a "
            "lower bound on demand; peak anon + swap is the closer figure."
        )
    if gap is not None and gap > 3 * interval:
        notes.append(
            f"Samples were missing for up to {gap:.0f} s during the runs; the sampled "
            "peaks are lower bounds (memory.peak is the kernel's, not sampled)."
        )
    if io_capped:
        notes.append(
            "Bytes written: n/a, the row's sampler stopped counting at 2 GiB; use "
            "the per-rule figures."
        )
    if any(v is None for v in (baseline, peak_cur, peak_anon, swap, peak_hw, oom)):
        notes.append(
            "n/a: the cgroup did not provide that value (cgroup v2 is needed; "
            "memory.peak needs Linux 5.19 or later)."
        )
    return "\n\n".join(["\n".join(table)] + notes)


def probe_summary(rows, t_start, t_end):
    during = in_window(rows, t_start, t_end)
    if not during:
        return "No probe samples during the run."
    failed = [r for r in during if r["http_code"] != "200"]
    times = sorted(float(r["time_total_s"]) for r in during if r["http_code"] == "200")
    p95 = times[int(len(times) * 0.95) - 1] if times else None

    # A failing probe waits up to its timeout, so failures under-count a stall:
    # measure each stall from its first failure to the next answer instead
    stalls, start = [], None
    for r in during:
        epoch = float(r["epoch"])
        if r["http_code"] != "200":
            if start is None:
                start = epoch
            stall_end = epoch + float(r["time_total_s"] or 0)
        elif start is not None:
            stalls.append((start, epoch))
            start = None
    if start is not None:
        stalls.append((start, stall_end))
    longest = max(stalls, key=lambda s: s[1] - s[0]) if stalls else None

    parts = [f"{len(failed)} of {len(during)} probes failed"]
    if stalls:
        parts.append(
            f"`/health` unanswered for {sum(e - s for s, e in stalls):.0f} s in "
            f"{len(stalls)} stall(s), the longest {longest[1] - longest[0]:.0f} s "
            f"from +{longest[0] - t_start:.0f} s"
        )
    if p95 is not None:
        parts.append(f"p95 latency of successes {p95 * 1000:.0f} ms")
    return "; ".join(parts)


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
    swap_seen = prov["container"].get("swap_seen_bytes")
    if swap_seen is not None:
        print(f"- swap the container sees: {swap_seen / MB:,.0f} MiB")
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
    runs = sorted(
        int(os.path.basename(p)[len("run-") : -len(".rc")])
        for p in glob.glob(os.path.join(row, "run-*.rc"))
    )
    for run in runs:
        result = bench_result(row, run)
        harness = result.get("harness_process") or {}
        harness_mb += harness.get("rss_anon_mb", 0.0)
        memory = (
            f" · harness process RssAnon {harness['rss_anon_mb']:,.0f} MB"
            f" (peak RSS {harness.get('hwm_mb', 0):,.0f} MB)"
            if "rss_anon_mb" in harness
            else ""
        )
        stop = ""
        if "cancelled" in result:
            stop = " · timed out, cancel " + (
                "accepted" if result["cancelled"] else "**not accepted**"
            )
            if result.get("still_running"):
                stop += ", **still running when the watcher gave up**"
        rc = open(os.path.join(row, f"run-{run}.rc")).read().strip()
        print(f"- run-{run}: exit {rc}{memory}{stop}")
    leftover_path = os.path.join(row, "processes-after-runs.json")
    if os.path.exists(leftover_path):
        leftover = json.load(open(leftover_path))
        if leftover.get("count"):
            print(
                f"- **{leftover['count']} workflow processes were still running"
                f" {leftover.get('waited_s', 0):.0f} s after the runs ended**: a"
                " stopped run's work went on beside the others"
            )
        elif leftover.get("count_at_end"):
            print(
                f"- {leftover['count_at_end']} workflow processes were still stopping"
                f" when the runs ended; gone {leftover['waited_s']:.0f} s later"
            )

    interval = prov.get("sampling", {}).get("cgroup_interval_s") or 1
    print("\n## Container (cgroup)\n")
    cgroup_rows = read_tsv(os.path.join(row, "cgroup.tsv"))
    print(cgroup_summary(cgroup_rows, t_start, t_end, interval))
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
    print(
        "\nPer-rule and per-step peaks are each rule process's own RSS high-water"
        " mark. They include file-backed pages (memmapped data, shared libraries),"
        " which the container's anon leaves out, and they leave out child"
        " processes (Children peak is the largest finished child, not a sum), such"
        " as a CaImAn worker pool. Read demand from the container's figures."
    )
    for run in runs:
        path = os.path.join(row, f"rules-{run}.jsonl")
        # Several fixtures: run N used the N-th
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
