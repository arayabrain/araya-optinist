#!/bin/sh
# Sample the container's own cgroup (v2) once per interval.
# Runs inside the benchmark container; reads cost no measurable memory.
# Usage: sample_cgroup.sh <output.tsv> [interval_s]
set -u
OUT="$1"
INTERVAL="${2:-1}"
CG=/sys/fs/cgroup

read_kv() { awk -v k="$2" '$1 == k { print $2; found=1 } END { if (!found) print "" }' "$CG/$1" 2>/dev/null; }
read_io() {
  # Sum rbytes / wbytes over all devices
  awk '{ for (i = 2; i <= NF; i++) { split($i, a, "="); if (a[1] == "rbytes") r += a[2]; if (a[1] == "wbytes") w += a[2] } } END { printf "%d\t%d", r, w }' "$CG/io.stat" 2>/dev/null
}

printf 'epoch\tmem_current\tmem_peak\tswap_current\tanon\tfile\toom_kill\tio_rbytes\tio_wbytes\tcpu_usage_usec\n' > "$OUT"
while :; do
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(date +%s.%N | cut -c1-14)" \
    "$(cat $CG/memory.current 2>/dev/null)" \
    "$(cat $CG/memory.peak 2>/dev/null)" \
    "$(cat $CG/memory.swap.current 2>/dev/null)" \
    "$(read_kv memory.stat anon)" \
    "$(read_kv memory.stat file)" \
    "$(read_kv memory.events oom_kill)" \
    "$(read_io)" \
    "$(read_kv cpu.stat usage_usec)" >> "$OUT"
  sleep "$INTERVAL"
done
