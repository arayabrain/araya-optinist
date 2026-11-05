#!/usr/bin/env bash
# Sample the container's own cgroup (v2) once per interval.
# Runs inside the benchmark container, so inside the cgroup it measures. Bash
# builtins only, no process per sample: near the memory ceiling a new process
# stalls in reclaim, which would drop samples exactly at the peak.
# Usage: sample_cgroup.sh <output.tsv> [interval_s]
set -u
OUT="$1"
INTERVAL="${2:-1}"
CG=/sys/fs/cgroup

# first_line <var> <file>: the file's first line, or empty
first_line() {
  local -n _out=$1
  _out=""
  read -r _out 2>/dev/null < "$CG/$2" || true
}

# key_value <var> <file> <key>: the value of a "key value" line, or empty
key_value() {
  local -n _out=$1
  local k v
  _out=""
  while read -r k v; do
    [[ "$k" == "$3" ]] && _out=$v
  done 2>/dev/null < "$CG/$2"
}

# io_bytes: RBYTES / WBYTES summed over all devices
io_bytes() {
  local -a fields
  local kv
  RBYTES=0 WBYTES=0
  while read -r -a fields; do
    for kv in "${fields[@]:1}"; do
      case "$kv" in
        rbytes=*) RBYTES=$((RBYTES + ${kv#rbytes=})) ;;
        wbytes=*) WBYTES=$((WBYTES + ${kv#wbytes=})) ;;
      esac
    done
  done 2>/dev/null < "$CG/io.stat"
}

# A read that times out on a pipe this shell holds open waits without a process
exec {WAIT_FD}<> <(:)

printf 'epoch\tmem_current\tmem_peak\tswap_current\tanon\tfile\toom_kill\tio_rbytes\tio_wbytes\tcpu_usage_usec\n' > "$OUT"
while :; do
  first_line MEM_CURRENT memory.current
  first_line MEM_PEAK memory.peak
  first_line SWAP_CURRENT memory.swap.current
  key_value ANON memory.stat anon
  key_value FILE memory.stat file
  key_value OOM_KILL memory.events oom_kill
  io_bytes
  key_value CPU_USEC cpu.stat usage_usec
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${EPOCHREALTIME:0:14}" "$MEM_CURRENT" "$MEM_PEAK" "$SWAP_CURRENT" \
    "$ANON" "$FILE" "$OOM_KILL" "$RBYTES" "$WBYTES" "$CPU_USEC" >> "$OUT"
  read -r -t "$INTERVAL" -u "$WAIT_FD" _ || true
done
