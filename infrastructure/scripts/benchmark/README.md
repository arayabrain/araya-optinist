# Workflow benchmark

Measures what a workflow run actually needs, per snakemake rule: peak memory, bytes written,
time to import vs. time to compute, and whether it completed. In parallel it records the
container's memory once per second and probes the API's `/health` once per second.

One benchmark container stands in for one application task: the API and the workflow run
share the container's cgroup, so the API's own memory counts against the same limit, as it
does in a deployed task.

A run starts in one of two ways (`--start`):

- **`api`**: `POST /run`, as the web UI does. A uvicorn worker builds the run and is its
  snakemake parent, as in a deployed task. Use it for figures read against a ceiling.
- **`exec`** (default, as in the earlier baseline rows): a driver process per run calls
  `WorkflowRunner` itself and is the snakemake parent. It is an extra app-sized process in
  the container, which a deployed run does not have; each run records its memory
  (`harness_process` in `run-N.log`, and in `summary.md`).

## What it does not measure

- **Wall clock comparable with a deployment, on an Apple Silicon host.** The image is
  `linux/amd64` and runs under emulation there. Memory and bytes written are comparable;
  times are not. On native x86_64 Linux (the AWS lane below) times are comparable too.
- **Swap behaviour on Docker Desktop.** Docker Desktop cannot provide the host swap a
  deployed instance has. Variant P runs at the tier's memory ceiling with swap disabled,
  which shows the failure point instead of a swap stall. Variant S needs host swap.
- **The cost of conda activation on a shared file system.** Conda envs are on a local
  volume here.
- **The web UI's result polling.** Neither start mode calls `/run/result` during a run; the
  `api` mode watches the run's `experiment.yaml` instead.

## Prerequisites

- Docker, and the backend image (default `development-optinist-for-cloud:latest`, override
  with `BENCH_IMAGE`). The repository is mounted over `/app`, so **the code under test is
  this checkout's working tree**; the image supplies the Python packages.
- cgroup v2 in the container (Docker Desktop, Amazon Linux 2023). A row stops before its runs
  if the sampler reads no `memory.current` or `anon`; `memory.peak` needs Linux 5.19 or later
  and shows as n/a otherwise.
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
  [--cpus 2 | --cpuset 0-4] [--timeout 10800] [--keep-outputs] [--start exec|api]
```

| Option | Meaning |
|---|---|
| `--algo` | Fixture in `fixtures/`: `suite2p` (file_convert → registration → roi), `caiman` (mc → cnmf), `lccd`, `vacant_roi` (suite2p registration, then that node), `multisession`. A comma-separated list starts one run per fixture, together (`suite2p,caiman`) |
| `--variant U` | No memory limit — measures demand |
| `--variant P` | The tier's container ceiling, swap disabled — shows what fails at that limit |
| `--variant S` | The tier's container ceiling plus its swap allowance — needs host swap |
| `--conc N` | N runs of the fixture started together in the same container (with a fixture list: the list's length) |
| `--set node.path=value` | Override one default parameter (dotted path into the node's default yaml); `nwb.path=value` overrides the NWB defaults |
| `--env KEY=VALUE` | Extra environment variable for the container. Recorded as given in `provenance.json` and `summary.md`, which the AWS lane copies to S3: pass no secrets |
| `--cpus` / `--cpuset` | CPU limit (default `--cpus 2`). `--cpuset` changes what the process sees as available CPUs, which caiman's `n_processes` clamp reads |
| `--timeout` | Per-run cap in seconds (default 3 h). In `api` mode the run is then cancelled through the API until it stops; in `exec` mode the signal stops the run's rules, and the snakemake parent the driver forked, which outlives it, is stopped next. Either way the row waits up to 2 min for the workflow processes to end and records what was left |
| `--keep-outputs` | Keep the runs' output directories |
| `--start exec\|api` | How runs start (above); default `exec` |

| Environment | Meaning |
|---|---|
| `BENCH_MEMORY_MIB`, `BENCH_SWAP_MIB` | Ceiling and swap allowance of variants P and S. Defaults: the free tier's `container_memory_mib` / `container_max_swap_mib` defaults in `infrastructure/terraform/main.tf`. Variant S stops unless the container sees at least `BENCH_SWAP_MIB` of host swap (1 % slack); on Docker Desktop, set it to the VM's swap |
| `BENCH_UVICORN_WORKERS` | API workers (default 5, as in the earlier baseline rows); set it to the worker count of the tier being modelled |
| `BENCH_LANE` | Lane name in the row ID and provenance (default `local`) |

Only one row runs at a time (the container name is fixed). A second row started meanwhile
stops without touching the first; so does a retry within the same minute (the row directory
exists).

## Inputs of other lengths

`make_large_input.py <src.tif> <dst.tif> --repeat 2` repeats a recording's frames, streaming
one page at a time; `--frames N` uses only its first N frames (`--repeat 1 --frames N` makes a
shorter input). Keep the JSON it prints with the results; frame repetition is a caveat for
ROI detection, not for registration memory.

## Output of a row

`<results-dir>/<YYYYMMDD-HHMM>-<lane>-<algo[+algo]>-<input>-<U|P|S>-c<N>[-label]/`

| File | Content |
|---|---|
| `provenance.json` | Git SHA and dirty files, fixture and overrides, input bytes and SHA-256, image ID, memory and CPU flags, uvicorn workers, sampling intervals, host |
| `rules-N.jsonl` | One line per rule: `peak_rss_mb` (kernel high-water mark, not sampled), `write_bytes`, process start / imports done / end times, per-step peaks (`phases`), and `status`, the rule process's own outcome. A function that fails writes its error as the rule's output, so its process still ends `ok`; the node's result is in the run's experiment record, and `summary.md` shows it per rule. The record is written when the run ends, so in a timed-out row finished nodes can still read `running` |
| `cgroup.tsv` | Container memory, peak, swap, OOM-kill count, CPU — every second, read with shell builtins inside the container (no process per sample) |
| `probe.tsv` | `/health` status and latency every second (failure = non-200 or 10 s timeout) |
| `processes-after-runs.json` | Workflow processes still running once every run had ended (a timed-out run still stopping), and how long they took to go |
| `summary.md` | The above reduced to tables |
| `run-N.log`, `snakemake-N.log`, `api.log`, `*-N.yaml`, `du-N.txt` | Raw logs, the run's workflow / snakemake / experiment yaml, output size per node |

## Reading the results

- **Demand is the container's peak `anon`** (process memory, API included), not `memory.peak`:
  the latter includes page cache, which the kernel reclaims under pressure. For what the runs
  add, subtract the idle `anon` baseline (`summary.md` gives the rise), not the idle
  `memory.current`.
- **When the container swapped, read peak `anon + swap`** (per sample, in `summary.md`):
  swapped-out pages leave `anon`, so peak `anon` alone is then a lower bound.
- **A gap in the samples makes the sampled peaks lower bounds.** `summary.md` gives the longest
  gap during the runs and says so when it exceeds three intervals.
- **In `exec` rows, demand includes one driver process per run.** `summary.md` gives each
  driver's memory under the runs. It is an upper bound on the excess, not the excess: in a
  deployment the uvicorn worker that runs the workflow grows too. Use `--start api` for figures
  read against a ceiling. Rows from before `--start` existed were `exec` rows.
- **Per-rule and per-step peaks are the rule process's own RSS.** They include file-backed pages
  (memmapped data such as suite2p's `.bin` and CaImAn's `.mmap`, shared libraries), which are
  reclaimable and not in `anon`, and they leave out child processes: `children_peak_rss_mb` is
  the largest finished child, not a sum, so a CaImAn worker pool (`n_processes` > 1) is not in
  them. Use the container's figures for demand, and the per-rule ones for where it arises.
- **A rule that was killed has no record** (the OOM killer, a cancel): it writes its record as it
  ends. `summary.md` lists the run's nodes without one, and `rule_windows.py` labels the window
  after the last record accordingly.
- **A timed-out run** is stopped, and the row records whether it did: in `api` mode
  `BENCH_RESULT` gives `cancelled` and `still_running`; in both modes
  `processes-after-runs.json` gives the workflow processes left when the runs ended and how
  long they took to go. In a concurrent row, a run still stopping counts in the other runs'
  figures.
- **`/health` stalls are measured in seconds**, from the first failed probe to the next answer: a
  failing probe waits up to its 10 s timeout, so the count of failed probes understates a stall.
- **If the container swapped, the figures are lower bounds.** Under variant U the only bound is
  the Docker VM; when `Peak swap` is non-zero, raise the VM memory and rerun. On Docker Desktop
  a memory change takes effect only after quitting and restarting Docker Desktop (its Restart
  menu item is not enough); check with `docker info --format '{{.MemTotal}}'`.
- Container-level write bytes exclude writes to bind mounts on Docker Desktop; use the per-rule
  `write_bytes` and `du-N.txt`. Rows sampled before 2026-11-05 stop counting I/O at 2 GiB
  (the sampler then summed in 32 bits); `summary.md` shows them as n/a.
- `rule_windows.py <row dir> [...]` gives the container's peak `anon` within each rule's window,
  beside the rule's own peak RSS, time and bytes written. A window starts at the rule's process
  start, so for a short rule its peak can be the previous rule's exit; check the rule's own RSS.
- The cgroup sampler reads once per second and can miss spikes shorter than that; the per-rule
  `peak_rss_mb` is the kernel's high-water mark and does not.

## On AWS (real-infrastructure lane)

`aws/bootstrap.sh` prepares a standalone Amazon Linux 2023 x86_64 instance from user data:

- schedules a self-shutdown;
- installs Docker;
- sets up host swap on `BENCH_SWAP_DEVICE`, if given: an empty volume under a stable name
  (`/dev/disk/by-id/…` or the EBS mapping's `/dev/sdX` link); a mounted, non-empty or root
  device is refused;
- clones `BENCH_GIT_REF` (default `develop-main`) and pulls the image from ECR;
- fetches the inputs from S3.

`aws/run_cases.sh` then runs the case list at the tier's ceiling with its swap allowance
(variant S), each run started through the API (`--start api`), and copies each finished row
to S3: one suite2p run as the reference, two
suite2p runs together, suite2p beside CaImAn, and one run at about twice the length.
`BENCH_SHORT_FRAMES` adds the two concurrent rows on an input cut to that many frames.

`bootstrap.sh` does not start it: start it once bootstrap has finished (for example with SSM
Run Command), and set its environment in that command, not in the user data:
`BENCH_S3_PREFIX`, `BENCH_UVICORN_WORKERS` (and `BENCH_MEMORY_MIB` / `BENCH_SWAP_MIB` if they
differ from the defaults) for the tier being modelled, and optionally `BENCH_SHORT_FRAMES` and
`BENCH_ROW_TIMEOUT`.

It ends with `ALL-ROWS-DONE` (exit 0) only when every row wrote its summary and every result
is in S3. Otherwise it ends with exit 1:
- `ROWS-INCOMPLETE` names the rows that stopped before measuring, including both short rows
  when their input could not be built (`SHORT-INPUT-FAILED`);
- `SYNC-FAILED` follows a failed copy, which the next copy retries; after the last copy it
  means some results are only on the instance.

`ROWS-WITH-FAILED-RUNS` lists the rows whose runs did not all succeed. Such a row is a result.

On native x86_64 Linux, wall clock is comparable with a deployment, and `provenance.json`
records that and the EC2 instance type.

## How per-rule recording works

`studio/app/common/core/rules/benchmark_recorder.py`, wrapped around each rule script's
`main()`, with named steps (`phase()`) inside `Runner.run`. It is inert unless
`OPTINIST_BENCHMARK=1`, which only `run_bench.sh` sets: each entry point then returns after
one environment lookup, without touching any file.

## References

- #893: the benchmark's design and its first measurements.
