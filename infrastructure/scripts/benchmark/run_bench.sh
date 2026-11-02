#!/usr/bin/env bash
# Run one row of the workflow benchmark and record it.
#
# One container = one application task: the API and the workflow(s) share the
# container's cgroup. See README.md for the runbook.
#
# Usage:
#   run_bench.sh --algo <fixture>[,<fixture>...] --input <host path to tiff> \
#                --variant U|P|S --results-dir <dir> [--conc N] [--label R3] \
#                [--set node.dotted.param=value ...] [--cpus 2 | --cpuset 0-4] \
#                [--env KEY=VALUE ...] [--timeout 10800] [--keep-outputs]
#
# Several fixtures start one run each, together (e.g. suite2p,caiman).
# Variants:  U = no memory limit (demand)   P = tier ceiling, no swap
#            S = tier ceiling plus its swap allowance (needs host swap)
# BENCH_MEMORY_MIB / BENCH_SWAP_MIB set the ceiling and the swap allowance.
# BENCH_UVICORN_WORKERS sets the API's workers (default 5, the baseline rows).
# BENCH_LANE (default "local") names the lane in the row ID and provenance.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
VOLUMES_DIR="${BENCH_VOLUMES_DIR:-$(cd "$REPO_DIR/.." && pwd)/optinist-docker-volumes}"
DATA_DIR="${BENCH_DATA_DIR:-$VOLUMES_DIR/bench_studio_data}"
SNAKEMAKE_DIR="${BENCH_SNAKEMAKE_DIR:-$VOLUMES_DIR/.snakemake}"
IMAGE="${BENCH_IMAGE:-development-optinist-for-cloud:latest}"
CONTAINER="optinist-bench"
HOST_PORT="${BENCH_PORT:-8100}"
UVICORN_WORKERS="${BENCH_UVICORN_WORKERS:-5}"
SAMPLE_INTERVAL=1
PROBE_INTERVAL=1
PROBE_TIMEOUT=10
IDLE_BEFORE_RUN=30

# Container ceiling and swap allowance of variants P and S. The defaults are
# the free tier's container_memory_mib / container_max_swap_mib defaults in
# infrastructure/terraform/main.tf; earlier rows were recorded with them.
MEMORY_MIB="${BENCH_MEMORY_MIB:-6656}"
SWAP_MIB="${BENCH_SWAP_MIB:-32768}"
CEILING="${MEMORY_MIB}m"
CEILING_PLUS_SWAP="$((MEMORY_MIB + SWAP_MIB))m"
LANE="${BENCH_LANE:-local}"

ALGO="" INPUT="" VARIANT="" RESULTS_DIR="" CONC="" LABEL="" CPUS="2" CPUSET=""
TIMEOUT=10800 KEEP_OUTPUTS=0
OVERRIDES=()
EXTRA_ENV=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --algo) ALGO="$2"; shift 2 ;;
    --input) INPUT="$2"; shift 2 ;;
    --variant) VARIANT="$2"; shift 2 ;;
    --results-dir) RESULTS_DIR="$2"; shift 2 ;;
    --conc) CONC="$2"; shift 2 ;;
    --label) LABEL="$2"; shift 2 ;;
    --set) OVERRIDES+=("$2"); shift 2 ;;
    --env) EXTRA_ENV+=("$2"); shift 2 ;;
    --cpus) CPUS="$2"; CPUSET=""; shift 2 ;;
    --cpuset) CPUSET="$2"; CPUS=""; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --keep-outputs) KEEP_OUTPUTS=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$ALGO" && -n "$INPUT" && -n "$VARIANT" && -n "$RESULTS_DIR" ]] || {
  sed -n '2,18p' "$0"; exit 2; }
IFS=, read -r -a ALGOS <<< "$ALGO"
for a in "${ALGOS[@]}"; do
  [[ -f "$SCRIPT_DIR/fixtures/$a.json" ]] || { echo "no fixture for $a" >&2; exit 2; }
done
if [[ ${#ALGOS[@]} -gt 1 ]]; then
  [[ -z "$CONC" || "$CONC" -eq ${#ALGOS[@]} ]] || {
    echo "--conc must equal the number of fixtures in --algo" >&2; exit 2; }
  CONC=${#ALGOS[@]}
fi
CONC="${CONC:-1}"
[[ -f "$INPUT" ]] || { echo "input not found: $INPUT" >&2; exit 2; }
docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER" && {
  echo "container $CONTAINER already exists — another run in progress?" >&2; exit 1; }

case "$VARIANT" in
  U) MEM_FLAGS=() ;;
  P) MEM_FLAGS=(--memory="$CEILING" --memory-swap="$CEILING") ;;
  S) MEM_FLAGS=(--memory="$CEILING" --memory-swap="$CEILING_PLUS_SWAP") ;;
  *) echo "variant must be U, P or S" >&2; exit 2 ;;
esac
ENV_FLAGS=()
for e in ${EXTRA_ENV[@]+"${EXTRA_ENV[@]}"}; do ENV_FLAGS+=(-e "$e"); done
if [[ -n "$CPUSET" ]]; then CPU_FLAGS=(--cpuset-cpus="$CPUSET"); else CPU_FLAGS=(--cpus="$CPUS"); fi

INPUT_DIR_HOST="$(cd "$(dirname "$INPUT")" && pwd)"
INPUT_NAME="$(basename "$INPUT")"
WORKSPACE="bench"
RUN_ID="$(date +%Y%m%d-%H%M)-${LANE}-${ALGO//,/+}-${INPUT_NAME%%.*}-${VARIANT}-c${CONC}${LABEL:+-$LABEL}"
mkdir -p "$RESULTS_DIR" "$DATA_DIR/input/$WORKSPACE"
OUT="$(cd "$RESULTS_DIR" && pwd)/$RUN_ID"
mkdir -p "$OUT"
echo "== $RUN_ID"

# The input node reads input/<workspace>/<file>; link to the read-only mount
ln -sfn "/bench_input/$INPUT_NAME" "$DATA_DIR/input/$WORKSPACE/$INPUT_NAME"

PROBE_PID=""
cleanup() {
  if [[ -n "$PROBE_PID" ]]; then kill "$PROBE_PID" 2>/dev/null; wait "$PROBE_PID" 2>/dev/null; fi || true
  docker logs "$CONTAINER" > "$OUT/api.log" 2>&1 || true
  docker rm -f "$CONTAINER" > /dev/null 2>&1 || true
}
trap cleanup EXIT

docker run -d --name "$CONTAINER" --platform linux/amd64 \
  ${MEM_FLAGS[@]+"${MEM_FLAGS[@]}"} "${CPU_FLAGS[@]}" \
  -p "127.0.0.1:$HOST_PORT:8000" \
  -v "$REPO_DIR:/app" \
  -v "$SNAKEMAKE_DIR:/app/.snakemake" \
  -v "$DATA_DIR:/app/studio_data" \
  -v "$INPUT_DIR_HOST:/bench_input:ro" \
  -v "$OUT:/bench_out" \
  -e IS_STANDALONE=True -e REMOTE_STORAGE_TYPE=0 -e OPTINIST_BENCHMARK=1 \
  -e OPTINIST_DIR=/app/studio_data -e PYTHONPATH=/app/ -e TZ=UTC \
  ${ENV_FLAGS[@]+"${ENV_FLAGS[@]}"} \
  -w /app --entrypoint "" "$IMAGE" \
  python main.py --host 0.0.0.0 --port 8000 --workers "$UVICORN_WORKERS" > /dev/null

echo "-- waiting for /health"
for _ in $(seq 1 180); do
  curl -sf -o /dev/null --max-time 2 "http://127.0.0.1:$HOST_PORT/health" && break
  sleep 2
done
curl -sf -o /dev/null --max-time 5 "http://127.0.0.1:$HOST_PORT/health" || {
  echo "API did not come up" >&2; exit 1; }

docker exec -d "$CONTAINER" sh /app/infrastructure/scripts/benchmark/sample_cgroup.sh \
  /bench_out/cgroup.tsv "$SAMPLE_INTERVAL"
"$SCRIPT_DIR/probe.sh" "http://127.0.0.1:$HOST_PORT/health" "$OUT/probe.tsv" \
  "$PROBE_INTERVAL" "$PROBE_TIMEOUT" &
PROBE_PID=$!

echo "-- idle ${IDLE_BEFORE_RUN}s (API baseline)"
sleep "$IDLE_BEFORE_RUN"
T_RUNS_START=$(date +%s)

SET_ARGS=()
for o in "${OVERRIDES[@]+"${OVERRIDES[@]}"}"; do SET_ARGS+=(--set "$o"); done

PIDS=()
for i in $(seq 1 "$CONC"); do
  UID_I="${RUN_ID}-${i}"
  # One fixture: every run uses it; several: run i uses the i-th
  FIXTURE_I="${ALGOS[$(((i - 1) % ${#ALGOS[@]}))]}"
  docker exec "$CONTAINER" timeout "$TIMEOUT" python \
    infrastructure/scripts/benchmark/run_workflow.py \
    --fixture "infrastructure/scripts/benchmark/fixtures/$FIXTURE_I.json" \
    --input "$INPUT_NAME" --workspace "$WORKSPACE" --unique-id "$UID_I" \
    "${SET_ARGS[@]+"${SET_ARGS[@]}"}" > "$OUT/run-$i.log" 2>&1 &
  PIDS+=($!)
  echo "-- started run $i ($UID_I, $FIXTURE_I)"
done

RC_ALL=0
for i in "${!PIDS[@]}"; do
  rc=0; wait "${PIDS[$i]}" || rc=$?
  echo "-- run $((i + 1)) exit $rc"
  echo "$rc" > "$OUT/run-$((i + 1)).rc"
  [[ $rc -ne 0 ]] && RC_ALL=$rc
done
T_RUNS_END=$(date +%s)

CONTAINER_STATE="$(docker inspect -f '{{.State.Status}} oom_killed={{.State.OOMKilled}} exit={{.State.ExitCode}}' "$CONTAINER" 2>/dev/null || echo gone)"

# Collect from the host side (works even if the container died)
for i in $(seq 1 "$CONC"); do
  RUN_DIR="$DATA_DIR/output/$WORKSPACE/${RUN_ID}-${i}"
  [[ -d "$RUN_DIR" ]] || continue
  [[ -f "$RUN_DIR/bench/rules.jsonl" ]] && cp "$RUN_DIR/bench/rules.jsonl" "$OUT/rules-$i.jsonl"
  for f in "$RUN_DIR"/*.yaml; do [[ -f "$f" ]] && cp "$f" "$OUT/$(basename "${f%.yaml}")-$i.yaml"; done
  ls -t "$RUN_DIR"/.snakemake/log/*.snakemake.log 2>/dev/null | head -1 | xargs -I{} cp {} "$OUT/snakemake-$i.log" || true
  (cd "$RUN_DIR" && du -sk -- */ 2>/dev/null) > "$OUT/du-$i.txt" || true
  du -sk "$RUN_DIR" | awk '{print $1"\tTOTAL"}' >> "$OUT/du-$i.txt"
done

python3 "$SCRIPT_DIR/write_provenance.py" \
  --out "$OUT/provenance.json" --repo "$REPO_DIR" --run-id "$RUN_ID" \
  --algo "$ALGO" --input "$INPUT" --variant "$VARIANT" --conc "$CONC" \
  --memory="${MEM_FLAGS[*]+${MEM_FLAGS[*]}}" --cpu="${CPU_FLAGS[*]}" --image "$IMAGE" \
  --uvicorn-workers "$UVICORN_WORKERS" --sample-interval "$SAMPLE_INTERVAL" \
  --probe "interval=${PROBE_INTERVAL}s timeout=${PROBE_TIMEOUT}s" --timeout "$TIMEOUT" \
  --t-runs-start "$T_RUNS_START" --t-runs-end "$T_RUNS_END" \
  --container-state "$CONTAINER_STATE" ${OVERRIDES[@]+--overrides "${OVERRIDES[@]}"} \
  ${EXTRA_ENV[@]+--extra-env "${EXTRA_ENV[@]}"}

kill "$PROBE_PID" 2>/dev/null; wait "$PROBE_PID" 2>/dev/null || true; PROBE_PID=""
python3 "$SCRIPT_DIR/summarize.py" "$OUT" > "$OUT/summary.md" || echo "summarize failed" >&2

if [[ $KEEP_OUTPUTS -eq 0 ]]; then
  for i in $(seq 1 "$CONC"); do rm -rf "${DATA_DIR:?}/output/$WORKSPACE/${RUN_ID}-${i}"; done
fi
echo "== done: $OUT (container: $CONTAINER_STATE)"
exit "$RC_ALL"
