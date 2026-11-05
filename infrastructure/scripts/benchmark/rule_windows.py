"""
Container anon per rule window, for every run of one or more benchmark rows
(rules-N.jsonl against cgroup.tsv), with each rule's own peak RSS, time and
bytes written. Host side, stdlib only.

anon and bytes written are in decimal MB (bytes / 1e6), the row's peak also in
MiB; rule RSS is the recorder's (MiB). A rule's window runs from its process
start to its end, so its first samples can still hold the previous rule's exit.
"After the rules" runs from a run's last rule end to its BENCH_RESULT t_end.

Usage: rule_windows.py <row dir> [<row dir> ...]
"""

import csv
import glob
import json
import os
import re
import sys

MIB = 1024 * 1024


def samples_of(row_dir):
    """(epoch, anon, swap) per sample with an anon value; swap is 0 where the
    cgroup does not account it."""
    with open(os.path.join(row_dir, "cgroup.tsv")) as f:
        return [
            (float(r["epoch"]), int(r["anon"]), int(r["swap_current"] or 0))
            for r in csv.DictReader(f, delimiter="\t")
            if r["anon"]
        ]


def peak(samples, start, end):
    window = [anon for t, anon, _ in samples if start <= t <= end]
    return max(window) if window else None


def result_of(row_dir, run):
    with open(os.path.join(row_dir, f"run-{run}.log"), errors="replace") as f:
        for line in f:
            match = re.search(r"BENCH_RESULT (\{.*\})", line)
            if match:
                return json.loads(match.group(1))
    return None


def fmt(value):
    return "        –" if value is None else f"{value / 1e6:9,.0f}"


def print_row(row_dir):
    samples = samples_of(row_dir)
    print(f"== {os.path.basename(row_dir.rstrip('/'))}")
    if not samples:
        print("  no cgroup samples")
        return
    row_peak = max(anon for _, anon, _ in samples)
    t_peak = next(t for t, anon, _ in samples if anon == row_peak)
    print(
        f"  row peak anon {row_peak / 1e6:,.0f} MB [{row_peak / MIB:,.0f} MiB]"
        f" at {t_peak - samples[0][0]:.0f} s; swap max"
        f" {max(swap for _, _, swap in samples) / 1e6:,.0f} MB"
    )
    for path in sorted(glob.glob(os.path.join(row_dir, "rules-*.jsonl"))):
        run = re.search(r"rules-(\d+)", path).group(1)
        with open(path) as f:
            rules = [json.loads(line) for line in f if line.strip()]
        if not rules:
            continue
        result = result_of(row_dir, run)
        written = sum(r.get("write_bytes") or 0 for r in rules)
        minutes = result["elapsed_s"] / 60 if result else 0
        print(
            f"  run {run}: {result['success'] if result else '?'}, "
            f"{minutes:.1f} min, written {written / 1e6:,.0f} MB"
            f" [{written / MIB:,.0f} MiB]"
        )
        for r in rules:
            anon = peak(samples, r["t_proc_start"], r["t_end"])
            seconds = r["t_end"] - r["t_proc_start"]
            print(
                f"    {r['rule']:28s} anon {fmt(anon)}  rule RSS MiB"
                f" {r['peak_rss_mb']:7,.0f}  {seconds:6.0f} s"
                f"  written {(r.get('write_bytes') or 0) / 1e6:9,.0f}  {r['status']}"
            )
        last = max(r["t_end"] for r in rules)
        # A rule writes its record as it ends: a killed one (OOM killer,
        # cancel) has none, and its peak falls into "after the rules"
        recorded = {r.get("node_id") for r in rules}
        functions = (result or {}).get("functions", {})
        unrecorded = [n for n in functions if n not in recorded]
        for node_id in unrecorded:
            print(f"    {node_id:28s} no record: killed, or never started")
        if result:
            after = peak(samples, last, result["t_end"])
            label = "after the rules" if not unrecorded else "after the last record"
            print(
                f"    {label:28s} anon {fmt(after)}"
                f"{'':30s}{result['t_end'] - last:6.0f} s"
            )


if __name__ == "__main__":
    for row_dir in sys.argv[1:]:
        print_row(row_dir)
