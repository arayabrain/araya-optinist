# Task brief: PR 938 follow-up, plan-time pickle invalidation and the rerun lock

You are an engineer agent with a fresh clone of `github.com/arayabrain/araya-optinist` and no
other context. Everything you need is in this file: background, the chosen design, a working
prototype patch (Appendix A), the probes that back the analysis (Appendix B), the remaining work
with acceptance criteria, how to test, house rules, and a PR body skeleton. Read it all before
touching code.

Note on characters: this brief, the embedded diffs and the embedded probes are plain ASCII. Keep
it that way in everything you write (section 9).

---

## 0. Deliverable at a glance

- New branch `fix/run-result-plan-invalidation`, created with `--no-track` from
  `origin/fix/run-result-blocking-save` (the branch of PR #938, expected head
  `6aac20ec71fab219483504ea5c4a5df467296465`, short `6aac20ec7`).
- A **draft** PR from that branch into `fix/run-result-blocking-save` (not into `develop-main`).
- Content: the "round 5" rerun lock (section 1.3) plus the root-cause fix "(d) + (e) + (W)"
  (section 2), finished from the prototype in Appendix A, with the tests, measurements and checks
  in section 6.
- Nothing else: no merge, no force-push, no push to any other branch, no comments or replies on
  PR #938 or its review threads (the user answers those).

Setup:

```bash
git fetch origin fix/run-result-blocking-save
git rev-parse origin/fix/run-result-blocking-save   # expect 6aac20ec71fab219483504ea5c4a5df467296465
git switch --no-track -c fix/run-result-plan-invalidation origin/fix/run-result-blocking-save
```

If the head is not `6aac20ec7`, read section 5.3 before applying anything.

---

## 1. Background

### 1.1 What PR 938 does (your base branch)

PR #938, "Fix /run/result blocking the API while saving large outputs", base `develop-main`.
Its commits on top of develop-main: `10bf3218c` (save once, off the loop), `593ea5378` (faster
timeseries JSON), `b59f4ee05` (worker-process save, observe lock), `d6f6618ac` (empty recorded
outputPaths count as saved), merge `9d571198b`, `6aac20ec7` (cancelled poll keeps the loop free
and the lock held).

How results reach the UI:

- The frontend polls `POST /run/result/{workspace_id}/{uid}` (`run_result` in
  `studio/app/common/routers/run.py`) every 10 s (`POLLING_INTERVAL = 10000` in
  `frontend/src/store/slice/Pipeline/PipelineHook.ts`) with the node ids it still treats as
  pending. It never re-polls a node it already holds as success, and stops polling on CANCELED.
- Each poll calls `WorkflowResult.observe(...)` in
  `studio/app/common/core/workflow/workflow_result.py`. It builds a `NodeResult` (or
  `PostProcessResult`) per node; the constructor reads the node's output pickle, written by the
  snakemake rule process. A dict pickle means the node is ready; a list/str pickle is an error.
- `NodeResult.observe` turns each `BaseData` in a ready pickle into JSON for the UI
  (`save_json`), then records `success` and `outputPaths` into `experiment.yaml` through
  `ExptConfigWriter.overwrite`.

On develop-main every poll re-saved every ready node's JSON inside the async handler, which
blocked the event loop. PR 938 changed that:

- **Save once.** `NodeResult.observe` skips the save when the yaml already records the node as
  `success` with non-None `outputPaths` (the "success already recorded" branch).
- **Worker process.** The save runs in `ProcessPoolExecutor(max_workers=1)`. The worker function
  `_save_node_outputs` re-reads the pickle itself instead of receiving it through the pipe.
  `_await_despite_cancel` waits out the worker if the poll is cancelled.
- **Observe lock.** `WorkflowResult.observe_lock()` is a `FileLock` per workflow directory.
  `run_result` acquires it non-blocking; a poll that overlaps a save answers `{}`.
- **600 s finalization fallback.** After snakemake exits, the snakemake child process
  (`_snakemake_execute_process` in `studio/app/common/core/snakemake/snakemake_executor.py`)
  runs `observe_overall()` through `_observe_overall_with_lock_retry`. `observe_overall()` waits
  up to `OBSERVE_LOCK_WAIT_SECONDS = 600` for the lock and then observes without it. After that
  the child registers the DB record, updates data usage and handles remote status.

### 1.2 Same-UID rerun mechanics (verified)

- `run_id` (`POST /run/{workspace_id}/{uid}`) constructs a `WorkflowRunner`. Its constructor
  rewrites experiment.yaml (`ExptConfigWriter.write`, `build_function_from_nodeDict`): every
  algorithm node becomes `running` with `outputPaths: None`, input nodes become `success`, and
  `started_at` gets a new value. `WorkflowRunner.run_workflow` then deletes the outputs of forced
  nodes and their downstream (`delete_dependencies`), always forces `post_process`
  (`delete_procs_dependencies`), and schedules `snakemake_execute` as a FastAPI background task.
- The frontend forces only algorithm nodes whose params changed or that errored
  (`RunSelectors.selectForceRunList`). The real snakemake run uses only
  `DAGSettings(forceall=...)`; "forced" nodes are implemented by deleting their outputs.
- Snakemake re-runs non-forced jobs on its own when input mtime, params, code or the software env
  changed. Routine triggers: an input file or hdf5/mat path change, edge rewiring, NWB settings
  (data rules carry `nwbfile`), a deploy that changes `runner.py`, `func.py` or `file_writer.py`,
  a conda yaml or param-yaml default change, an S3 full restore (fresh mtimes in arbitrary order),
  and experiment copies (snakemake metadata is keyed by absolute output path). The last two are
  from code reading, not probed.
- Snakemake 9.10.1 (locked in `poetry.lock`; `pyproject.toml` allows `>=9, <10`) deletes a job's
  old output only when that job starts (`Job.prepare` -> `remove_existing_output` in
  `snakemake/jobs.py`). Until then the previous run's pickle is on disk. Probed: while rule `a`
  re-ran, the old downstream `b.out` still existed.
- STOP: `WorkflowMonitor.cancel_run` deletes the job's snakemake script and SIGTERMs the job pid
  from `pid.json`; statuses are untouched. Snakemake fails the job and raises; the child logs to
  `error.log`, deletes the remote sync lock, finalizes in error mode (no saves; running nodes
  become error; success nodes untouched), registers the record and writes remote sync status
  ERROR. The frontend sets CANCELED at once and re-enables Run, so a same-UID rerun can start
  before the old child finalizes.
- Process death: no finalization; polls get a dummy process for up to
  `PROCESS_SNAKEMAKE_WAIT_TIMEOUT` (7200 s) after the pid timestamp, then the "No Snakemake
  process found" pass marks pending nodes error (`WorkflowMonitor.search_process`).

### 1.3 Round 5: the rerun lock (reviewed, uncommitted, carried by this PR)

Copilot thread `4278546559` on #938: with the save behind an await, a same-UID rerun could reset
experiment.yaml and delete forced nodes' outputs while an old poll was mid-save; the old save
then recorded its result as the new run's success, and save-once made it permanent.

Round 5 fixes it: `run_id` holds `observe_lock()` (non-blocking) across `WorkflowRunner(...)`,
`ensure_input_data_local()` and `run_workflow()`. If a save holds the lock, `run_id` answers
423 "The previous run's results are still being saved." (the frontend shows its generic
"Failed to Run workflow" snackbar; Run works again once the save ends). The `observe_lock`
docstring names the rerun reset as a holder. Regression test
`test_a_rerun_mid_save_is_refused_so_the_save_cannot_land_in_it` plus a `rerun()` helper in
`studio/tests/app/common/core/workflow/test_workflow_result_save_once.py`. It was mutation-checked
locally: without the lock in `run_id`, only that test fails ("DID NOT RAISE"), 5 others pass.
A generation check was rejected at the time as the round-5 fix; it comes back below as (e), for a
different purpose. Full diff: Appendix A, Patch A.

### 1.4 The problems this PR fixes

- **M2, the root cause (a regression against develop-main).** On a rerun where snakemake re-runs
  a non-forced node, a poll arrives before snakemake reaches that node, finds the previous run's
  pickle, saves it and records success. Save-once means nothing saves that node again, so its
  JSON and yaml `outputPaths` stay from the previous run for good, while the pickle and the NWB
  hold the new result. develop-main showed the same premature success during the run but
  re-saved at a finalization without workflow error, so its JSON healed (its yaml `data_shape`
  stayed stale). Probe, func1 changing from value 0.0 with 3 rows to 1.0 with 5 rows:
  develop-main ends with JSON 1.0 and yaml shape [3, 10]; PR 938 with JSON 0.0 and [3, 10];
  the prototype with JSON 1.0 and [5, 10].
- **M1, the rerun part of Copilot thread `4278546530`.** After the 600 s fallback, finalization
  observes without the lock. Once the stuck poll releases it, a rerun can take it and reset the
  config, and the lockless finalization then writes the previous run's success into the rerun.
  Probed: "after finalization: success {... data_shape=[3, 10]}" lands in the rerun.
  The same-generation part of that thread (two savers for one node after the timeout) is
  deliberately unchanged: both savers re-read the same pickle and write identical files, the
  stuck poll rewrites last, and waiting indefinitely would park DB registration behind a hung
  worker. Keep the fallback.
- **W, new in PR 938.** The worker re-reads the pickle. If snakemake deletes it (`Job.prepare`)
  or replaces it with an error pickle between the poll's read and the worker's, `success()`
  iterates `None.items()` or `list.items()`, `run_result` answers 500 "Failed to result
  workflow.", and the frontend's `pollRunResult.rejected` reducer
  (`frontend/src/store/slice/Pipeline/PipelineSlice.ts`) sets the run to ABORTED and stops
  polling. develop-main saved from the copy it had read. Probed (both cases answer 500).
- **5b, pre-existing, fixed by (e).** STOP, then an immediate rerun: the cancelled run's late
  finalization marks the new run and its nodes error. Probed on develop-main and PR 938.
- **Premature success, pre-existing.** The first poll after a non-forced rerun reports nodes as
  success from the previous pickles; they turn green and open old plots before snakemake ran
  them. develop-main also leaves a later-erroring node as success and a node cancelled mid-job as
  success with no pickle. Fixed by (d).

---

## 2. The chosen design: (d) + (e) + (W)

### 2.1 (d) Plan-time invalidation behind a poll gate

1. `run_id`, as the first statement inside the round-5 observe lock, writes the marker
   `<workflow_dir>/.plan_pending` (`WorkflowResult.mark_plan_pending()`).
2. While the marker exists, `WorkflowResult.observe` passes `plan_pending=True` to
   `NodeResult` / `PostProcessResult`, which then skip the pickle read. Every node is "not ready"
   and the poll reports nothing for it. Workflow-error mode is unaffected (that branch never reads
   pickles), and the no-process second pass passes `plan_pending=False`.
3. The snakemake child, before its real `SnakemakeApi` session, asks snakemake for its plan with
   a dry run through the public API (`planned_outputs(smk_workdir, params)`, exactly as
   prototyped):

   ```python
   def planned_outputs(smk_workdir: str, params: SmkParam) -> List[str]:
       """The outputs of every job a run with these params would execute"""
       deployment_methods = (
           [DeploymentMethod.CONDA] if getattr(params, "use_conda", True) else []
       )
       planned = []
       with SnakemakeApi(OutputSettings(quiet={Quietness.ALL})) as snakemake_api:
           snakemake_api.workflow(
               snakefile=Path(DIRPATH.SNAKEMAKE_FILEPATH),
               workdir=Path(smk_workdir),
               storage_settings=StorageSettings(),
               resource_settings=ResourceSettings(cores=getattr(params, "cores", 1)),
               deployment_settings=DeploymentSettings(
                   deployment_method=deployment_methods,
                   conda_frontend="conda",
                   conda_prefix=DIRPATH.SNAKEMAKE_CONDA_ENV_DIR,
               ),
           ).dag(
               dag_settings=DAGSettings(forceall=getattr(params, "forceall", False))
           ).execute_workflow(executor="dryrun", updated_files=planned)
       return [str(f) for f in planned]
   ```

   (`Quietness` comes from `snakemake.settings.enums`.) In snakemake 9.10.1 `Workflow.execute`
   fills `updated_files` with the outputs of every needrun job right after building the DAG,
   before conda env creation and scheduling. A separate dry-run session in the same process
   returns the same plan as the real run without running jobs (verified on a toy Snakefile and
   through the real optinist Snakefile, Appendix B).
4. `invalidate_planned_outputs(workspace_id, unique_id, outputs)` takes the observe lock
   (`timeout=OBSERVE_LOCK_WAIT_SECONDS`), deletes every planned `.pkl` that exists, and removes
   the marker (`clear_plan_pending()`). From then on any pickle a poll can see is either this
   run's result (a planned job) or the valid result of a job snakemake will skip, so save-once is
   correct by construction.
5. If planning raises, the child logs a warning and leaves the gate up: polls show nothing and
   finalization (which observes everything) delivers the results. Safe, slower UX.
6. `observe_overall()` clears the marker before observing (snakemake has exited, so every pickle
   left is this run's).

### 2.2 (e) Generation guard on `started_at`

- The child captures experiment.yaml `started_at` when it starts (`_started_at()`, tolerant:
  None on any read error, which disables the guard; a strict read broke 10 mocked tests).
- After snakemake exits, if `started_at` changed (a rerun reset the config), the child skips
  finalization.
- `observe_overall(generation)` re-checks after taking (or timing out on) the lock and returns
  `{}` on a mismatch, before clearing the marker.
- Polls use the `started_at` they read at observe start. Every yaml write from an observation
  goes through `ExptConfigWriter.overwrite(update, started_at=generation)`, which re-reads the
  config inside the same yaml `FileLock` that the reset (`ConfigWriter.write`) takes and drops
  the write when `started_at` differs. Check-and-write is therefore atomic against the reset.
- `started_at` has second resolution (`DATE_FORMAT = "%Y-%m-%d %H:%M:%S"`). Enough, because a
  rerun starts after the previous run started.
- (d) needs (e): without it a cancelled run's late finalization clears the new run's gate.

### 2.3 (W) Worker robustness

`_save_node_outputs` returns None unless `PickleReader.check_is_valid_node_pickle(...)` holds for
the pickle it re-read (a dict). `NodeResult.observe` then returns None, and `__observe_nodes`
leaves that node out of this poll's results. The next poll sees the new state (no pickle: not
ready; error pickle: error). Probed: both race cases answer `{}` instead of 500.

### 2.4 Round 5 stays as is

The round-5 lock is where the marker is written. Its 423 behaviour does not change.

---

## 3. Scenario matrix

Legend: OK = JSON, yaml outputPaths and status all match this run. HEAL = stale during the run,
correct after finalization. STALE = JSON stays from the previous pickle. WS = wrong status
(success for a node that errored or never produced a pickle). PS = premature success visible
during the run. (P) = probe-backed; everything else is traced through code only.

| # | Scenario | develop-main | PR 938 + round 5 | (d)+(e)+(W) | Basis of the (d) cell |
|---|---|---|---|---|---|
| 1 | Fresh run | OK (re-saves every poll) | OK | OK | code |
| 2 | Rerun, nothing changed | OK (P) | OK (P) | OK | probe S2 |
| 3 | Rerun, changed params (forced) | OK | OK | OK | code |
| 4 | Rerun, snakemake re-runs non-forced nodes (M2) | PS, HEAL JSON, yaml shape stale (P) | PS, STALE (P) | OK | probe S4 |
| 5a | STOP and leave | PS, STALE, WS (P) | PS, STALE, WS (P) | OK | probe S5 |
| 5b | STOP, rerun before the old child finalizes | rerun marked error (P) | rerun marked error (P) | OK, gate kept | probe S5b |
| 5c | STOP while a poll saves | premature save | premature save; 500 if the job starts mid-read (P) | OK (gate + W) | W probe; gate by code |
| 6 | A downstream node errors | upstream HEAL, errored node WS (P) | STALE, WS (P) | OK | probe S6 |
| 7 | Snakemake dies (OOM) | PS, STALE, WS (P) | PS, STALE, WS (P) | OK, error after the 7200 s no-process path | probe S7 |
| 8 | 600 s lock timeout, then a rerun (M1) | n.a. (no lock) | finalization lands in the rerun (P) | OK | probe M1 |
| 9 | Remote storage | S3 may keep stale JSON | S3 keeps stale JSON | OK once the marker stays out of S3 (R1) | code |
| 10 | Copy / reproduce / dataview, then rerun | as 4 | as 4 | OK | code |
| 11 | Input nodes | saved at every finalization | same | same; planned input pickles deleted early | code |
| 12 | Several uvicorn workers | per-request saves race | file lock, cross-process | file lock and file marker, cross-process | code |

Probe outputs for the prototype: S4 func1 JSON 1.0, pickle 1.0, yaml success [5, 10], and
`early={} first={}` (no poll reported anything before snakemake reached the node). S5 func2 yaml
error / None. S6 func2 error. S7 func2 error. S5b rerun and both nodes stay running, gate kept.
M1 func1 stays running / None after the stale finalization. W both cases answer `{}`.
Plan probe through the real Snakefile on the `smk_exec_lccd` fixture: untouched input plans
nothing (first SnakemakeApi call in a process 4.1 to 5.5 s with `use_conda=True`, later calls
0.07 to 0.10 s); after touching the input tif it plans
`input_0/data_endoscope.pkl`, `lccd_cell_detection_1234/lccd_cell_detection.pkl`,
`pca_1234/pca.pkl` in 0.08 to 0.10 s; after `invalidate_planned_outputs` all three are gone and
the marker is cleared.

Limits of that evidence: the matrix probes simulate snakemake's file operations (write and unlink
pickles in the order `Job.prepare` does) against the real `WorkflowResult`, `run_id` and
`WorkflowRunner` code. Only the plan probe runs the real Snakefile, and only as a dry run. Nothing
has been run end to end with real jobs and live polls (R5).

---

## 4. Why (d), not (a), (b) or (c)

- **(a) Finalization re-saves every success node** (bypass save-once at finalization and refresh
  yaml outputPaths). Heals only runs that finish without a workflow error (rows 4 and the upstream
  node in 6). Premature success, the errored-node WS in 6, and every STOP / OOM row stay broken
  as on develop-main. Costs one extra save per node at every finalization, under the observe
  lock, delaying record registration and "Workflow finished" (indicative save_json cost 1.1 s for
  10 x 107k and 7.4 s for 50 x 107k, measured under amd64 emulation). Also needs a forced upload,
  because when a poll made the last node finished, that poll already uploaded and finalization
  returns early. Kept as the fallback if the team wants #938 minimal: (a) + (e) + (W), with (d)
  as a follow-up.
- **(b) Pickle identity check** (record `(st_ino, st_mtime_ns, st_size)` from `os.fstat` on the
  file object the worker unpickled; re-save when it differs). Same coverage as (a), since the
  frontend never re-polls a success node and only finalization re-checks. Needs a sidecar file or
  a new ExptFunction field (schema change, leaks into the experiments API). Not prototyped.
- **(c) Accept and document.** A regression against develop-main in routine reruns (deploys,
  input or NWB changes, restores, copies): plots and outputPaths would permanently disagree with
  the pickle and the NWB download. Rejected.
- **(d) chosen**: correct by construction for every probed row, at the cost of one extra DAG
  build in the background child per run and a short window (until the plan lands) where polls
  show nothing; the first frontend poll is 10 s after the start.
- Rejected variants of (d): planning synchronously in `run_id` (needs a subprocess and executor to
  keep the loop free, holds the round-5 lock for the DAG build, delays the response); a `list`
  subclass whose `extend` runs the invalidation inside the real `execute_workflow` (zero extra
  cost and verified to fire before any job in 9.10.1, but depends on internal call order, and a
  version that filled `updated_files` after execution would delete fresh outputs); a run id
  stamped into each pickle (skipped nodes keep the old stamp, so they would spin until
  finalization and could not be told apart from planned-but-unreached nodes after a cancel);
  moving `save_json` into the rule process (large refactor across the py3.9 conda rule envs, and
  outputPaths would still need refreshing).

---

## 5. The prototype (Appendix A)

### 5.1 What it is

**It is a prototype. Review and finish it; do not ship it blind.** It passed 79 tests locally
(the workflow, run-router and post-process suites, `test_expt_writer.py`, and the probes), but it
has known gaps (section 5.4), none of section 6 is done, and nothing was run end to end.

- Patch A: the full round-5 diff on top of `6aac20ec7` (3 files: `run.py`,
  `workflow_result.py` docstring, the save-once test file).
- Patch B: round 5 plus (d) + (e) + (W) on top of `6aac20ec7` (4 files:
  `studio/app/common/core/experiment/experiment_writer.py`,
  `studio/app/common/core/snakemake/snakemake_executor.py`,
  `studio/app/common/core/workflow/workflow_result.py`, `studio/app/common/routers/run.py`;
  143 insertions, 24 deletions). Patch B contains Patch A's two app-file changes (its `run.py`
  hunk names the lock `workflow_result` instead of an inline call, otherwise equivalent). It does
  **not** contain Patch A's test-file change; that is why Patch A is included.

Both were checked to apply to `6aac20ec7` with `git apply --check`, and Patch A's test hunk
applies on top of Patch B.

### 5.2 How to apply (two commits, so round 5 stays reviewable on its own)

Save the two fenced blocks of Appendix A to files outside the repo (for example `/tmp/A.patch`
and `/tmp/B.patch`), copying everything between the fences exactly (diff context lines that are a
single space must survive). Then:

```bash
git apply --check /tmp/A.patch && git apply /tmp/A.patch
# run the save-once tests, then commit round 5 on its own, e.g.
#   fix: refuse a same-uid rerun while a poll is saving

git checkout 6aac20ec7 -- studio/app/common/routers/run.py \
  studio/app/common/core/workflow/workflow_result.py
git apply --check /tmp/B.patch && git apply /tmp/B.patch
# working tree = round 5 + prototype; the test file keeps round 5's test
```

### 5.3 If the base head moved

If `origin/fix/run-result-blocking-save` is no longer `6aac20ec7`:

- If it already contains round 5 (`run_id` takes `observe_lock()` and
  `test_a_rerun_mid_save_is_refused_so_the_save_cannot_land_in_it` exists), skip Patch A.
- Do not use the `git checkout 6aac20ec7 -- ...` step, it would drop newer base changes. Apply
  Patch B with `git apply --3way`, or port it by hand, and keep the base's other changes.
- Name the head you built on in the PR body.

### 5.4 Review notes on the prototype (resolve or decide each one)

1. **Lint (checked).** flake8 and codespell are clean. isort wants the new
   `from studio.app.common.core.experiment.experiment_reader import ExptConfigReader` in
   `snakemake_executor.py` after the `studio.app.common.core.cloud.storage_tracking` import.
   black (23.11) splits `.execute_workflow(executor="dryrun", updated_files=planned)` over three
   lines in `snakemake_executor.py`, and joins `PostProcessResult.__init__`'s
   `super().__init__(...)` onto one line in `workflow_result.py`.
2. **Path form of planned outputs.** The Snakefile's outputs are absolute
   (`SmkUtils.output` joins `DIRPATH.OUTPUT_DIR`), and the probe deleted the files, but it is not
   verified whether snakemake hands back absolute or workdir-relative paths, nor what the process
   cwd is after the dry-run session. `invalidate_planned_outputs` calls `os.path.exists(path)`
   directly. Make it independent of cwd (resolve relative entries against the workdir) and pin it
   in a test.
3. **Marker lifetime when `run_id` fails.** The marker is written first inside the lock. If
   `WorkflowRunner(...)`, `ensure_input_data_local()` or `run_workflow()` raises, it stays behind
   with no child to clear it. Polls hold the observe lock while observing and `run_id` holds it
   throughout, so any position inside the locked block is equivalent for polls: consider writing
   it last (right after `run_workflow`) or clearing it on the failure path. Pin the choice in a
   test.
4. **The superseded-run early return also skips the remote ERROR status write.** The prototype
   returns from `_snakemake_execute_process` before the finalization `try`, which also skips the
   trailing `create_sync_status_file_for_error` for a failed run. That remote-status behaviour is
   a pre-existing issue the user asked to leave alone (section 7). Default: scope the guard to the
   finalization block only, so the ERROR status write behaves as on the base. If you keep the
   early return, say so explicitly in the PR body.
5. **`__check_has_whole_nwb`** writes `hasNWB` through `overwrite()` without `started_at`. A
   superseded observation could still flip `hasNWB` in a rerun's config. Decide: pass the
   generation through, or document why it is harmless.
6. **Lock-timeout window.** In the 600 s fallback, finalization runs without the lock. A rerun can
   then reset between finalization's generation check and its `clear_plan_pending()`, and the
   old finalization would remove the new run's marker. Narrow. Decide: re-check, or accept and
   document.
7. **Plan equals run.** `planned_outputs` must build the DAG with exactly the settings of the real
   run (snakefile, workdir, deployment methods, `forceall`). If the two can drift, one shared
   builder for the two callers is justified; otherwise leave them separate.
8. **Snakemake side effects of the dry run.** Confirm it leaves no workdir lock (the real run right
   after it must not hit a locked directory), writes nothing noisy to the app log, and does not
   build missing conda envs when `use_conda=True` (the 4 to 5.5 s first call was measured in a
   container where it is unknown whether the envs already existed).

---

## 6. Required remaining work

Each item needs its acceptance criteria met and its evidence in the PR body.

- [ ] **R1. Keep `.plan_pending` out of remote storage and every sync.**
  - Today a full upload takes everything except `.snakemake/` and two coordination files: the S3
    path (`upload_experiment` in `studio/app/common/core/storage/s3_storage_controller.py`)
    skips `remote_sync.lock` and the sync status file and filters `UPLOAD_EXPERIMENT_EXCLUDED_DIRS
    = {".snakemake"}`; the mock path (`mock_storage_controller.py`) uses
    `create_upload_experiment_ignore_function()`, which excludes only directories. The
    `post_process` rule (`studio/app/common/core/rules/post_process.py`) runs a full upload
    before it writes its pickle; the marker is normally gone by then, but not when planning failed.
  - Pick the smaller change and justify it in Design Decisions: either put the marker under
    `<workflow_dir>/.snakemake/` (already excluded by both paths; check that the directory exists
    at `run_id` time on a brand-new experiment and that snakemake never wipes a foreign file
    there), or add the name to the S3 coordination set and to the mock ignore function.
  - Acceptance: a test with mock remote storage (the test lane enables it through the root
    `conftest.py`: `REMOTE_STORAGE_TYPE=1`, `MOCK_STORAGE_DIR=/tmp/studio/mock_storage`) shows a
    full `upload_experiment` with the marker present puts no marker in the mock bucket. Also
    state, for the experiment copy path in `experiment_writer.py`
    (`shutil.copytree(output_dir, new_output_dir)`), `workflow_filter.py`'s copytree, workflow
    import in `routers/workflow.py`, and the S3 full download (`_clear_local_experiment_data`
    deletes every local file except the sync lock and status file), whether a stray marker can
    appear or vanish there, and what it would do (it only gates polls of that experiment until
    its next plan or finalization).
- [ ] **R2. Pin test: the dry run must fill `updated_files`.**
  - A snakemake upgrade where the dry run stops filling `updated_files` would silently bring M2
    back. Adapt `test_plan_probe.py` (Appendix B) into an asserting test, for example
    `studio/tests/app/common/core/snakemake/test_snakemake_plan.py`. Do not mark it
    `heavier_processing` (CI runs `-m "not heavier_processing"`). Use `use_conda=False`.
  - Acceptance: through the real Snakefile (`DIRPATH.SNAKEMAKE_FILEPATH`) on a copy of
    `studio/test_data/output_test/default/smk_exec_lccd`, with dummy pickles newer than the input
    the plan is empty; after touching `studio/test_data/input/1/test.tif` the plan is exactly the
    three pickles; after `invalidate_planned_outputs` they are gone and the marker is cleared.
    Mutation: make `planned_outputs` return an empty list (or drop `updated_files`) and the test
    fails.
  - Isolation: `test_snakemake_executor_lccd.py` copies the same fixture into
    `output/default/smk_exec_lccd` at import time. Do not delete that shared directory from your
    test. Copy the fixture to its own uid and rewrite the `default/smk_exec_lccd` prefix in the
    copied `snakemake.yaml` (it bakes the uid into every output path), or otherwise avoid
    touching the shared one. Restore the tif's mtime afterwards.
- [ ] **R3. Cancel-before-turn: decide and document.**
  - With (d), a STOP before snakemake reaches a planned node leaves that node with no pickle (it
    was deleted at plan time): it ends as error with `outputPaths: None`, and its previous JSON
    stays on disk unreferenced. develop-main kept the old pickle and showed the node as success
    with old data.
  - Recommended: accept (the node did not run in this generation), after checking that no
    consumer reads a node pickle without going through the yaml status (NWB export, dataview,
    reproduce, Edit ROI, downloads).
  - Acceptance: the decision and its one-line reason in Design Decisions, and a test pinning the
    chosen behaviour (matrix probe S5).
- [ ] **R4. Measure the dry-run cost on a large workflow.**
  - Build or reuse a workflow with many nodes (20 or more; duplicate nodes of an existing test
    workflow if needed). Measure `planned_outputs` cold (first SnakemakeApi call in a process)
    and warm, with `use_conda` True and False, and whether the real run's DAG build gets cheaper
    after the dry run absorbed the warm-up.
  - Acceptance: numbers and method in Evidence. If the plan takes longer than the 10 s poll
    interval, or several seconds warm, raise it under Difficulties.
- [ ] **R5. End-to-end run with real snakemake jobs.**
  - Scenarios: fresh run; rerun with no change; forced rerun (changed params); non-forced rerun
    after an input change (touch the input file or change an NWB setting); STOP mid-run then
    rerun; a node that errors.
  - For each: what the polls reported during the run (no premature success), and at the end the
    yaml status and outputPaths, and the JSON values against the pickle.
  - How: through the UI if you can run the app (`docker-compose.dev.yml`), otherwise a scripted
    integration that runs `snakemake_execute` with real jobs (the `smk_exec_lccd` fixture is
    already used with real jobs by `test_snakemake_executor_lccd.py`) in a background
    thread/process while calling `run_result` about once a second with the pending list the
    frontend would send; STOP through `WorkflowMonitor.cancel_run` or the cancel route; an error
    through params that make a node raise.
  - Acceptance: a results table in Evidence, saying which route you used. If real jobs cannot
    run in your environment (conda envs), say so explicitly under Difficulties; do not mark R5
    done.
- [ ] **R6. Remote-storage path review.**
  - Walk each path with the marker and the generation guard in mind: the `post_process` full
    upload; the observe-path upload of yaml plus JSON, done only by the observation that flips the
    last node to finished (finalization uploads nothing when a poll did that flip);
    `ensure_input_data_local()` now running inside the observe lock in `run_id` (polls answer
    `{}` meanwhile); `ExptConfigReader.ensure_synced_async` in `run_result`; the sync job
    downloads (`studio/app/common/core/background/sync_job.py`); a superseded run skipping its
    finalization upload.
  - Acceptance: a short per-path verdict under Others; tests where cheap with mock storage.
- [ ] **R7. Regression tests.** Each must fail on the base (`6aac20ec7` + round 5) and pass with
  the fix. Use the `finished_node` fixture and the `poll()` / `rerun()` helpers in
  `test_workflow_result_save_once.py`; Appendix B shows working setups for each.
  1. M2: unforced rerun; a poll before snakemake reaches a planned node does not report it; at
     the end the node's JSON value and yaml `data_shape` match the new pickle (S4).
  2. Gate: a poll between `run_id` and the plan answers `{}`; after the plan, skipped nodes
     answer success.
  3. Cancel mid-rerun (S5): the reached node is consistent; the cancelled planned node is error,
     not success.
  4. Downstream error (S6): the errored node ends error.
  5. 5b: a cancelled run's finalization after a rerun reset leaves the rerun's statuses and gate
     untouched.
  6. M1: finalization past the lock timeout, mid-save, then a rerun: the rerun's node stays
     `running` with `outputPaths: None` (patch `OBSERVE_LOCK_WAIT_SECONDS` small).
  7. W: pickle deleted, and pickle replaced by an error pickle, after the poll read it: no 500,
     node absent from that poll's results (parametrize).
  8. Round 5: `test_a_rerun_mid_save_is_refused_so_the_save_cannot_land_in_it` still passes
     (adapt it only if the marker legitimately changes what it asserts, and say why).
  9. The pin test (R2) and the upload-exclusion test (R1).
  - Test-data notes: the save-once fixture's `started_at` is `2022-05-15 23:02:15`, so a rerun in
    a test gets a new generation without sleeping; when a test resets twice within one second,
    sleep 1.1 s (second resolution). Probes print; your tests must assert.
- [ ] **R8. Mutation-check every new test.** Revert the specific fix it guards (in the working
  tree only, never committed), confirm the test fails for the expected reason, restore. List each
  mutation and its failure in the PR's test-coverage section.
- [ ] **R9. Lint and suites.** black, isort, flake8, codespell clean on every changed file; the
  focused suites and the full backend suite pass (section 8).
- [ ] **R10. Open the draft PR** (section 10), with every TODO in the body filled.

---

## 7. Known pre-existing issues: do NOT fix in this PR

Leave these alone. Mention them under Others as known and out of scope.

1. `run_id` never clears the previous run's `error.log`. The child removes it at start
   (`SmkStatusLogger`'s error-log init), so a poll in between marks every pending node error.
2. A cancelled run's child, after a rerun has started, still deletes the remote sync lock
   (`delete_sync_lock_file` without a token) and writes remote sync status ERROR
   (`create_sync_status_file_for_error`). See review note 5.4 item 4: do not change the ERROR
   write as a side effect, and do not extend the guard to the lock deletion.
3. Premature-success nuances that remain after (d): if planning fails, results appear only at
   finalization (delayed, not wrong); a superseded run's JSON writes are not guarded (the rerun's
   own save overwrites them, except when its node finishes before the stale save does); the
   same-generation two-saver overlap after the 600 s fallback (section 1.4, M1). Document, do not
   fix.
4. `test_expt_reader.py::test_read` fails in isolation on develop-main too. Unrelated.

---

## 8. How to test in this repo

- Backend tests run in the test container (`docker-compose.test.yml`, service
  `test_studio_backend`, built from `studio/config/docker/Dockerfile.test`, repo mounted at
  `/app`). Makefile targets:
  - `make test_backend_one T="<paths or -k expr>"`: one file or a few, same image and env as CI.
  - `make test_backend`: the CI lane, `pytest -s studio/tests/app/ -m "not heavier_processing"`.
  - `make test_backend_native`: the same tests against your local Python env, if you have no
    docker (`PYTEST_NATIVE` overrides the command).
- The root `conftest.py` sets the test environment (`OPTINIST_DIR=studio/test_data`,
  `IS_TEST`, mock remote storage, dummy Stripe values); no gitignored config is needed. If a test
  does need a gitignored file, report it rather than inventing credentials.
- Focused suites to run on every iteration:

  ```bash
  make test_backend_one T="studio/tests/app/common/core/workflow/test_workflow_result_save_once.py \
    studio/tests/app/common/core/workflow/test_workflow_result.py \
    studio/tests/app/common/core/workflow/test_workflow_result_missing_config.py \
    studio/tests/app/common/core/workflow/test_workflow_result_has_nwb_repair.py \
    studio/tests/app/common/core/snakemake/test_snakemake_executor_post_process.py \
    studio/tests/app/common/core/experiment/test_expt_writer.py \
    studio/tests/app/common/routers/test_background_task_sessions.py \
    studio/tests/app/common/routers/test_path_traversal_response.py \
    studio/tests/app/common/routers/test_run_authorization.py \
    studio/tests/app/common/routers/test_run_result_record_gate.py \
    studio/tests/app/common/routers/test_run_api_contract.py"
  ```

  plus your new test files. Reference counts from local runs: round 5 alone, 77 passed on a
  similar set; the prototype, 79 passed including the probes. Get your own numbers.
- **Conftest wipe.** `studio/tests/app/conftest.py`'s session fixture ends with
  `shutil.rmtree(<DATA_DIR>/output)`, and runs leave lock directories behind. After several
  iterative single-file runs in the same checkout, unrelated tests (seen before:
  `test_experiment.py`, `test_outputs.py`, `test_workflow.py`, `test_filepath_creater.py`,
  `test_workflow_reader.py`) can fail with `FileNotFoundError`. Before you call anything a
  regression, re-run in a clean checkout (`git worktree add` or `git clean` of
  `studio/test_data/output`) and compare against the base branch.
- **CI caveat.** `.github/workflows/tests.yml` runs only on PRs into `main`, `develop-main` and
  `future*`. A PR into `fix/run-result-blocking-save` gets the linters and codespell, but not the
  test jobs. Run `make test_backend` yourself and paste the summary line into the PR.
- **Lint** (CI: `.github/workflows/linters.yml`, `codespell.yml`; local: `.pre-commit-config.yaml`):
  - `black --check studio` (black `~= 23.11`)
  - `isort --check --diff studio` (isort 5, `profile = "black"` in `pyproject.toml`)
  - `flake8` (`.flake8`: max line 88, extend-ignore E203, W503)
  - `codespell -I .codespellignore <changed files>`
  - or `pre-commit run --files <changed files>`

---

## 9. House rules (non-negotiable)

- **Git.** Branch `fix/run-result-plan-invalidation`, created with `--no-track`. Push only that
  branch. Never merge anything (no `gh pr merge`, no `git merge` of a feature branch into a base,
  no merge via the UI). Never force-push. Never touch rulesets or branch protection. Do not push
  to `fix/run-result-blocking-save` or `develop-main`.
- **Commits.** One line, `<type>: <imperative summary>`, types `feature`, `fix`, `docs`,
  `refactor`, `test`; lowercase type, imperative mood, under 72 characters, no trailing period.
  No body, no trailers (no Co-Authored-By, no "Generated with" lines), no mention of Claude.
  No "WIP", "Update files", "Fix bug". Suggested sequence (keep round 5 as its own first commit;
  split the rest as far as the shared files allow, fewer commits are fine):
  - `fix: refuse a same-uid rerun while a poll is saving`
  - `fix: invalidate planned pickles before polls can read them`
  - `fix: drop observation writes from a superseded run`
  - `fix: skip a node whose pickle changed under the save worker`
  - `fix: keep the plan marker out of remote uploads`
  - `test: pin the snakemake dry-run plan through the real snakefile`
- **Characters.** No em dash (U+2014) and no arrow glyph (U+2192) anywhere: code, comments,
  docstrings, commit messages, PR title and body. Use `->`, `:`, `,` or `-`. Check with
  `grep -rnP '\x{2014}|\x{2192}'` over your diff and the PR body before posting.
- **Code comments.** Default to none. If one is needed, a single line that will still be true in
  a year without outside context. No issue numbers, PR links, branch names or "see X.md" pointers
  in source. No `ponytail:` or other tool prefixes. Trim the prototype's comments to this
  standard (most of them already are one line).
- **YAGNI.** Build only what this needs. No config knobs, no abstraction for a single caller, no
  new dependency. Stdlib and existing helpers first.
- **References.** In docstrings, commit messages and the PR body, prefer grep-able anchors
  (function, class, constant names) over line numbers. Do not reference files that are not
  committed.
- **PR.** Open it as a draft (`gh pr create --draft --base fix/run-result-blocking-save`). No
  "Generated with" footer. Do not comment on #938, do not reply to or resolve its threads, do not
  request reviewers. The body follows the template in section 10 exactly, section names and
  order included.

---

## 10. Proposed PR title and body

Title (verb first, 61 characters):

`Fix stale rerun results by invalidating planned pickles first`

Body skeleton (fill every TODO; keep the section names and order exactly):

```markdown
**Current Branch:** fix/run-result-plan-invalidation
**Target Branch:** fix/run-result-blocking-save

----------
### Content

#### Summary
- On a same-UID rerun, a /run/result poll could save the previous run's pickle for a node snakemake was about to re-run and record it as this run's success; with #938's save-once, that stale JSON and outputPaths became permanent (develop-main healed it at a clean finalization).
- `run_id` now marks the run "plan pending" inside the rerun lock; polls treat every pickle as not ready until the snakemake child has asked snakemake for its plan (dry run), deleted the old pickles of the jobs it will run, and cleared the mark. A pickle a poll can see is then always this run's result or a valid skipped result.
- A `started_at` generation guard drops any experiment.yaml write, and skips finalization, from a run that a rerun has superseded (fixes the lock-timeout fallback writing into a rerun, and a cancelled run's late finalization marking the rerun error).
- The save worker skips a node whose pickle vanished or became an error pickle between the poll's read and its own, instead of answering 500 (which the frontend turns into ABORTED).
- Also carries the round-5 rerun lock: `run_id` answers 423 while a poll is saving, so a save can never land in the reset config.

#### Design Decisions
- Plan-time invalidation (d) over the alternatives: re-saving every node at finalization (a) heals only runs that finish cleanly, leaves premature success and every STOP/OOM case broken, adds a save per node under the lock and needs a forced upload; a pickle-identity check (b) has the same coverage as (a) because the frontend never re-polls a success node, and needs a sidecar or a schema change; accepting it (c) is a regression in routine reruns (deploys, input or NWB changes, restores, copies).
- The plan comes from snakemake's public API (`execute_workflow(executor="dryrun", updated_files=...)`) in the background child, not from `run_id` (no subprocess or DAG build under the request lock) and not from hooking the real run's `updated_files` list (depends on internal call order). A pin test runs it through the real Snakefile so a snakemake upgrade that stops filling `updated_files` fails CI.
- The gate is a file marker written under the observe lock, so it holds across uvicorn workers with the same locality as the lock itself.
- `started_at` is the generation: it is already in experiment.yaml, the check runs inside the same yaml file lock as the rerun's reset, and second resolution suffices because a rerun starts after the previous run started. (d) needs it: otherwise a cancelled run's late finalization clears the new run's gate.
- The 600 s finalization fallback stays: two savers of the same generation re-read the same pickle and the stuck poll rewrites last; the rerun case it allowed is now dropped by the generation guard.
- Round 5 refuses a mid-save rerun with 423 rather than waiting, like the non-blocking poll.
- TODO: marker placement for remote storage (R1) and why.
- TODO: cancel before a planned node's turn (R3): decision and reason.
- TODO: decisions on review notes 5.4 items 3 to 7 (marker on `run_id` failure, superseded-run early return scope, hasNWB write, lock-timeout window, shared DAG builder or not).

### Evidence
- TODO: end-to-end results table (R5), route used.
- TODO: dry-run cost, cold and warm, use_conda on/off, workflow size (R4).
- TODO: before/after for M2 (JSON value and yaml data_shape after a non-forced rerun), W (500 -> skipped), M1, 5b.
- TODO: `make test_backend` summary line.

### References
- Stacked on #938 (`fix/run-result-blocking-save`).
- Copilot thread on #938, finalization saving without the lock after the 600 s wait: https://github.com/arayabrain/araya-optinist/pull/938#discussion_r4278546530
- Copilot thread on #938, same-UID rerun during a save: https://github.com/arayabrain/araya-optinist/pull/938#discussion_r4278546559
- Root cause introduced by 10bf3218c (save once, off the event loop: stop re-saving every poll); W introduced by b59f4ee05 (worker-process save re-reads the pickle). This PR keeps both intents and makes the pickle a poll can see trustworthy.

#### Files changed
- `studio/app/common/routers/run.py` -- `run_id` holds the observe lock (423 when held) and marks the plan pending while it resets the run.
- `studio/app/common/core/workflow/workflow_result.py` -- plan marker and poll gate, generation passed to every yaml write, `invalidate_planned_outputs`, worker skip on a vanished or error pickle.
- `studio/app/common/core/snakemake/snakemake_executor.py` -- `planned_outputs` dry run, invalidation before the real run, finalization skipped when superseded.
- `studio/app/common/core/experiment/experiment_writer.py` -- `ExptConfigWriter.overwrite(started_at=...)` drops a superseded write inside the yaml lock.
- TODO: storage file(s) if R1 needs them.
- `studio/tests/app/common/core/workflow/test_workflow_result_save_once.py` -- TODO: list.
- TODO: new test files.

### Manual Testcases
- TODO: per R5 scenario: who (developer), action, expected result.
- `pytest studio/tests/app/ -m "not heavier_processing"` -- all N tests pass (TODO: N)

### Unit, Integration, Contract Test Coverage
- TODO: each new test, what it pins, and the mutation that makes it fail (R8).

### Others
- TODO: remote-storage path review (R6).
- Known and out of scope: `run_id` does not clear the previous `error.log`; a cancelled run's child still deletes the sync lock and writes remote ERROR status after a rerun started; a superseded run's JSON writes are not guarded.

#### Difficulties (if any)
- TODO

#### Risk Assessment
| Area | Risk | Notes |
|------|------|-------|
| Run/rerun result pipeline | Medium | Every run now waits for the plan before polls report nodes; a planning failure only delays results to finalization. TODO: confirm with R4/R5 numbers. |
| Snakemake upgrade | Medium | The design relies on the dry run filling `updated_files`; the pin test fails if a version stops doing so. |
| Remote storage | TODO | Marker exclusion (R1) and the per-path review (R6). |
| Finalization | Low | Skipped only when `started_at` changed, i.e. a rerun superseded the run. |
| Same-UID rerun during a save | Low | Answers 423 and shows the generic run-failed snackbar; Run works once the save ends. |
```

---

## Appendix A: patches

Both patches are relative to `6aac20ec7`. Section 5.2 says how to apply them.

### Patch A: round 5 (the full uncommitted round-5 diff)

```diff
diff --git a/studio/app/common/core/workflow/workflow_result.py b/studio/app/common/core/workflow/workflow_result.py
index 5cd400e7d..8a4338df7 100644
--- a/studio/app/common/core/workflow/workflow_result.py
+++ b/studio/app/common/core/workflow/workflow_result.py
@@ -140,7 +140,7 @@ class WorkflowResult:
         return node_results
 
     def observe_lock(self, timeout: float = 0) -> FileLock:
-        """Held while observing, so two observers never save the same outputs"""
+        """Held while observing or resetting for a rerun, so neither overlaps a save"""
         return FileLock(FileLockUtils.get_lockfile_path(self.workflow_dirpath), timeout)
 
     async def observe_overall(self) -> Dict[str, Message]:
diff --git a/studio/app/common/routers/run.py b/studio/app/common/routers/run.py
index 67c762a71..d14cea607 100644
--- a/studio/app/common/routers/run.py
+++ b/studio/app/common/routers/run.py
@@ -202,15 +202,27 @@ async def run_id(
         # on; refuse before the runner rewrites the experiment's config files
         RemoteSyncLockFileUtil.check_sync_lock_file(workspace_id, uid, raise_error=True)
 
-        runner = WorkflowRunner(
-            remote_bucket_name, workspace_id, uid, runItem, current_user.id
-        )
+        # An observer mid-save would record its result into the reset config
+        observe_lock = WorkflowResult(workspace_id, uid).observe_lock()
+        try:
+            observe_lock.acquire()
+        except Timeout:
+            raise HTTPException(
+                status_code=status.HTTP_423_LOCKED,
+                detail="The previous run's results are still being saved.",
+            )
+        try:
+            runner = WorkflowRunner(
+                remote_bucket_name, workspace_id, uid, runItem, current_user.id
+            )
 
-        # Download any remote-only input files before workflow runs
-        if RemoteStorageController.is_available():
-            await runner.ensure_input_data_local()
+            # Download any remote-only input files before workflow runs
+            if RemoteStorageController.is_available():
+                await runner.ensure_input_data_local()
 
-        runner.run_workflow(background_tasks)
+            runner.run_workflow(background_tasks)
+        finally:
+            observe_lock.release()
 
         # Refresh storage cache in background
         background_tasks.add_task(
diff --git a/studio/tests/app/common/core/workflow/test_workflow_result_save_once.py b/studio/tests/app/common/core/workflow/test_workflow_result_save_once.py
index 45a4aa753..ad90cca68 100644
--- a/studio/tests/app/common/core/workflow/test_workflow_result_save_once.py
+++ b/studio/tests/app/common/core/workflow/test_workflow_result_save_once.py
@@ -16,13 +16,20 @@ from unittest.mock import AsyncMock, Mock, patch
 
 import numpy as np
 import pytest
+from fastapi import HTTPException
 from filelock import Timeout
 
+from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
 from studio.app.common.core.rules.runner import Runner
-from studio.app.common.core.workflow.workflow import NodeItem
+from studio.app.common.core.snakemake.smk import ForceRun
+from studio.app.common.core.storage.remote_storage_controller import (
+    RemoteStorageController,
+)
+from studio.app.common.core.workflow.workflow import Node, NodeData, NodeItem, RunItem
 from studio.app.common.core.workflow.workflow_result import WorkflowResult
+from studio.app.common.core.workflow.workflow_runner import WorkflowRunner
 from studio.app.common.dataclass.timeseries import TimeSeriesData
-from studio.app.common.routers.run import run_result
+from studio.app.common.routers.run import run_id, run_result
 from studio.app.dir_path import DIRPATH
 from studio.app.optinist.dataclass import BehaviorData
 
@@ -56,6 +63,30 @@ def poll():
     )
 
 
+def rerun():
+    nodes = {
+        node_id: Node(
+            id=node_id,
+            type="AlgorithmNode",
+            data=NodeData(label=node_id, param={}, path="", type="algorithm"),
+            position={"x": 0, "y": 0},
+            style={},
+        )
+        for node_id in NODES
+    }
+    return run_id(
+        workspace_id=WORKSPACE_ID,
+        uid=UNIQUE_ID,
+        runItem=RunItem(
+            nodeDict=nodes, forceRunList=[ForceRun(nodeId="func1", name="func1")]
+        ),
+        background_tasks=Mock(),
+        remote_bucket_name="",
+        current_user=Mock(id=None),
+        db=Mock(),
+    )
+
+
 @pytest.mark.asyncio
 async def test_repeated_observes_save_a_finished_node_once(finished_node):
     saved = finished_node / "func1" / "behavior"
@@ -162,6 +193,59 @@ async def test_a_cancelled_poll_frees_the_loop_and_holds_its_lock(finished_node)
         assert (finished_node / "func1" / "behavior").is_dir()
 
 
+@pytest.mark.asyncio
+async def test_a_rerun_mid_save_is_refused_so_the_save_cannot_land_in_it(
+    finished_node,
+):
+    started, finish = threading.Event(), threading.Event()
+
+    def slow_save(self, json_dir):
+        started.set()
+        finish.wait(3)
+        save_json(self, json_dir)
+
+    with patch.object(
+        TimeSeriesData, "save_json", autospec=True, side_effect=slow_save
+    ), patch(
+        "studio.app.common.core.workflow.workflow_result.ProcessPoolExecutor",
+        ThreadPoolExecutor,
+    ), patch(
+        "studio.app.common.routers.run.ExptConfigReader.ensure_synced_async",
+        new=AsyncMock(),
+    ), patch(
+        "studio.app.common.routers.run.ExperimentRecordService.is_available",
+        return_value=False,
+    ), patch(
+        "studio.app.common.routers.run._check_storage_quota", new=AsyncMock()
+    ), patch(
+        "studio.app.common.routers.run.ensure_structure_caches", new=AsyncMock()
+    ), patch(
+        "studio.app.common.routers.run.validate_input_edges"
+    ), patch(
+        "studio.app.common.routers.run.get_current_user_storage_usage",
+        new=AsyncMock(),
+    ), patch.object(
+        RemoteStorageController, "is_available", return_value=False
+    ), patch.object(
+        WorkflowRunner, "set_smk_config"
+    ):
+        task = asyncio.create_task(poll())
+        try:
+            assert await asyncio.to_thread(started.wait, 10)
+            with pytest.raises(HTTPException) as refused:
+                await rerun()
+        finally:
+            finish.set()
+            await task
+        assert refused.value.status_code == 423
+
+        assert await rerun() == UNIQUE_ID
+
+    func1 = ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).function["func1"]
+    assert (func1.success, func1.outputPaths) == ("running", None)
+    assert not (finished_node / "func1" / "func1.pkl").exists()
+
+
 @pytest.mark.asyncio
 async def test_finalization_waits_for_a_poll_mid_save(finished_node):
     held = threading.Event()
```

### Patch B: prototype, round 5 + (d) + (e) + (W)

```diff
diff --git a/studio/app/common/core/experiment/experiment_writer.py b/studio/app/common/core/experiment/experiment_writer.py
index d99fe1886..0fa7b0e83 100644
--- a/studio/app/common/core/experiment/experiment_writer.py
+++ b/studio/app/common/core/experiment/experiment_writer.py
@@ -113,7 +113,7 @@ class ExptConfigWriter:
             auto_file_lock=auto_file_lock,
         )
 
-    def overwrite(self, update_params: dict) -> None:
+    def overwrite(self, update_params: dict, started_at: str = None) -> None:
         expt_filepath = ExptConfigReader.get_config_yaml_path(
             self.workspace_id, self.unique_id
         )
@@ -123,6 +123,8 @@ class ExptConfigWriter:
         with FileLock(lock_path, ConfigWriter.FILE_LOCK_TIMEOUT):
             # Read experiment config
             config = ExptConfigReader.read(self.workspace_id, self.unique_id)
+            if started_at is not None and config.started_at != started_at:
+                return  # a rerun reset the config after this write was decided
 
             # Merge overwrite params
             config_merged = differential_deep_merge(asdict(config), update_params)
diff --git a/studio/app/common/core/snakemake/snakemake_executor.py b/studio/app/common/core/snakemake/snakemake_executor.py
index e57e89daa..a5b28e30a 100644
--- a/studio/app/common/core/snakemake/snakemake_executor.py
+++ b/studio/app/common/core/snakemake/snakemake_executor.py
@@ -14,7 +14,9 @@ from snakemake.api import (
     SnakemakeApi,
     StorageSettings,
 )
+from snakemake.settings.enums import Quietness
 
+from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
 from studio.app.common.core.cloud.storage_tracking import (
     update_user_storage_after_workflow,
 )
@@ -36,7 +38,10 @@ from studio.app.common.core.storage.remote_storage_controller import (
     RemoteSyncStatusFileUtil,
 )
 from studio.app.common.core.utils.filepath_creater import get_pickle_file, join_filepath
-from studio.app.common.core.workflow.workflow_result import WorkflowResult
+from studio.app.common.core.workflow.workflow_result import (
+    WorkflowResult,
+    invalidate_planned_outputs,
+)
 from studio.app.common.core.workspace.workspace_data_capacity_services import (
     WorkspaceDataCapacityService,
 )
@@ -104,6 +109,7 @@ def _snakemake_execute_process(
     # Snakemake execution process
     # ------------------------------------------------------------
 
+    generation = _started_at(workspace_id, unique_id)
     smk_logger = SmkStatusLogger(workspace_id, unique_id)
     smk_workdir = join_filepath(
         [
@@ -120,6 +126,16 @@ def _snakemake_execute_process(
     if getattr(params, "use_conda", True):
         deployment_methods.append(DeploymentMethod.CONDA)
 
+    # Snakemake deletes a job's old output only when the job starts; until
+    # then a poll would take that old pickle for this run's result
+    try:
+        invalidate_planned_outputs(
+            workspace_id, unique_id, planned_outputs(smk_workdir, params)
+        )
+    except Exception as e:
+        # The gate stays up, so polls wait for finalization instead
+        logger.warning(f"Could not plan the run, results wait for its end: {e}")
+
     # Use context manager for proper cleanup
     with SnakemakeApi(
         OutputSettings(
@@ -190,6 +206,10 @@ def _snakemake_execute_process(
     if snakemake_result and RemoteStorageController.is_available():
         RemoteSyncLockFileUtil.wait_for_lock_release(workspace_id, unique_id)
 
+    if generation and _started_at(workspace_id, unique_id) != generation:
+        logger.warning(f"Superseded by a rerun, skipping finalization: {unique_id}")
+        return snakemake_result
+
     try:
         # Finalize workflow results. Reaching the upload lock proves the local
         # ExptConfig is finalized, so a lock conflict (idempotent upload handled
@@ -199,7 +219,7 @@ def _snakemake_execute_process(
             observe_success,
             observe_lock_conflict,
             upload_confirmed,
-        ) = _observe_overall_with_lock_retry(workspace_id, unique_id)
+        ) = _observe_overall_with_lock_retry(workspace_id, unique_id, generation)
         should_finalize = observe_success or observe_lock_conflict
 
         # Update experiment database record
@@ -265,7 +285,39 @@ def _snakemake_execute_process(
     return snakemake_result
 
 
-def _observe_overall_with_lock_retry(workspace_id: str, unique_id: str) -> tuple:
+def _started_at(workspace_id: str, unique_id: str):
+    try:
+        return ExptConfigReader.read(workspace_id, unique_id).started_at
+    except Exception:
+        return None
+
+
+def planned_outputs(smk_workdir: str, params: SmkParam) -> List[str]:
+    """The outputs of every job a run with these params would execute"""
+    deployment_methods = (
+        [DeploymentMethod.CONDA] if getattr(params, "use_conda", True) else []
+    )
+    planned = []
+    with SnakemakeApi(OutputSettings(quiet={Quietness.ALL})) as snakemake_api:
+        snakemake_api.workflow(
+            snakefile=Path(DIRPATH.SNAKEMAKE_FILEPATH),
+            workdir=Path(smk_workdir),
+            storage_settings=StorageSettings(),
+            resource_settings=ResourceSettings(cores=getattr(params, "cores", 1)),
+            deployment_settings=DeploymentSettings(
+                deployment_method=deployment_methods,
+                conda_frontend="conda",
+                conda_prefix=DIRPATH.SNAKEMAKE_CONDA_ENV_DIR,
+            ),
+        ).dag(
+            dag_settings=DAGSettings(forceall=getattr(params, "forceall", False))
+        ).execute_workflow(executor="dryrun", updated_files=planned)
+    return [str(f) for f in planned]
+
+
+def _observe_overall_with_lock_retry(
+    workspace_id: str, unique_id: str, generation: str = None
+) -> tuple:
     """Run observe_overall() at finalization, tolerating upload-lock conflicts.
 
     observe_overall() finalizes the local ExptConfig (node statuses) first, then
@@ -297,7 +349,9 @@ def _observe_overall_with_lock_retry(workspace_id: str, unique_id: str) -> tuple
 
     for attempt in range(1, retry_max + 1):
         try:
-            asyncio.run(WorkflowResult(workspace_id, unique_id).observe_overall())
+            asyncio.run(
+                WorkflowResult(workspace_id, unique_id).observe_overall(generation)
+            )
             observe_success = True
             upload_confirmed = True
             break
diff --git a/studio/app/common/core/workflow/workflow_result.py b/studio/app/common/core/workflow/workflow_result.py
index 5cd400e7d..0ac873608 100644
--- a/studio/app/common/core/workflow/workflow_result.py
+++ b/studio/app/common/core/workflow/workflow_result.py
@@ -76,8 +76,19 @@ class WorkflowResult:
             ]
         )
         self.monitor = WorkflowMonitor(workspace_id, unique_id)
+        self.plan_pending_path = join_filepath([self.workflow_dirpath, ".plan_pending"])
 
-    async def observe(self, observe_node_ids: List[str]) -> Dict[str, Message]:
+    def mark_plan_pending(self) -> None:
+        """Until snakemake has planned a rerun, an old pickle may be about to rerun"""
+        open(self.plan_pending_path, "w").close()
+
+    def clear_plan_pending(self) -> None:
+        if os.path.exists(self.plan_pending_path):
+            os.remove(self.plan_pending_path)
+
+    async def observe(
+        self, observe_node_ids: List[str], generation: str = None
+    ) -> Dict[str, Message]:
         """
         Perform the following operations for the specified workflow
           - Check and update the workflow execution status
@@ -105,14 +116,19 @@ class WorkflowResult:
             )
             return {}
 
+        # Writes are dropped once a rerun has reset the config
+        generation = generation or getattr(expt_config, "started_at", None)
+
         # check for workflow errors
         workflow_error = SmkStatusLogger.get_error_content(
             self.workspace_id, self.unique_id
         )
 
+        plan_pending = os.path.exists(self.plan_pending_path)
+
         # observe node list
         node_results = await self.__observe_nodes(
-            observe_node_ids, expt_config, workflow_error
+            observe_node_ids, expt_config, workflow_error, plan_pending, generation
         )
 
         # check workflow status
@@ -134,16 +150,16 @@ class WorkflowResult:
 
                 # re-run observe node list (reflects workflow error)
                 node_results = await self.__observe_nodes(
-                    observe_node_ids, expt_config, workflow_error
+                    observe_node_ids, expt_config, workflow_error, False, generation
                 )
 
         return node_results
 
     def observe_lock(self, timeout: float = 0) -> FileLock:
-        """Held while observing, so two observers never save the same outputs"""
+        """Held while observing or resetting for a rerun, so neither overlaps a save"""
         return FileLock(FileLockUtils.get_lockfile_path(self.workflow_dirpath), timeout)
 
-    async def observe_overall(self) -> Dict[str, Message]:
+    async def observe_overall(self, generation: str = None) -> Dict[str, Message]:
         """
         Automatically observe all nodes
         """
@@ -158,11 +174,17 @@ class WorkflowResult:
             )
         try:
             expt_config = ExptConfigReader.read(self.workspace_id, self.unique_id)
+            if generation and expt_config.started_at != generation:
+                logger.warning(f"Superseded by a rerun: [{self.unique_id}]")
+                return {}
+
+            # Snakemake has exited, so every pickle left is this run's
+            self.clear_plan_pending()
 
             # Observe all nodes
             observe_node_ids = expt_config.function.keys() | expt_config.procs.keys()
 
-            return await self.observe(observe_node_ids)
+            return await self.observe(observe_node_ids, generation)
         finally:
             lock.release()
 
@@ -171,6 +193,8 @@ class WorkflowResult:
         observe_node_ids: List[str],
         expt_config: ExptConfig,
         workflow_error: WorkflowErrorInfo,
+        plan_pending: bool = False,
+        generation: str = None,
     ) -> Dict[str, Message]:
         """
         Observe by Nodes
@@ -195,6 +219,7 @@ class WorkflowResult:
                     self.unique_id,
                     node_id,
                     workflow_error=workflow_error,
+                    plan_pending=plan_pending,
                 )
             else:
                 node_result = NodeResult(
@@ -202,10 +227,13 @@ class WorkflowResult:
                     self.unique_id,
                     node_id,
                     workflow_error=workflow_error,
+                    plan_pending=plan_pending,
                 )
 
             if node_result.is_ready():
-                node_results[node_id] = await node_result.observe(expt_function)
+                message = await node_result.observe(expt_function, generation)
+                if message is not None:
+                    node_results[node_id] = message
 
         # If all nodes have already been processed, return here.
         if NodeResult.is_all_nodes_already_finished(expt_config):
@@ -330,6 +358,7 @@ class NodeResult(BaseNodeResult):
         unique_id: str,
         node_id: str,
         workflow_error: WorkflowErrorInfo = None,
+        plan_pending: bool = False,
     ):
         self.workspace_id = workspace_id
         self.unique_id = unique_id
@@ -348,7 +377,7 @@ class NodeResult(BaseNodeResult):
         # Read node's output pickle file
         self.algo_name = None
         self.info = None
-        if not self.workflow_has_error:
+        if not self.workflow_has_error and not plan_pending:
             node_pickle_path = PickleReader.search_node_pickle_path(self.node_dirpath)
             if node_pickle_path:
                 node_pickle_path = node_pickle_path.replace("\\", "/")
@@ -359,7 +388,7 @@ class NodeResult(BaseNodeResult):
                     self.info = None  # indicates error
                     logger.error(e, exc_info=True)
 
-    async def observe(self, expt_function: ExptFunction) -> Message:
+    async def observe(self, expt_function: ExptFunction, generation=None) -> Message:
         # Generate node process status (workflow.Message)
         # case) error throughout workflow
         if self.workflow_has_error:
@@ -395,6 +424,8 @@ class NodeResult(BaseNodeResult):
                         ),
                     )
                 )
+            if message is None:  # snakemake replaced the pickle since it was read
+                return None
 
         # Determine if the node has already been processed
         # *If it has, skip subsequent ExptConfig update process.
@@ -484,7 +515,7 @@ class NodeResult(BaseNodeResult):
 
         # Overwrite experiment config
         ExptConfigWriter(self.workspace_id, self.unique_id).overwrite(
-            update_expt_config_dict
+            update_expt_config_dict, started_at=generation
         )
 
         logger.debug(
@@ -567,8 +598,11 @@ class PostProcessResult(NodeResult):
         unique_id: str,
         node_id: str,
         workflow_error: WorkflowErrorInfo = None,
+        plan_pending: bool = False,
     ):
-        super().__init__(workspace_id, unique_id, node_id, workflow_error)
+        super().__init__(
+            workspace_id, unique_id, node_id, workflow_error, plan_pending
+        )
 
 
 async def _await_despite_cancel(future: asyncio.Future) -> Message:
@@ -584,12 +618,27 @@ async def _await_despite_cancel(future: asyncio.Future) -> Message:
     return future.result()
 
 
+def invalidate_planned_outputs(
+    workspace_id: str, unique_id: str, outputs: List[str]
+) -> None:
+    """Delete the old pickle of every job snakemake will run, then let polls in"""
+    result = WorkflowResult(workspace_id, unique_id)
+    with result.observe_lock(timeout=WorkflowResult.OBSERVE_LOCK_WAIT_SECONDS):
+        for path in outputs:
+            if path.endswith(".pkl") and os.path.exists(path):
+                os.remove(path)
+        result.clear_plan_pending()
+
+
 @with_client_id_context
 def _save_node_outputs(
     result_cls, workspace_id: str, unique_id: str, node_id: str, client_id=None
 ) -> Message:
     # Re-reads the node pickle rather than receive it through the process pipe
-    return result_cls(workspace_id, unique_id, node_id).success()
+    node_result = result_cls(workspace_id, unique_id, node_id)
+    if not PickleReader.check_is_valid_node_pickle(node_result.info):
+        return None
+    return node_result.success()
 
 
 class WorkflowMonitor:
diff --git a/studio/app/common/routers/run.py b/studio/app/common/routers/run.py
index 67c762a71..1ca65e30f 100644
--- a/studio/app/common/routers/run.py
+++ b/studio/app/common/routers/run.py
@@ -202,15 +202,29 @@ async def run_id(
         # on; refuse before the runner rewrites the experiment's config files
         RemoteSyncLockFileUtil.check_sync_lock_file(workspace_id, uid, raise_error=True)
 
-        runner = WorkflowRunner(
-            remote_bucket_name, workspace_id, uid, runItem, current_user.id
-        )
+        # An observer mid-save would record its result into the reset config
+        workflow_result = WorkflowResult(workspace_id, uid)
+        observe_lock = workflow_result.observe_lock()
+        try:
+            observe_lock.acquire()
+        except Timeout:
+            raise HTTPException(
+                status_code=status.HTTP_423_LOCKED,
+                detail="The previous run's results are still being saved.",
+            )
+        try:
+            workflow_result.mark_plan_pending()
+            runner = WorkflowRunner(
+                remote_bucket_name, workspace_id, uid, runItem, current_user.id
+            )
 
-        # Download any remote-only input files before workflow runs
-        if RemoteStorageController.is_available():
-            await runner.ensure_input_data_local()
+            # Download any remote-only input files before workflow runs
+            if RemoteStorageController.is_available():
+                await runner.ensure_input_data_local()
 
-        runner.run_workflow(background_tasks)
+            runner.run_workflow(background_tasks)
+        finally:
+            observe_lock.release()
 
         # Refresh storage cache in background
         background_tasks.add_task(
```

---

## Appendix B: local probes

These are the print-based probes behind sections 3 and 4. They were run inside the test
container from a directory outside the repo's test tree (for example with
`pytest -q -s -p no:cacheprovider -p conftest --rootdir=/app -c /app/pyproject.toml <probe>`).
They import helpers from `test_workflow_result_save_once.py`, including `rerun`, which exists
only after Patch A. They print instead of asserting; turn them into real tests (R2, R7). The
matrix probe runs unchanged on develop-main, on round 5 and on the prototype (it feature-detects
`mark_plan_pending`, `invalidate_planned_outputs` and the `generation` parameter);
`M2_VARIANT` only labels its output.

### B.1 `test_plan_probe.py` (basis for the pin test, R2)

```python
"""Proto (d) against the real optinist Snakefile: the dry-run plan names the
pickles a rerun will replace, so they can be deleted before any poll sees them."""

import os
import pickle
import shutil
import time
from pathlib import Path

from studio.app.common.core.snakemake.smk import SmkParam
from studio.app.common.core.snakemake.snakemake_executor import planned_outputs
from studio.app.common.core.workflow.workflow_result import (
    WorkflowResult,
    invalidate_planned_outputs,
)
from studio.app.dir_path import DIRPATH

WS, UID = "default", "smk_exec_lccd"
OUTS = [
    "input_0/data_endoscope.pkl",
    "lccd_cell_detection_1234/lccd_cell_detection.pkl",
    "pca_1234/pca.pkl",
]


def test_dry_run_plan_names_the_pickles_to_replace():
    wdir = Path(DIRPATH.OUTPUT_DIR) / WS / UID
    shutil.copytree(
        Path(DIRPATH.DATA_DIR) / "output_test" / WS / UID, wdir, dirs_exist_ok=True
    )
    tif = Path(DIRPATH.INPUT_DIR) / "1" / "test.tif"
    base = time.time() - 100
    os.utime(tif, (base, base))
    for i, rel in enumerate(OUTS):
        p = wdir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            pickle.dump({"old": True}, f)
        os.utime(p, (base + 10 + i, base + 10 + i))

    for use_conda in (True, False):
        params = SmkParam(use_conda=use_conda, cores=1, forceall=False)
        t = time.monotonic()
        plan = planned_outputs(str(wdir), params)
        print(f"PLAN use_conda={use_conda} untouched: {sorted(os.path.relpath(p, wdir) for p in plan)} in {time.monotonic()-t:.2f}s")

    os.utime(tif, None)  # the input changed; nothing is forced
    t = time.monotonic()
    plan = planned_outputs(str(wdir), SmkParam(use_conda=True, cores=1, forceall=False))
    print(f"PLAN after input touched: {sorted(os.path.relpath(p, wdir) for p in plan)} in {time.monotonic()-t:.2f}s")

    WorkflowResult(WS, UID).mark_plan_pending()
    invalidate_planned_outputs(WS, UID, plan)
    print("AFTER invalidate: exists=", {rel: (wdir / rel).exists() for rel in OUTS},
          "gate=", os.path.exists(WorkflowResult(WS, UID).plan_pending_path))
    shutil.rmtree(wdir)
```

### B.2 `test_m2_matrix.py` (scenarios S2, S4, S5, S5b, S6, S7)

```python
"""M2 scenario matrix probe: runs unchanged on develop-main and on PR 938 trees.

Snakemake is simulated by writing and deleting node pickles in the order the
real scheduler does (Job.prepare removes a job's old output right before the
job runs). The frontend is simulated by polling only nodes still pending.
"""

import inspect
import json
import os
import pickle
import shutil
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import pytest_asyncio

from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
)
from studio.app.common.core.workflow import workflow_result as wr
from studio.app.common.core.workflow.workflow import Edge, Node, NodeData, RunItem
from studio.app.common.core.workflow.workflow_result import (
    WorkflowMonitor,
    WorkflowResult,
)
from studio.app.common.core.workflow.workflow_runner import WorkflowRunner
from studio.app.dir_path import DIRPATH
from studio.app.optinist.dataclass import BehaviorData

WS, UID = "777778", "m2probe"
NODES = ["func1", "func2"]
PP = "post_process_0"
VARIANT = os.environ.get("M2_VARIANT", "?")


def wdir() -> Path:
    return Path(DIRPATH.OUTPUT_DIR) / WS / UID


def pkl(node: str) -> Path:
    return wdir() / node / f"{'post_process' if node == PP else node}.pkl"


def write(node: str, value=None, rows=3, error=False):
    p = pkl(node)
    p.parent.mkdir(parents=True, exist_ok=True)
    if error:
        data = ["Traceback: simulated node error"]
    elif node == PP:
        data = {"success": True}
    else:
        data = {"behaviors_data": BehaviorData(np.full((rows, 10), float(value)))}
    with open(p, "wb") as f:
        pickle.dump(data, f)


def run_item():
    nodes = {
        n: Node(
            id=n,
            type="AlgorithmNode",
            data=NodeData(label=n, param={}, path="", type="algorithm"),
            position={"x": 0, "y": 0},
            style={},
        )
        for n in NODES
    }
    edges = {
        "e1": Edge(
            id="e1",
            source="func1",
            sourceHandle="func1--out",
            target="func2",
            targetHandle="func2--in",
            type="buttonedge",
            animated=False,
            style={},
        )
    }
    return RunItem(nodeDict=nodes, edgeDict=edges, forceRunList=[])


def reset(rerun: bool):
    """What run_id/run do before snakemake starts (forceRunList empty)"""
    wresult = WorkflowResult(WS, UID)
    if rerun and hasattr(wresult, "mark_plan_pending"):
        wresult.mark_plan_pending()
    WorkflowRunner("", WS, UID, run_item(), None)
    if pkl(PP).exists():
        pkl(PP).unlink()


def apply_plan(planned):
    """What the snakemake child does once it knows its plan (prototype d only)"""
    hook = getattr(wr, "invalidate_planned_outputs", None)
    if hook:
        hook(WS, UID, [str(pkl(n)) for n in planned])


class Frontend:
    def __init__(self):
        self.pending = NODES + [PP]
        self.shown = {}

    async def poll(self, no_process=False):
        ctx = (
            patch.object(WorkflowMonitor, "search_process", return_value=None)
            if no_process
            else patch.object(WorkflowMonitor, "search_process", return_value=object())
        )
        with ctx:
            results = await WorkflowResult(WS, UID).observe(self.pending)
        for node, msg in results.items():
            ok = msg.status == "success" and msg.outputPaths is not None
            self.shown[node] = "success" if ok else "error"
        self.pending = [n for n in self.pending if n not in results]
        return {n: results[n].status for n in results}


def json_value(node: str):
    files = sorted((wdir() / node / "behavior").glob("chunk_0.json"))
    if not files:
        return None
    rec = json.loads(files[0].read_text())["records"]
    first = next(iter(rec.values()))
    return next(iter(first.values()))[0]


def pickle_value(node: str):
    if not pkl(node).exists():
        return None
    with open(pkl(node), "rb") as f:
        data = pickle.load(f)
    if not isinstance(data, dict):
        return "error-pickle"
    return float(data["behaviors_data"].data.flat[0])


def yaml_state(node: str):
    f = ExptConfigReader.read(WS, UID).function[node]
    shape = None
    if f.outputPaths:
        op = f.outputPaths["behaviors_data"]
        shape = op["data_shape"] if isinstance(op, dict) else op.data_shape
    return f.success, shape


def report(scenario: str, fe: Frontend, extra=""):
    parts = [f"RESULT {VARIANT:5s} {scenario:9s}"]
    for n in NODES:
        status, shape = yaml_state(n)
        parts.append(
            f"{n}: yaml={status}/{shape} json={json_value(n)} pkl={pickle_value(n)} "
            f"ui={fe.shown.get(n, 'pending')}"
        )
    print(" | ".join(parts) + (f" | {extra}" if extra else ""), flush=True)


@pytest.fixture(autouse=True)
def local_storage():
    with patch.object(RemoteStorageController, "is_available", return_value=False):
        yield


@pytest_asyncio.fixture()
async def previous_run(local_storage):
    shutil.rmtree(wdir(), ignore_errors=True)
    reset(rerun=False)
    apply_plan(NODES + [PP])
    write("func1", 0)
    write("func2", 0)
    write(PP)
    with patch.object(WorkflowMonitor, "search_process", return_value=object()):
        await WorkflowResult(WS, UID).observe_overall()
    assert json_value("func1") == 0.0 and yaml_state("func1") == ("success", [3, 10])
    yield
    shutil.rmtree(wdir().parent, ignore_errors=True)


async def start_rerun(fe: Frontend):
    """Rerun with nothing forced; snakemake will re-run func1 and func2"""
    reset(rerun=True)
    early = await fe.poll()  # before the child has planned
    apply_plan(NODES + [PP])
    first = await fe.poll()  # before snakemake reaches func1
    pkl("func1").unlink(missing_ok=True)  # Job.prepare of func1
    write("func1", 1, rows=5)
    await fe.poll()
    return f"early={early} first={first}"


@pytest.mark.asyncio
async def test_s4_rerun_completes(previous_run):
    fe = Frontend()
    polls = await start_rerun(fe)
    pkl("func2").unlink(missing_ok=True)
    write("func2", 2, rows=5)
    write(PP)
    await fe.poll()
    await WorkflowResult(WS, UID).observe_overall()
    report("S4-done", fe, polls)


@pytest.mark.asyncio
async def test_s5_cancel_during_func2(previous_run):
    fe = Frontend()
    polls = await start_rerun(fe)
    pkl("func2").unlink(missing_ok=True)  # Job.prepare of func2, then the user cancels
    (wdir() / "error.log").write_text("WorkflowError: job did not complete\n")
    await WorkflowResult(WS, UID).observe_overall()
    report("S5-cancel", fe, polls)


@pytest.mark.asyncio
async def test_s6_downstream_node_errors(previous_run):
    fe = Frontend()
    polls = await start_rerun(fe)
    pkl("func2").unlink(missing_ok=True)
    write("func2", error=True)
    write(PP, error=True)
    await fe.poll()
    await WorkflowResult(WS, UID).observe_overall()
    report("S6-error", fe, polls)


@pytest.mark.asyncio
async def test_s7_process_dies_during_func2(previous_run):
    fe = Frontend()
    polls = await start_rerun(fe)
    pkl("func2").unlink(missing_ok=True)  # the child is OOM-killed while func2 runs
    await fe.poll(no_process=True)
    report("S7-died", fe, polls)


@pytest.mark.asyncio
async def test_s2_rerun_nothing_to_do(previous_run):
    fe = Frontend()
    reset(rerun=True)
    apply_plan([PP])
    await fe.poll()
    write(PP)
    await fe.poll()
    await WorkflowResult(WS, UID).observe_overall()
    report("S2-noop", fe)


@pytest.mark.asyncio
async def test_s5b_cancel_then_rerun_before_old_finalization(previous_run):
    fe = Frontend()
    await start_rerun(fe)  # run N
    gen_n = ExptConfigReader.read(WS, UID).started_at
    pkl("func2").unlink(missing_ok=True)
    (wdir() / "error.log").write_text("WorkflowError: job did not complete\n")
    time.sleep(1.1)  # started_at has second resolution
    reset(rerun=True)  # run N+1, before run N's child finalizes
    kw = (
        {"generation": gen_n}
        if "generation" in inspect.signature(WorkflowResult.observe_overall).parameters
        else {}
    )
    await WorkflowResult(WS, UID).observe_overall(**kw)  # run N's finalization
    cfg = ExptConfigReader.read(WS, UID)
    gate = (wdir() / ".plan_pending").exists()
    print(
        f"RESULT {VARIANT:5s} S5b-race  | run N+1 after run N's finalization: "
        f"run={cfg.success} func1={cfg.function['func1'].success} "
        f"func2={cfg.function['func2'].success} gate_kept={gate}",
        flush=True,
    )
```

### B.3 `test_m1_probe.py` (lock-timeout finalization into a rerun)

```python
import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, Mock, patch

import pytest

from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
)
from studio.app.common.core.workflow.workflow_result import (
    WorkflowMonitor,
    WorkflowResult,
)
from studio.app.common.core.workflow.workflow_runner import WorkflowRunner
from studio.app.common.dataclass.timeseries import TimeSeriesData
from studio.tests.app.common.core.workflow.test_workflow_result_save_once import (  # noqa
    UNIQUE_ID,
    WORKSPACE_ID,
    finished_node,
    rerun,
    save_json,
)


@pytest.mark.asyncio
async def test_probe_lockless_finalization_lands_in_rerun(finished_node):
    started, finish, release_poll = (threading.Event() for _ in range(3))

    def stuck_poll():
        with WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe_lock():
            release_poll.wait(10)

    def slow_save(self, json_dir):
        started.set()
        finish.wait(10)
        save_json(self, json_dir)

    poller = threading.Thread(target=stuck_poll)
    poller.start()
    with patch.object(WorkflowResult, "OBSERVE_LOCK_WAIT_SECONDS", 0.5), patch.object(
        TimeSeriesData, "save_json", autospec=True, side_effect=slow_save
    ), patch(
        "studio.app.common.core.workflow.workflow_result.ProcessPoolExecutor",
        ThreadPoolExecutor,
    ), patch.object(
        WorkflowMonitor, "search_process", return_value=object()
    ), patch(
        "studio.app.common.routers.run._check_storage_quota", new=AsyncMock()
    ), patch(
        "studio.app.common.routers.run.ensure_structure_caches", new=AsyncMock()
    ), patch(
        "studio.app.common.routers.run.validate_input_edges"
    ), patch(
        "studio.app.common.routers.run.get_current_user_storage_usage",
        new=AsyncMock(),
    ), patch.object(
        RemoteStorageController, "is_available", return_value=False
    ), patch.object(
        WorkflowRunner, "set_smk_config"
    ):
        gen = ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).started_at
        kw = {"generation": gen} if "generation" in __import__("inspect").signature(WorkflowResult.observe_overall).parameters else {}
        fin = asyncio.create_task(WorkflowResult(WORKSPACE_ID, UNIQUE_ID).observe_overall(**kw))
        ok = await asyncio.to_thread(started.wait, 10)
        print("started:", ok, "fin done:", fin.done(), fin.exception() if fin.done() else None, fin.result() if fin.done() and not fin.exception() else None)
        assert ok
        release_poll.set()  # the stuck poll finishes and frees the lock
        await asyncio.to_thread(poller.join)
        assert await rerun() == UNIQUE_ID  # rerun is NOT refused
        reset = ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).function["func1"]
        print("after rerun:", reset.success, reset.outputPaths)
        finish.set()
        await fin
    func1 = ExptConfigReader.read(WORKSPACE_ID, UNIQUE_ID).function["func1"]
    print("after finalization:", func1.success, func1.outputPaths)
    print("pickle exists:", (finished_node / "func1" / "func1.pkl").exists())



```

### B.4 `test_worker_race.py` (W)

```python
"""Round 5's save worker re-reads the node pickle. If snakemake's Job.prepare
deletes it (or a rerun writes an error pickle) between the poll's read and the
worker's, does the poll fail? develop-main saved from the copy it had read."""

import os
from unittest.mock import AsyncMock, patch

import pytest

from studio.app.common.core.workflow import workflow_result as wr
from studio.app.common.core.workflow.workflow_result import NodeResult
from studio.app.common.routers.run import run_result
from studio.app.common.core.workflow.workflow import NodeItem
from studio.tests.app.common.core.workflow.test_workflow_result_save_once import (  # noqa
    NODES,
    UNIQUE_ID,
    WORKSPACE_ID,
    finished_node,
)

orig_init = NodeResult.__init__


@pytest.mark.asyncio
@pytest.mark.parametrize("swap", ["deleted", "error-pickle"])
async def test_pickle_swapped_after_the_poll_read_it(finished_node, swap):
    def init_then_snakemake_moves(self, workspace_id, unique_id, node_id, *a, **kw):
        orig_init(self, workspace_id, unique_id, node_id, *a, **kw)
        p = finished_node / "func1" / "func1.pkl"
        if node_id == "func1" and self.info is not None and os.getpid() == MAIN:
            if swap == "deleted":
                p.unlink()
            else:
                import pickle

                with open(p, "wb") as f:
                    pickle.dump(["Traceback: simulated"], f)

    with patch.object(NodeResult, "__init__", init_then_snakemake_moves), patch(
        "studio.app.common.routers.run.ExptConfigReader.ensure_synced_async",
        new=AsyncMock(),
    ), patch(
        "studio.app.common.routers.run.ExperimentRecordService.is_available",
        return_value=False,
    ), patch(
        "studio.app.common.routers.run.RemoteStorageController.is_available",
        return_value=False,
    ):
        try:
            res = await run_result(
                workspace_id=WORKSPACE_ID,
                uid=UNIQUE_ID,
                nodeDict=NodeItem(pendingNodeIdList=NODES),
                background_tasks=__import__("unittest.mock").mock.Mock(),
                remote_bucket_name="",
            )
            print(f"RACE {swap}: answered {({k: v.status for k, v in res.nodeResults.items()})}")
        except Exception as e:
            print(f"RACE {swap}: poll raised {type(e).__name__}: {getattr(e, 'status_code', '')} {getattr(e, 'detail', e)}")


MAIN = os.getpid()
```
