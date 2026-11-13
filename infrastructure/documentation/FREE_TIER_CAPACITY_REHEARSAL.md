# Free-Tier Capacity Rehearsal: Pre-provisioning and Reactive Scaling on Development

This document rehearses on development the
[pre-provisioning runbook](FREE_TIER_PREPROVISIONING_RUNBOOK.md) and the Free Manager's reactive
scaling, so that an event can depend on both. Run it in pre-release testing of any release that
changes the Free Manager, the free-tier Auto Scaling Group or its Terraform
([DEPLOYMENT_PROCEDURE.md](DEPLOYMENT_PROCEDURE.md), *Pre-Release Testing*), and before the first
event that depends on either mechanism.

## Executive Summary

- **Two ways free-tier capacity grows, both required.** **Pre-provisioning** raises the Auto
  Scaling Group's minimum ahead of expected load, and serves the first wave of a burst; the
  runbook is how. **Reactive scaling** is the Free Manager following the active-user count, out
  and back in, with the `-high` load alarms adding capacity on load. It cannot serve the first
  wave: a 5-minute polling interval, an instance launch, and sticky sessions that keep active
  users where they are.
- **Development only, by construction.** The helpers hold every target as a development constant,
  every write passes one guard, and production is read before and after the session and compared.
- **Eight cases, in two parts.** PP-1 to PP-5 run the runbook's steps at N = 3; RS-1 to RS-3 show
  the group growing with the user count and returning to its baseline.
- **Run by an operator, not by CI.** The cases move real capacity on development and take about
  three hours.

The mechanism itself is described in [FREE_MANAGER_ARCHITECTURE.md](FREE_MANAGER_ARCHITECTURE.md).

---

## Overview

| Case | Part | What it shows |
|---|---|---|
| **PP-1** | Pre-provisioning | Raising the ceiling, then the floor, brings the group to N instances serving |
| **PP-2** | Pre-provisioning | An apply returns both CLI-raised bounds to the tfvars values (read-only plan) |
| **PP-3** | Pre-provisioning | The raised capacity holds through an idle period: the Free Manager does not take it down |
| **PP-4** | Pre-provisioning | New sessions reach every pre-provisioned instance; a session in progress stays put |
| **PP-5** | Pre-provisioning | Floor first, ceiling last: the Free Manager returns the group to its baseline |
| **RS-1** | Reactive scaling | The group grows with the user count: 6 active users take it from 1 instance to 2 |
| **RS-2** | Reactive scaling | New sessions reach the instance RS-1 added |
| **RS-3** | Reactive scaling | With the users gone, the Free Manager returns the group to `MinSize` on its own |

Allow about 75 minutes for PP-1 to PP-5 and 90 for RS-1 to RS-3. The two parts can be run on
different days; each starts from Step 1.

---

## What the Cases Exercise

PP-1 to PP-5 carry out the [runbook](FREE_TIER_PREPROVISIONING_RUNBOOK.md)'s steps on development
at N = 3, with helpers in place of its commands: `fts_set_bounds` for the bounds (steps 2, 3, 6
and 7) and `fts_chain` for the settled check (step 4). The runbook gives each step's reason and the
timings measured here; the cases give what to run and what must be seen.

---

## Rehearsal Safety

### Development only, by construction

- **Every target is a constant in the helpers.** No function takes an environment, a resource name
  or a variable that could point it elsewhere. Commands in this document name development
  resources literally, or call a helper. Do not adapt either for another environment.
- **Every write goes through a guard.** `fts_assert_dev` refuses unless `AWS_PROFILE` is
  exported, every target constant is a `development-` name, and the free ASG itself reports
  `Environment = Development`. The writes are `fts_set_bounds`, `fts_map`, `fts_touch`,
  `fts_activity_loop`, `fts_cleanup_users` and `fts_login`; nothing else writes.
- **`fts_set_bounds` sets the minimum and maximum, never desired capacity,** and refuses a maximum
  below the current desired capacity, for the reason in the runbook's step 7.
- **Production is read before and after, and compared.** `fts_prod_snapshot` records production's
  free ASG bounds, instances, hooks, policies and ECS desired count, read only; every session ends
  with `fts_prod_compare`, which must print `PRODUCTION UNCHANGED`.
- **Production and development share one AWS account,** told apart only by the name prefix. No
  account boundary catches a mistake, which is why the names are constants rather than inputs.

### Constraints

- **Six free-tier test accounts for RS-1,** driven without logging in. Active users are rows in
  `free_user_assignments`, one per account, and a target of k instances by user count needs
  `5 × (k − 1) + 1` active accounts: six for 2, eleven for 3. That is why PP-1 raises capacity by
  the minimum and RS-1 stops at 2. Which accounts exist depends on how the environment's test
  pool was created; resolve their database ids before the session (read only):
  ```bash
  fts_pool_ids 10                  # accounts named optinist_test_user_free_*, with their ids
  fts_user_id <account email>      # any other test account, by email
  ```
  Use six free-tier test accounts nobody else is using, and note their ids as `<U1>` to `<U6>`.
- **One account that can log in,** a free-tier user whose password the operator has, for PP-4 and
  RS-2. It may be one of the six.
- **On-hours only.** The development scheduler stops the environment in the evening and rewrites
  the group's bounds on start. The Free Manager's schedule rule is a delayed rule: it stays
  disabled for 15 minutes after a scheduled start. Start at least 15 minutes after it.
- **No apply to development during a session.** An apply returns both bounds to the tfvars values
  (PP-2): the raised floor goes mid-case, and a tfvars maximum below the current desired capacity
  terminates instances the group chooses.
- **Nobody else on development.** The count includes every active row, not only the pool's.

### Not to be used: `infrastructure/scripts/test_autoscaling_user_number.py`

It was written for the model before the capacity-bounds and scale-in changes: it assumes a 5-user
threshold for entering the scaling path, and that the Free Manager moves users between instances by
rewriting their assignment rows, which does not affect routing. Its API base URL defaults to
production. It has never had a full run.

---

## Rehearsal Procedure

### Session setup

In every terminal used below, including the one an activity loop runs in:

```bash
export AWS_PROFILE=<the profile for the account hosting development>
source infrastructure/scripts/free_tier_capacity_helpers.sh
```

The helpers keep their working files in `$FTS_STATE_DIR` (default `$TMPDIR/free-tier-capacity`,
mode 0700). Terminals must share it; `fts_whoami` prints it.

### Step 0: Production snapshot

```bash
fts_prod_snapshot before
```

Prints and saves production's free-tier state. Nothing to judge yet; Finish compares against it.

### Step 1: Preflight

```bash
fts_preflight
```

**Must end `PREFLIGHT OK`.** It checks, and prints `STOP:` for each that fails; a check whose
read fails is a `STOP` too:

| Check | Why |
|---|---|
| The chain is settled | A case started unsettled measures the settling |
| No suspended process, no instance refresh running | Either stops a launch |
| Scaling lock clear (no `1` in the last 20 minutes) | A set lock makes the Free Manager skip scaling, and the run looks silent |
| The load, errors and unhealthy-hosts alarms all exist, none in `ALARM` | `cpu-high` / `memory-high` scale out on their own and hold scale-in; an errors alarm means the Free Manager is failing; `free-tg-unhealthy-hosts` is what the cases watch for a new instance |
| Schedule rule `ENABLED` | Every case is driven by the scheduled run |
| NAT running | Off-hours, or a stopped environment |
| Active users 0 | The cases need an exact count |

It also prints the lifecycle hooks. Expected: `development-optinist-launch-hook` (the boot wait)
and `ecs-managed-draining-termination-hook`. A `-terminate-hook` as well means the retired hook
was never deleted, and every scale-in takes a few minutes longer; see *Lifecycle Hooks* in
[FREE_MANAGER_ARCHITECTURE.md](FREE_MANAGER_ARCHITECTURE.md). **Record the starting bounds** from the chain line as
`<min0>` and `<max0>` (normally the environment's tfvars values): the cases restore to them.
`fts_whoami` prints the idle threshold the Free Manager uses (`FREE_IDLE_THRESHOLD_MINUTES`); the
cases below call it the idle threshold.

---

### PP-1: Ceiling, then floor

The runbook's steps 2 to 4. The rehearsal takes N = 3, from Step 1's bounds with no active users,
so desired capacity starts at `<min0>`. Raise the ceiling only if `<max0>` is below 3:

```bash
fts_set_bounds <min0> 3   # WRITE: the ceiling first; desired stays where it is
fts_set_bounds 3 3        # WRITE: then the floor; the group raises desired to 3 and launches the difference
```

Note the time of the second call: **T0**. Then poll every minute or two:

```bash
fts_chain
fts_instances
fts_activities 3
fts_log 10
```

| Mark | Where it shows | Expected |
|---|---|---|
| T1 — launched | `fts_activities`: one `Launching a new EC2 instance` per added instance; `fts_instances`: `Pending:Wait` | Seconds after T0 |
| T2 — ECS raised | `fts_log`: `Event Type: EC2 Instance-launch Lifecycle Action`, then `Syncing ECS from <min0> to 3` | Within seconds of the launch, while the instances wait on the launch hook |
| T3 — in service | `fts_instances`: `InService` | About 300 s after T1, when the launch hook ends |
| T4 — serving | `fts_chain`: `CHAIN OK at 3` | Under 30 minutes; record the figure (about 7 to 9 minutes on development) |

**Pass:** `CHAIN OK at 3` within 30 minutes of T0, nothing rejected, `free-manager-errors` and
`free-tg-unhealthy-hosts` `OK` throughout (`fts_alarms`): a new target is never unhealthy. The
next scheduled run logs `Calculated desired instances: 3`.

### PP-2: What an apply would do to CLI bounds

The runbook's step 5. While PP-1 holds the group at 3, from the deployment checkout, initialised
against the development backend, with the development tfvars:

```bash
terraform plan -lock=false -var-file=environments/development.tfvars \
  -target=aws_autoscaling_group.main > /tmp/pp2_plan.txt 2>&1
grep -E '^\s+# |min_size|max_size|desired_capacity|Plan:' /tmp/pp2_plan.txt
```

Read only: `-lock=false`, and nothing is applied. **Do not apply it.**

**Pass:** the group updated in place, `min_size` and `max_size` back to the tfvars values,
`desired_capacity` absent from the diff, and nothing else in the plan. Read the `max_size` line
against the current desired capacity: where the tfvars maximum is below it (`<max0>` below 3),
applying this plan would make the group lower desired capacity to `<max0>` at once and terminate
the difference itself, blind to workloads. The floor alone going would leave the instances to the Free Manager. That is why the
runbook freezes applies for the window, and restores through tfvars in two applies, floor first.

### PP-3: The raised capacity holds while idle

With no active users, let **three scheduled runs** pass (15 minutes):

```bash
fts_log 20                # every run: Active users: 0, Calculated desired instances: 3, no "Scaling down"
fts_chain                 # CHAIN OK at 3 throughout
fts_activities 3          # no Terminating activity since T1
fts_target                # TargetInstances 3.0
```

**Pass:** no instance terminated, and the target stays at `MinSize`.

### PP-4: Every pre-provisioned instance takes new sessions

In the operator's own terminal, with the session setup done there:

```bash
fts_login <a free-tier account email>    # WRITE: a login; prompts for the password without echo
```

The token is kept in `$FTS_STATE_DIR/auth_header` (0600) and never printed. The login makes that
account active for the idle threshold; with a floor of 3 that changes nothing.

```bash
fts_sessions 20           # 20 requests, no cookie jar: each a new session
fts_sticky 5              # 5 requests holding one cookie jar
```

**Pass:** every `fts_sessions` line is `200`, and **three distinct `x-served-by-instance` values**
appear; the five `fts_sticky` values are identical. Do not expect an even split: round robin
counts requests, and each load-balancer node keeps its own rotation. Keep the token if RS-2
follows the same day; otherwise `fts_forget_token`.

### PP-5: Floor first, ceiling last

The runbook's steps 6 and 7.

```bash
fts_set_bounds <min0> 3   # WRITE: the floor back; the maximum stays; desired left to the Free Manager
```

Note the time: **T0** (each case's marks start again from T0). Then:

```bash
fts_log 10                # the next run: Calculated desired instances: <min0>, Scaling down from 3 to <min0>
fts_activities 3          # one Terminating activity per surplus instance, and when each completed
fts_chain                 # until CHAIN OK at <min0>
```

A surplus of 2 or more goes in one run; a surplus of exactly 1 waits for three completed periods
first. Each termination is released when ECS's draining hook completes (about 8 minutes); the ECS
desired count steps down as each instance leaves. Once the chain is at `<min0>`:

```bash
fts_set_bounds <min0> <max0>   # WRITE: the ceiling back; refused while desired is above <max0>
```

**Pass:** the Free Manager, not the group's policy, removes the surplus (`Scaling down` in its
log); `CHAIN OK at <min0>` at the starting bounds. Record T0 to the decision and T0 to the chain
settling.

---

### RS-1: The group grows with the user count

Starts from Step 1, at the starting bounds. **It assumes `<min0>` is 1 and `<max0>` is 2 or
more:** six users ask for 2 instances, which a higher floor already holds and a ceiling of 1
refuses.

**Expected:** with 6 active users the target is `ceil(6 / 5) = 2`. The next scheduled run raises
the ASG and ECS desired counts to 2 at once, then waits for the new instance to register.

#### 1. Make six users active on the current instance

```bash
fts_cleanup_users                       # WRITE: clears leftover pool rows
fts_instances                           # note the InService instance id: <A>
fts_map <U1>:<A> <U2>:<A> <U3>:<A> <U4>:<A> <U5>:<A> <U6>:<A>
fts_touch                               # WRITE: one pass; every user must read "ok"
fts_active                              # must read active_user_count 6
```

Then keep them active for the rest of the case, in its own terminal or as a background job:

```bash
fts_activity_loop 45 120                # WRITE: one pass every 120 s for up to 90 min
```

Every pass prints one `u<id>:ok` per user. `u<id>:FAILED` means that user has no fresh row and the
count will be short. The interval must stay well inside the idle threshold: 120 s against a
5-minute threshold leaves room for one missed pass.

Note the time the count first read 6: **T0**.

#### 2. Watch the scheduled run scale out

Poll every minute or two:

```bash
fts_chain
fts_log 10
fts_activities 3
```

| Mark | Where it shows | Expected |
|---|---|---|
| T1 — the decision | `fts_log`: `Active users: 6`, `Calculated desired instances: 2`, `Scaling up from 1 to 2 instances`, `Scaling ASG … to desired capacity: 2`, `Successfully updated ECS service … to 2 tasks` | The first scheduled run after T0 (within 5 minutes) |
| T2 — the launch | `fts_activities`: `Launching a new EC2 instance: <B>`; `fts_instances`: `Pending:Wait` for about 300 s (the launch hook), then `InService` | Seconds after T1 |
| T3 — the second task running | `fts_chain`: ECS `2/2` | A few minutes after T2 |
| T4 — serving | `fts_chain`: `CHAIN OK at 2` | Within about 10 minutes of T1 |

The run waits for the new container instance to register (`All instances ready!`, which counts
registered instances, not running tasks), then rebalances by rewriting three users' assignment
rows to `<B>` and ends with `Rebalancing successful` and `Scaling lock CLEARED`, a few minutes
after T1. Each moved user also logs `Error migrating user … No module named 'requests'`: the row
is moved, the experiment sync that follows it is not. That is a known defect, tracked
separately; it does not affect this case.

**The lock outlives its clearing.** The lock is read as the maximum of `ScalingInProgress` over the
last 15 minutes, so every scheduled run within 15 minutes of the lock being set logs
`Scaling operation already in progress, skipping`, cleared or not. Expect three skipped runs after
T1; no second scale-out and no scale-in can be decided in that window.

#### 3. Re-pin half the users to the new instance

The loop pins every user to `<A>` on every pass, undoing the Free Manager's rewrite; in the real
app a user's row follows the instance that served their last request. Once T3 is reached, move
three users to `<B>`, as their next requests would:

```bash
fts_map <U1>:<A> <U2>:<A> <U3>:<A> <U4>:<B> <U5>:<B> <U6>:<B>   # WRITE: the running loop picks it up next pass
```

Without this, every later run finds six users on one instance and rebalances again.

**Pass:** T1 within one schedule period of T0; ASG and ECS desired raised together; the new
instance `InService` after the launch hook; `CHAIN OK at 2`; `free-manager-errors` and
`free-tg-unhealthy-hosts` `OK` throughout
(`fts_alarms`); and `fts_target` showing `2.0` from the run at T1 on.

### RS-2: New sessions reach the new instance

Run while RS-1 holds the group at 2. If no token is kept from PP-4, log in as in PP-4. The login
may make the count read 7: the target is still 2.

```bash
fts_sessions 20
fts_sticky 5
```

**Pass:** every `fts_sessions` line is `200` and **two distinct `x-served-by-instance` values**
appear; the five `fts_sticky` values are identical.

### RS-3: Back to the baseline on its own

```bash
fts_activity_stop                        # the loop ends after its current sleep
fts_cleanup_users                        # WRITE: clears the pool rows it can select
fts_active                               # 0 once every remaining row has aged out
```

`cleanup_all_test_users` selects accounts named `optinist_test_user_free_%` only. Accounts named
otherwise keep their rows, and the count stays above 0 until the loop's last pass is older than
the idle threshold. Nothing more to do: with no further passes they age out.

Then watch. With the target at `MinSize` the surplus is 1, removed only once it has persisted for
three completed periods: the scale-in is decided about 15 minutes after the users are cleared,
chooses an instance with no running workflow, and is released when ECS's draining hook completes
(about 8 minutes).

```bash
fts_log 10                               # "a surplus of 1 that has not persisted yet", then "Scaling down from 2 to 1"
fts_chain                                # until CHAIN OK at 1
```

**Pass:** the Free Manager removes the instance on its own, and `CHAIN OK at 1` at the starting
bounds.

---

### Finish: production untouched

At the end of every session:

```bash
fts_prod_snapshot after
fts_prod_compare before after            # must print PRODUCTION UNCHANGED
fts_forget_token
```

A difference is read before anything else. Production can change on its own (a real scale-out by
its users, or someone else's work), so a difference is not by itself proof of a mistake, but it is
never left unexplained.

**Pass:** development at its starting bounds with `CHAIN OK`, `free-manager-errors` `OK`, and
`PRODUCTION UNCHANGED`.

---

## Results

Record each run as a comment on the tracking issue, with this table.

| | Value |
|---|---|
| Date, commit deployed on development | |
| Starting bounds, hooks present | |
| PP-1: T0 → T1 (floor raised → launched); T0 → T3 (→ `InService`); T0 → T4 (→ `CHAIN OK at 3`); `free-tg-unhealthy-hosts` stayed `OK` | |
| PP-2: plan diff | |
| PP-3: three idle runs, target and instances | |
| PP-4: distinct `x-served-by-instance` values in 20 new sessions; sticky 5 of 5 | |
| PP-5: T0 → decision; T0 → `CHAIN OK at <min0>`; who chose the instances | |
| RS-1: T0 → T1 (count reaches 6 → decision); T1 → T4 (→ `CHAIN OK at 2`); skipped runs after T1 | |
| RS-2: distinct values in 20 new sessions; sticky 5 of 5 | |
| RS-3: users cleared → scale-in decision; → `CHAIN OK at 1` | |
| `PRODUCTION UNCHANGED` | |

---

## References

- Requirements, the case history and the first rehearsal's results: issue #890
- [FREE_TIER_PREPROVISIONING_RUNBOOK.md](FREE_TIER_PREPROVISIONING_RUNBOOK.md) — the steps PP-1 to
  PP-5 rehearse, with the production commands
- [FREE_MANAGER_ARCHITECTURE.md](FREE_MANAGER_ARCHITECTURE.md) — the Free Manager's scaling rules
- [TERRAFORM_ARCHITECTURE.md](TERRAFORM_ARCHITECTURE.md) — the ASG variables and `ignore_changes`
