#!/usr/bin/env bash
# Run the #893 real-lane cases on an instance prepared by bootstrap.sh.
# Copies each finished row to S3 straight away, so a self-shutdown loses nothing.
#
# Usage (as root, on the instance):
#   BENCH_S3_PREFIX=s3://<bucket>/<prefix> run_cases.sh demand|replica
#
# Inputs expected in /opt/bench/inputs: neurofinder.00.10_stack.tif (M) and
# neurofinder.00.10_stack_x2.tif (L, from make_large_input.py).
set -uo pipefail

ROLE="${1:?demand|replica}"
: "${BENCH_S3_PREFIX:?}"
ROOT=/opt/bench
B="$ROOT/repo/infrastructure/scripts/benchmark/run_bench.sh"
RES="$ROOT/results"
M="$ROOT/inputs/neurofinder.00.10_stack.tif"
L="$ROOT/inputs/neurofinder.00.10_stack_x2.tif"
S="$ROOT/repo/sample_data/dev_mouse2p_short_image.tiff"
FIXES=(--env OPTINIST_BENCH_SKIP_PC_METRICS=1 --env OPTINIST_BENCH_STREAM_MC_IMAGES=1
       --env OPTINIST_BENCH_ROI_IMAGES_PATH=1 --set suite2p_file_convert.batch_size=100)
export BENCH_LANE="aws-$ROLE"

sync_results() { aws s3 sync --quiet "$RES/" "$BENCH_S3_PREFIX/results/$ROLE/"; }

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

case "$ROLE" in
  demand)
    warmup --algo suite2p --input "$S" --variant U
    warmup --algo caiman --input "$S" --variant U
    # A: two concurrent runs at 1.45 GB, as shipped and with the #531 fixes
    row A1 --algo suite2p --input "$M" --variant U --conc 2 --cpus 8 --timeout 10800
    row A1-fix --algo suite2p --input "$M" --variant U --conc 2 --cpus 8 --timeout 10800 "${FIXES[@]}"
    row A2 --algo caiman --input "$M" --variant U --conc 2 --cpus 8 --timeout 10800
    # B: one run at ~2.9 GB, as shipped and with the fixes
    row B --algo suite2p --input "$L" --variant U --cpus 8 --timeout 10800
    row B-fix --algo suite2p --input "$L" --variant U --cpus 8 --timeout 10800 "${FIXES[@]}"
    # Reproducibility
    row A1-rep --algo suite2p --input "$M" --variant U --conc 2 --cpus 8 --timeout 10800
    ;;
  replica)
    warmup --algo suite2p --input "$S" --variant U --cpus 2
    # C: today's production configuration (ceiling + swap), the stall baseline.
    # Production sets CPU shares, not a cap, so the container may use both vCPUs
    row C --algo suite2p --input "$M" --variant S --cpus 2 --timeout 10800
    # C-fix: same configuration with the #531 fixes
    row C-fix --algo suite2p --input "$M" --variant S --cpus 2 --timeout 10800 "${FIXES[@]}"
    ;;
  *) echo "role must be demand or replica" >&2; exit 2 ;;
esac

sync_results
echo "ALL-ROWS-DONE $(date -u +%H:%M)"
