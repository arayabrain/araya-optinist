#!/usr/bin/env bash
# Run the real-infrastructure case list on one instance prepared by
# bootstrap.sh, at the tier's container ceiling with its swap allowance
# (variant S, which needs the host swap bootstrap.sh sets up). Copies each
# finished row to S3 straight away, so a self-shutdown loses nothing.
#
# Usage (as root, on the instance):
#   BENCH_S3_PREFIX=s3://<bucket>/<prefix> run_cases.sh
# Environment passed on to run_bench.sh, for the tier being modelled:
#   BENCH_UVICORN_WORKERS, BENCH_MEMORY_MIB, BENCH_SWAP_MIB
# Optional:
#   BENCH_ROW_TIMEOUT   seconds per run (default 3600)
#   BENCH_SHORT_FRAMES  also run the concurrent rows on M cut to this many frames
#
# Inputs expected in /opt/bench/inputs: neurofinder.00.10_stack.tif (M) and
# neurofinder.00.10_stack_x2.tif (L, from make_large_input.py).
set -uo pipefail

: "${BENCH_S3_PREFIX:?}"
ROOT="${BENCH_ROOT:-/opt/bench}"
B="$ROOT/repo/infrastructure/scripts/benchmark/run_bench.sh"
RES="$ROOT/results"
M="$ROOT/inputs/neurofinder.00.10_stack.tif"
L="$ROOT/inputs/neurofinder.00.10_stack_x2.tif"
S="$ROOT/repo/sample_data/dev_mouse2p_short_image.tiff"
SHORT_FRAMES="${BENCH_SHORT_FRAMES:-}"
# A task sets CPU shares, not a cap, so the container may use every vCPU.
# Runs start through the API, so no harness process adds to the container's memory
COMMON=(--variant S --start api --cpus "$(nproc)" --timeout "${BENCH_ROW_TIMEOUT:-3600}")
export BENCH_LANE="aws"

sync_results() { aws s3 sync --quiet "$RES/" "$BENCH_S3_PREFIX/results/"; }

row() {
  local label=$1; shift
  echo "ROW-START $label $(date -u +%H:%M)"
  "$B" --label "$label" --results-dir "$RES" "$@"
  echo "ROW-END $label exit=$? $(date -u +%H:%M)"
  sync_results
}

warmup() {
  # Builds the conda envs once; not recorded with the results
  "$B" --label warmup --results-dir "$ROOT/warmup" --timeout 3600 "$@" > /dev/null 2>&1
  echo "WARMUP $* exit=$?"
}

warmup --algo suite2p --input "$S" --variant U --start api
warmup --algo caiman --input "$S" --variant U --start api

# One run: the reference the concurrent rows are compared with
row S2P-1 --algo suite2p --input "$M" "${COMMON[@]}"
# Two suite2p runs, then suite2p beside CaImAn
row S2P-2 --algo suite2p --input "$M" --conc 2 "${COMMON[@]}"
row S2P-CM --algo suite2p,caiman --input "$M" "${COMMON[@]}"
# One run at about twice the length
row S2P-L --algo suite2p --input "$L" "${COMMON[@]}"

if [[ -n "$SHORT_FRAMES" ]]; then
  SHORT="neurofinder.00.10_stack_f${SHORT_FRAMES}.tif"
  docker run --rm --platform linux/amd64 \
    -v "$ROOT/repo:/app" -v "$ROOT/inputs:/inputs" -w /app --entrypoint "" \
    development-optinist-for-cloud:latest \
    python infrastructure/scripts/benchmark/make_large_input.py \
    "/inputs/$(basename "$M")" "/inputs/$SHORT" --repeat 1 --frames "$SHORT_FRAMES" \
    > "$RES/input-$SHORT.json"
  row S2P-2-short --algo suite2p --input "$ROOT/inputs/$SHORT" --conc 2 "${COMMON[@]}"
  row S2P-CM-short --algo suite2p,caiman --input "$ROOT/inputs/$SHORT" "${COMMON[@]}"
fi

sync_results
echo "ALL-ROWS-DONE $(date -u +%H:%M)"
