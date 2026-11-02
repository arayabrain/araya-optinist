# Workflow benchmark

Measures what a workflow run actually needs, per snakemake rule: peak memory, bytes written,
time to import vs. time to compute, and whether it completed. In parallel it records the
container's memory once per second and probes the API's `/health` once per second.

One benchmark container stands in for one application task: the API and the workflow run
share the container's cgroup, so the API's own memory counts against the same limit, as it
does in a deployed task.

## What it does not measure

- **Wall clock comparable with a deployment, on an Apple Silicon host.** The image is
  `linux/amd64` and runs under emulation there. Memory and bytes written are comparable;
  times are not. On native x86_64 Linux (the AWS lane below) times are comparable too.
- **Swap behaviour on Docker Desktop.** Docker Desktop cannot provide the host swap a
  deployed instance has. Variant P runs at the tier's memory ceiling with swap disabled,
  which shows the failure point instead of a swap stall. Variant S needs host swap.
- **The cost of conda activation on a shared file system.** Conda envs are on a local
  volume here.

## Prerequisites

- Docker, and the backend image (default `development-optinist-for-cloud:latest`, override
  with `BENCH_IMAGE`). The repository is mounted over `/app`, so **the code under test is
  this checkout's working tree**; the image supplies the Python packages.
- `../optinist-docker-volumes/.snakemake` — conda envs (built on first use if missing; run a
  throwaway row first so env creation is not measured). `BENCH_VOLUMES_DIR` points
  elsewhere, e.g. from a git worktree.
- About 35 GB free for the largest row. Outputs are deleted after each row.

## Running a row

```sh
infrastructure/scripts/benchmark/run_bench.sh \
  --algo suite2p --input /path/to/input.tif --variant U \
  --results-dir /path/to/results [--conc 2] [--label R3] \
  [--set caiman_cnmf.advanced.patch_params.n_processes=4] \
  [--cpus 2 | --cpuset 0-4] [--timeout 10800] [--keep-outputs]
```

| Option | Meaning |
|---|---|
| `--algo` | Fixture in `fixtures/`: `suite2p` (file_convert → registration → roi), `caiman` (mc → cnmf), `lccd`, `vacant_roi` (suite2p registration, then that node), `multisession`. A comma-separated list starts one run per fixture, together (`suite2p,caiman`) |
| `--variant U` | No memory limit — measures demand |
| `--variant P` | The tier's container ceiling, swap disabled — shows what fails at that limit |
| `--variant S` | The tier's container ceiling plus its swap allowance — needs host swap |
| `--conc N` | N runs of the fixture started together in the same container (with a fixture list: the list's length) |
| `--set node.path=value` | Override one default parameter (dotted path into the node's default yaml); `nwb.path=value` overrides the NWB defaults |
| `--env KEY=VALUE` | Extra environment variable for the container |
| `--cpus` / `--cpuset` | CPU limit (default `--cpus 2`). `--cpuset` changes what the process sees as available CPUs, which caiman's `n_processes` clamp reads |
| `--timeout` | Per-run cap in seconds (default 3 h) |
| `--keep-outputs` | Keep the runs' output directories |

| Environment | Meaning |
|---|---|
| `BENCH_MEMORY_MIB`, `BENCH_SWAP_MIB` | Ceiling and swap allowance of variants P and S. Defaults: the free tier's `container_memory_mib` / `container_max_swap_mib` defaults in `infrastructure/terraform/main.tf` |
| `BENCH_UVICORN_WORKERS` | API workers (default 5, as in the earlier baseline rows); set it to the worker count of the tier being modelled |
| `BENCH_LANE` | Lane name in the row ID and provenance (default `local`) |

Only one row runs at a time (the container name is fixed).

## Larger inputs

`make_large_input.py <src.tif> <dst.tif> --repeat 2` repeats a recording's frames, streaming
one page at a time. Keep the JSON it prints with the results; frame repetition is a caveat for
ROI detection, not for registration memory.

## Output of a row

`<results-dir>/<YYYYMMDD-HHMM>-<lane>-<algo[+algo]>-<input>-<U|P|S>-c<N>[-label]/`

| File | Content |
|---|---|
| `provenance.json` | Git SHA and dirty files, fixture and overrides, input bytes and SHA-256, image ID, memory and CPU flags, uvicorn workers, sampling intervals, host |
| `rules-N.jsonl` | One line per rule: `peak_rss_mb` (kernel high-water mark, not sampled), `write_bytes`, process start / imports done / end times, status, and per-step peaks (`phases`) |
| `cgroup.tsv` | Container memory, peak, swap, OOM-kill count, CPU — every second |
| `probe.tsv` | `/health` status and latency every second (failure = non-200 or 10 s timeout) |
| `summary.md` | The above reduced to tables |
| `run-N.log`, `snakemake-N.log`, `api.log`, `*-N.yaml`, `du-N.txt` | Raw logs, the run's workflow / snakemake / experiment yaml, output size per node |

## Reading the results

- **Demand is the container's peak `anon`** (process memory, API included), not `memory.peak`:
  the latter includes page cache, which the kernel reclaims under pressure.
- **Per-rule RSS overstates caiman**: its memmapped file pages count as RSS but are reclaimable.
  Use the container's peak `anon` for caiman.
- **If the container swapped, the figures are lower bounds.** Under variant U the only bound is
  the Docker VM; when `Peak swap` is non-zero, raise the VM memory and rerun. On Docker Desktop
  a memory change takes effect only after quitting and restarting Docker Desktop (its Restart
  menu item is not enough); check with `docker info --format '{{.MemTotal}}'`.
- Container-level write bytes exclude writes to bind mounts on Docker Desktop; use the per-rule
  `write_bytes` and `du-N.txt`.

## On AWS (real-infrastructure lane)

`aws/bootstrap.sh` prepares a standalone Amazon Linux 2023 x86_64 instance from user data:

- schedules a self-shutdown;
- installs Docker;
- sets up host swap on `BENCH_SWAP_DEVICE`, if given;
- clones `BENCH_GIT_REF` (default `develop-main`) and pulls the image from ECR;
- fetches the inputs from S3.

On native x86_64 Linux, wall clock is comparable with a deployment, and `provenance.json`
records that and the EC2 instance type.

## How per-rule recording works

`studio/app/common/core/rules/benchmark_recorder.py`, wrapped around each rule script's
`main()`, with named steps (`phase()`) inside `Runner.run`. It is inert unless
`OPTINIST_BENCHMARK=1`, which only `run_bench.sh` sets: each entry point then returns after
one environment lookup, without touching any file.

## References

- #893: the benchmark's design and its first measurements.
