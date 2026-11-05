# Tier Resize Runbook

A workflow tier's instance size is a configuration change: an instance type and a sizing override in
the environment's tfvars. This runbook is the prepared change to each tier's next size, when to apply
it, how to apply and verify it, and how to revert it. Today's sizes are the defaults in `main.tf`
(`free_instance_type`, `premium_instance_type`, `local.*_tier_sizing_defaults`).

## Executive Summary

- **Only on a measured trigger.** Two concurrent runs failing, a pair taking more than 2.5 times one
  run, or `/health` unanswered for 2 minutes. Each is measured with the workflow benchmark's AWS lane.
- **The host first, then the task.** Phase 1 changes the instance type and the volumes; the old task
  fits the new host. Phase 2 raises the task's CPU and memory, once every instance of the tier runs
  the new type: on an old instance the larger task is never placed.
- **Revert in reverse.** Phase 2 back first (the smaller task fits either host), then phase 1.
  Removing the override returns the tier to its defaults.
- **The plan rejects a set that cannot work.** A task above its instance type, a root volume below the
  AMI's root snapshot, and gp3 IOPS or throughput out of range fail at plan time, not at the next
  launch.
- **Each phase restarts the tier's work.** A refresh replaces the free instances; a premium type
  change stops the base premium instance, and the per-user ones are replaced; a task-definition
  change restarts the tier's tasks. A run in progress on the tier at that moment is lost, so each
  phase goes in an announced window.
- **Rehearsed on development**: not yet (see Rehearsal).

---

## When to Resize

Measure with the benchmark's AWS lane (`infrastructure/scripts/benchmark/README.md`, "On AWS"):
`aws/run_cases.sh` on one standalone instance of the tier's current type, at the tier's ceiling
(variant S, runs started through the API). Set `BENCH_MEMORY_MIB` and `BENCH_SWAP_MIB` to the tier's
`container_memory_mib` and `container_max_swap_mib`, and `BENCH_UVICORN_WORKERS` to its worker count
(the task definition's `UVICORN_WORKERS`).

| Trigger | Read from the rows | Fires when |
|---|---|---|
| Two concurrent runs do not both complete | `ROWS-WITH-FAILED-RUNS` at the end of `run_cases.sh`; `OOM kills in cgroup` in a two-run row's `summary.md` | a two-run row is listed, or its OOM kill count is above 0 |
| A pair takes too long | `wall clock of the runs` in each `summary.md` | a two-run row on the single run's input (`S2P-2`, `S2P-CM`) takes more than 2.5 times the single run (`S2P-1`); CPU sharing alone gives about 2 times |
| The API stops answering | the `/health` line in each `summary.md` | the longest stall is 120 s or more (two of the load balancer's 60 s checks; five replace the instance) |

A trigger names the tier that needs the resize; it does not by itself prove the next size clears it.
Before the window, run the same rows on one instance of the next size, with `BENCH_MEMORY_MIB` set to
the value set's `container_memory_mib`, and check that every row clears it (below).

The premium tier's next size is for three runs per user, so add a three-run row on that instance
after `run_cases.sh` has finished (it has built the conda environments and fetched the inputs). Give
it `run_cases.sh`'s row options, every vCPU included (`run_bench.sh` alone limits the container to 2),
in the same environment (`BENCH_S3_PREFIX`, `BENCH_UVICORN_WORKERS`, `BENCH_MEMORY_MIB`,
`BENCH_SWAP_MIB`):

```bash
BENCH_LANE=aws /opt/bench/repo/infrastructure/scripts/benchmark/run_bench.sh --label S2P-3 \
  --results-dir /opt/bench/results --algo suite2p \
  --input /opt/bench/inputs/neurofinder.00.10_stack.tif --conc 3 \
  --variant S --start api --cpus "$(nproc)" --timeout 3600
aws s3 sync /opt/bench/results/ "$BENCH_S3_PREFIX/results/"
```

A row clears a size when every run completes with no OOM kill and the longest `/health` stall is
under 120 s. A row of k concurrent runs on the single run's input (`S2P-2`, `S2P-CM`, `S2P-3`) must
also take at most 1.25 × k times `S2P-1` on the same instance. For a pair that is the trigger's 2.5
times: CPU sharing alone makes k runs take at most k times one run, and the rule allows a quarter
more. Three runs clear it within 3.75 times. The long row (`S2P-L`) and the short rows have no single
run of their own length to compare with, so only the first sentence applies to them.

---

## The Prepared Value Sets

One set per tier, in two parts: phase 1 (the host) and phase 2 (the task). They go into the
environment's tfvars, the file every apply reads, so a later routine apply keeps them. A separate
`-var-file` would be dropped by the next apply that does not name it, and revert the resize. Where
the tfvars already sets the variable, merge the fields into it: a second assignment of the same
variable fails the plan.

| | Free | Premium |
|---|---|---|
| Next type | `r7i.large`, 16 GiB, 2 vCPU | `m7i.xlarge`, 16 GiB, 4 vCPU |
| Holds | two runs with room to spare; three | three runs per user, each with its own vCPU beside the API |
| Volume throughput | 125 MiB/s: the instance's EBS baseline (about 81 MB/s) is below it | 150 MiB/s: the instance's EBS baseline (about 156 MB/s) is above gp3's 125 |
| IOPS | 3,000 (gp3's included baseline) | 3,000 |

### Free tier

```hcl
# Phase 1: the host
free_instance_type = "r7i.large"
free_tier_sizing = {
  root_volume_iops             = 3000
  root_volume_throughput_mibps = 125
  swap_volume_iops             = 3000
  swap_volume_throughput_mibps = 125
}
```

```hcl
# Phase 2: the task (replaces phase 1's free_tier_sizing)
free_instance_type = "r7i.large"
free_tier_sizing = {
  task_memory_mib              = 14848
  container_memory_mib         = 14336
  root_volume_iops             = 3000
  root_volume_throughput_mibps = 125
  swap_volume_iops             = 3000
  swap_volume_throughput_mibps = 125
}
```

### Premium tier

```hcl
# Phase 1: the host
premium_instance_type = "m7i.xlarge"
premium_tier_sizing = {
  root_volume_iops             = 3000
  root_volume_throughput_mibps = 150
  swap_volume_iops             = 3000
  swap_volume_throughput_mibps = 150
}
```

```hcl
# Phase 2: the task (replaces phase 1's premium_tier_sizing)
premium_instance_type = "m7i.xlarge"
premium_tier_sizing = {
  task_cpu                     = 4096
  container_cpu                = 3072
  task_memory_mib              = 14848
  container_memory_mib         = 14336
  root_volume_iops             = 3000
  root_volume_throughput_mibps = 150
  swap_volume_iops             = 3000
  swap_volume_throughput_mibps = 150
}
```

### How the sets are derived

- **Memory**: task = the type's memory − 1,536 MiB, container = task − 512 MiB. Today's defaults leave
  1,024 and 1,536 MiB; a 16 GiB host's kernel takes about 300 MiB more, so the sets leave 512 MiB more
  to the host. ECS registers about 95 % of the nominal memory (`t3.large`: 7,823 of 8,192 MiB) and
  places a task only within it.
- **CPU**: task = vCPU × 1,024, container = three quarters of it, as today.
- **Left out on purpose**: the memory reservation, swap ceiling, swappiness and the root and swap
  volume sizes. They follow the defaults, so a later change to a default reaches the resized tier too.
- **Another size**: the same rules. The plan checks the result (next section); the rows of "When to
  Resize" check that it holds the load.
- **Not changed with the size**: the API's worker count, which is chosen for memory, not vCPU.

---

## What the Plan Checks

Preconditions in `compute.tf` and variable rules in `main.tf`; each fails the plan with a message
naming the field.

| Rule | On | Why |
|---|---|---|
| Task memory ≤ 15/16 of the instance type's memory | each tier's task definition | ECS registers less than the nominal memory and never places a larger task. 15/16 stays below the registered share from 4 GiB up |
| Task CPU ≤ the type's vCPUs × 1,024 | each tier's task definition | The registered CPU; above it the task is never placed |
| Root volume ≥ the AMI's root snapshot | each tier's launch template | The AMI the template boots, stock or custom; below it every launch fails after the template is accepted |
| Throughput ≤ IOPS / 4; IOPS ≤ 500 per GiB of the volume (3,000 on any size) | each tier's launch template | gp3's ratios; unset fields count as 3,000 IOPS and 125 MiB/s |
| IOPS 3,000–80,000; throughput 125–2,000 MiB/s | the sizing variables | gp3's range |

The checks read the configured instance type, not the running instances. That is why phase 2 waits
until every instance of the tier runs the new type.

**If the custom AMI is gone, every plan fails.** The AMI check looks the image up by the ID the
templates boot. With `use_custom_ami`, that ID is the SSM parameter, which is updated by hand after
each pipeline build, while the Image Builder lifecycle deletes the project's images after 90 days.
A parameter left on a deleted image fails every plan at `data.aws_ami.effective` with "Your query
returned no results", whatever the plan changes; launches from the templates already fail by then.
Recover by pointing the parameter at a live image (`CUSTOM_AMI_ARCHITECTURE.md`, Procedure B,
steps 4 and 5), then plan again. Do not switch `use_custom_ami` off to get past it: that also removes the
Image Builder pipeline and the parameter.

---

## Before the Window

1. **Measured**: a trigger fired, and the next size cleared the same rows (When to Resize).
2. **Offered**: the type is offered in the availability zones of both private subnets.

   ```bash
   aws ec2 describe-instance-type-offerings --region ap-northeast-1 \
     --location-type availability-zone --filters Name=instance-type,Values=<type> \
     --query 'InstanceTypeOfferings[].Location' --output text
   # Expected: both availability zones of the private subnets
   ```

3. **Announced**: the window, through the announcement and, in production, the maintenance page
   (`MAINTENANCE_PROCEDURES.md`, "Planned Downtime: Announcement and Maintenance Page"). Runs in
   progress on the tier at an apply are lost.
4. **The deployment checkout**: on the commit being deployed, the environment's tfvars present, and
   the backend initialised for that environment (`INFRA_DEPLOYMENT_PROCEDURE.md`).

Names below use `<env>`, the Terraform `environment` value: `development` or `subscr`.

---

## Apply: Free Tier

### Phase 1 — the host

1. Add phase 1's block to the tfvars, then plan.

   ```bash
   terraform plan -var-file=environments/<env>.tfvars
   # Expected: aws_launch_template.ecs updated in place (instance type, volumes), and
   # aws_autoscaling_group.main updated in place (its LaunchTemplateVersion tag). Nothing else
   ```

2. Apply. Running instances are unchanged: the group uses the template's `$Latest`, and a refresh does
   not start by itself.
3. Raise the floor by one with `FREE_TIER_PREPROVISIONING_RUNBOOK.md`, steps 1–4 (N = today's instance
   count + 1), so that the refresh never takes the tier's last instance. The new instance launches as
   the new type.
4. Replace every instance, one at a time:

   ```bash
   aws autoscaling start-instance-refresh --region ap-northeast-1 \
     --auto-scaling-group-name <env>-optinist-asg \
     --preferences '{"MinHealthyPercentage":50,"InstanceWarmup":300}'
   aws autoscaling describe-instance-refreshes --region ap-northeast-1 \
     --auto-scaling-group-name <env>-optinist-asg \
     --query 'InstanceRefreshes[0].[Status,PercentageComplete]' --output text
   # Expected, repeated until it holds: Successful 100
   ```

   Terminate-first is the only refresh that completes here (`CUSTOM_AMI_ARCHITECTURE.md`, "Active
   Instance Replacement"); the raised floor keeps one instance serving throughout.
5. Check that every free instance runs the new type (Verify, step 1), then lower the floor again with
   the pre-provisioning runbook, steps 6–7.

### Phase 2 — the task

1. Replace the block with phase 2's, then plan.

   ```bash
   terraform plan -var-file=environments/<env>.tfvars
   # Expected: aws_ecs_task_definition.autoscaling replaced (a new revision) and
   # aws_ecs_service.autoscaling updated in place. Nothing else
   ```

2. Apply. ECS stops the free tasks and starts the new revision. The service's minimum healthy
   percent is 0, so every task can stop at once: the free tier is unavailable until the new tasks
   pass the health check.
3. Verify (below).

---

## Apply: Premium Tier

### Phase 1 — the host

1. Add phase 1's block to the tfvars, then plan.

   ```bash
   terraform plan -var-file=environments/<env>.tfvars
   # Expected: aws_launch_template.premium updated in place; aws_instance.premium[0] at the new
   # type; aws_lambda_function.premium_manager updated (PREMIUM_INSTANCE_TYPE). If the base
   # instance is replaced, the Lambdas and the dashboard that name its ID are updated too
   ```

2. Note the base premium instance's state, apply, and read it again:

   ```bash
   aws ec2 describe-instances --region ap-northeast-1 \
     --filters "Name=tag:Name,Values=<env>-premium-initial" \
       "Name=instance-state-name,Values=pending,running,stopping,stopped" \
     --query 'Reservations[].Instances[].[InstanceId,InstanceType,State.Name]' --output text
   # Expected after the apply: the new type, in the state it had before
   ```

   The base instance changes to the new type, interrupting a user on it, and the premium manager
   launches new per-user instances at the new type. If the base instance was stopped and the apply
   leaves it running, stop it (`aws ec2 stop-instances --instance-ids <id>`). The premium manager's
   periodic scale-down always keeps the last running instance up, and only a sign-out that leaves
   no premium user stops every idle instance, so it would stay running until then.
3. Remove the per-user instances of the old type. The premium manager stops an idle instance and
   starts it again at the next sign-in, so without this they keep the old type. This terminates every
   per-user instance, running or stopped, and clears its records; the base instance stays.

   ```bash
   aws lambda invoke --region ap-northeast-1 --function-name <env>-premium-manager \
     --cli-binary-format raw-in-base64-out --payload '{"action": "cleanup_all_dynamic"}' \
     /dev/stdout
   # Expected: "terminated" lists the per-user instances; "errors" is empty
   ```

4. Check that no premium instance of the old type is left:

   ```bash
   aws ec2 describe-instances --region ap-northeast-1 \
     --filters "Name=tag:Name,Values=<env>-premium-*" \
       "Name=instance-state-name,Values=pending,running,stopping,stopped" \
     --query 'Reservations[].Instances[].[InstanceId,InstanceType,State.Name]' --output text
   # Expected: every line shows the new type
   ```

### Phase 2 — the task

1. Replace the block with phase 2's, then plan.

   ```bash
   terraform plan -var-file=environments/<env>.tfvars
   # Expected: aws_ecs_task_definition.premium replaced (a new revision) and
   # aws_ecs_service.premium updated in place. Nothing else
   ```

2. Apply. Every running premium task restarts at once: the service does not keep the old task up
   during a deployment.
3. Sign in as a premium test user, so that an instance starts at the new size, and verify (below).

---

## Verify at the New Size

1. **The hosts are registered at the new size**, and hold the task:

   ```bash
   aws ecs list-container-instances --region ap-northeast-1 \
     --cluster <env>-optinist-cloud-cluster --filter "attribute:tier == <free|premium>" \
     --query 'containerInstanceArns' --output text
   aws ecs describe-container-instances --region ap-northeast-1 \
     --cluster <env>-optinist-cloud-cluster --container-instances <the ARNs above> \
     --query 'containerInstances[].[attributes[?name==`ecs.instance-type`]|[0].value,registeredResources[?name==`MEMORY`]|[0].integerValue,registeredResources[?name==`CPU`]|[0].integerValue,runningTasksCount]' \
     --output text
   # Expected: the new type; MEMORY at least task_memory_mib, CPU at least task_cpu; 1 running task
   ```

2. **The API runs its worker count**: free 3, premium 2.

   ```bash
   aws logs filter-log-events --region ap-northeast-1 \
     --log-group-name /ecs/<env>-optinist-cloud-taskdef --start-time <apply time, epoch ms> \
     --filter-pattern '"Uvicorn workers"' --query 'events[].message' --output text
   # Premium: --log-group-name /ecs/<env>-premium-optinist-cloud-taskdef
   # Expected: "Uvicorn workers: 3" (free) or "Uvicorn workers: 2" (premium) for each new task
   ```

3. **`/health` answers**: free, the pre-provisioning runbook's step 4 chain (healthy targets = running
   tasks); premium, the task's health status is `HEALTHY` in `aws ecs describe-tasks`. In a maintenance
   window, check `/health` from inside the container (`MAINTENANCE_PROCEDURES.md`, its runbook step 3).
4. **The load it was sized for completes**: from the Web UI, one suite2p run alone on the
   benchmark's reference input (about 1.45 GB), then two at once for the free tier, three for the
   premium tier. Each completes, and the k runs together take at most 1.25 × k times the single
   run's time: 2.5 times for two, 3.75 times for three (When to Resize).

---

## Revert

Delete the tier's lines from the tfvars in two steps, the reverse of the apply:

1. **Phase 2 back**: restore phase 1's block (the type stays, the sizing keeps only the volumes), plan
   and apply. The default task fits the larger host; the tier's tasks restart.
2. **Phase 1 back**: delete the block, plan and apply.
   - Free: the floor, the refresh and the floor again, as in phase 1, steps 3–5.
   - Premium: the base instance returns to the default type (its state checked as in phase 1,
     step 2); then terminate the per-user instances of the larger type as in phase 1, step 3, so
     that they stop costing the larger size.
3. Verify, steps 1–3, at the default size.

---

## Rehearsal

Not yet rehearsed. The rehearsal applies both tiers' sets on development, phase by phase, verifies
them, and reverts them. It also settles what this runbook states without a measurement: the time of
each phase, whether the raised floor keeps the free tier serving through the refresh, whether the
base premium instance is replaced or changed in place and the state it is left in, and the
registered memory of the new types.

---

## References

- Issue #1111 — the resize kit: value sets, guards, this runbook, the rehearsal
- Issue #894 — the sizing study and its verification run; the triggers' figures are in #891, section 8
- Issue #893 and `infrastructure/scripts/benchmark/README.md` — the benchmark and its AWS lane
- Issue #1029 — the per-tier sizing variables; issue #1031 — the API's worker counts
- [FREE_TIER_PREPROVISIONING_RUNBOOK.md](FREE_TIER_PREPROVISIONING_RUNBOOK.md) — raising and lowering
  the free tier's floor
- [CUSTOM_AMI_ARCHITECTURE.md](CUSTOM_AMI_ARCHITECTURE.md) — how a template change reaches each kind
  of instance
- [PREMIUM_MANAGER_ARCHITECTURE.md](PREMIUM_MANAGER_ARCHITECTURE.md) — the premium manager's actions,
  `cleanup_all_dynamic` among them
- [MAINTENANCE_PROCEDURES.md](MAINTENANCE_PROCEDURES.md) — announcements and the maintenance page
