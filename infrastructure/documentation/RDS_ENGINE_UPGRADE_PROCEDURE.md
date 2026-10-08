# RDS Engine Upgrade: MySQL Major Version Procedure

## Executive Summary

- **In-place MySQL major version upgrade** of the RDS instances in both environments, with `ModifyDBInstance`. Not a parallel instance: `RestoreDBInstanceFromDBSnapshot` has no engine version parameter, so a copy restores at `<FROM>` and still needs the same upgrade, frozen at its snapshot. The in-place upgrade keeps the warm storage volume.
- **The engine change is two lines of Terraform.** Everything else here exists because the surrounding configuration does not tolerate those two lines without preparation: **six constraints, B1 to B6**, properties of this stack rather than of any version pair.
- **The development scheduler's nightly destroy and restore cycle is the dominant constraint.** The scheduler's restore passes a parameter group but no engine version, so the parameter group swap and the engine upgrade must land in one apply, the instance must be `available` on `<TO>` before that day's scheduled stop, and the nightly cycle is itself a mandatory verification step.
- **The apply must modify the instance, never replace it.** A replacement is an empty database behind the same endpoint, and the loss is silent. This and every other gate is an asserted command output or a plan-JSON query, never a visual read: the failure mode is an upgrade that Terraform reports as successful and RDS has deferred.
- **Whether a terraform apply is also an application deploy depends on the branch lineage (B6).** On the development lineage every apply rebuilds the image and rolls every ECS service; on the production lineage the apply is the database alone.
- **Recovery points are created before the apply and closed deliberately afterwards.** A manual snapshot and a retained outgoing-family parameter group are prerequisites, both verified. **On an environment rebuilt on a schedule, automated retention is not one continuous window**: each cycle's instance carries its own backup — one `active` row for the current instance, one `retained` row per earlier cycle — so point-in-time recovery reaches any moment *inside* a running day and nothing overnight. The manual snapshot covers the gaps, and deleting it is what closes the rollback window.
- **Rollback is a snapshot restore.** There is no engine downgrade; the restore creates a new instance under the same identifier.

**Version placeholders used throughout:** `<FROM>` is the current major version, `<TO>` the target, and `<ENV>` the Terraform `var.environment` value. First applied for MySQL 8.0 to 8.4 (see [References](#references)).

---

## Architecture Overview

**Six phases, in order, and this table is the whole overview.**

| Phase | Target | Live impact | Code change | Reversal |
|-------------------|-----------------------|-----------------------|-----------------------|-----------------------|
| **0A** | Throwaway clone of development | None. Upgrade **and** rollback drill | No | Delete the clone |
| **0B** | Throwaway clone of production | **Creates a snapshot of production. Nothing else**, and only when its gate says run — **a skipped 0B means phase 0 performs no production operation at all** | No | Delete the clone and the snapshot |
| **0C** | Terraform state | None, read only. The plan gate, dry | No | Nothing to reverse |
| **1** | Development instance | Offline for the upgrade. **Weekdays only, started early enough to finish before the scheduled stop** — it must not span a night | Yes, Terraform | Snapshot restore |
| **2** | Development instance | None beyond the normal cycle. **One night is the floor, and the only floor in the procedure** | No | Snapshot restore |
| **3** | Development instance | Test and job traffic | **No** — the test assertion moved to its own issue | Snapshot restore |
| **4** | Production instance | **Offline for the upgrade. No ECS rollout on this lineage** (B6) | No, applies phase 1's code | Snapshot restore |
| **5** | Both | None | Yes, config removal | Revert the cleanup |

Two ordering constraints do not follow from the table: **phase 0's clones must be gone before phase 1**, or the
apply cannot destroy the outgoing parameter group; and **phase 4 needs a measured downtime (0B's or 0A's) and
phase 3's production baseline, which cannot be captured afterwards.**

---

## Implementation Details

### The six constraints

These are referenced as B1 to B6 from the tracking issues.

#### B1: The parameter group name is fixed, so create_before_destroy collides

**File:** `infrastructure/terraform/infrastructure.tf`

Both `family` and `name` are ForceNew on `aws_db_parameter_group`, so changing the family replaces the resource. With `create_before_destroy = true`, Terraform creates the replacement first, under the same name, and AWS rejects it with `DBParameterGroupAlreadyExists`.

**Fix:** use `name_prefix` in place of `name`, which is durable across future family changes. The `Name` tag is left as it was: nothing reads it, and changing it would add a diff for the gate to account for.

Consumers follow automatically because every reference derives from `aws_db_parameter_group.main.name`: the instance always, and the scheduler Lambda's `RDS_PARAMETER_GROUP_NAME` and the IAM policy's ARN only where `enable_dev_schedule` is set (phase 4 checks which case applies).

#### B2: apply_immediately is unset, so the upgrade is deferred and then lost

The provider default is `false`, so `ModifyDBInstance` records the new version in `PendingModifiedValues` and RDS performs the upgrade at the next maintenance window. **Terraform reports a successful update in place either way**, so nothing looks wrong.

On development the maintenance window falls at a time when the scheduler has already deleted the instance. The pending upgrade disappears with it, leaving Terraform state on `<TO>` and the instance on `<FROM>` permanently, while the parameter group has already moved - which then triggers B3 on the next restore.

**Fix:** set `apply_immediately = true` for the upgrade apply, and remove it with `allow_major_version_upgrade` afterwards (phase 5).

#### B3: An outgoing-version snapshot restored against the new parameter group family fails

**File:** `infrastructure/terraform/dev_scheduler_package/dev_scheduler.py`

`restore_rds()` passes `DBParameterGroupName` but no `EngineVersion`, so a restore always comes back at the snapshot's engine version, and an `<FROM>` snapshot with a `<TO>`-family group is rejected with `InvalidParameterCombination`. **Fix:** an ordering constraint, not a code change — the parameter group swap and the engine upgrade in the same `ModifyDBInstance`, `available` on `<TO>` before that day's scheduled stop, so that evening's snapshot is `<TO>`.

#### B4: Applying while the development instance is destroyed recreates it empty

If the scheduler has already deleted the instance, Terraform creates a fresh, empty one behind the same identifier and endpoint, and nobody notices until the next day. **Fix:** confirm `DBInstanceStatus` is `available` immediately before applying, and assert against the plan JSON that `aws_db_instance` carries no `create` or `delete` action.

#### B5: Do not declare engine_lifecycle_support

The AWS provider sends `EngineLifecycleSupport` only on the create and restore paths, so declaring it on a live instance produces a diff on every plan that no apply can clear. **Fix:** leave it out. After the upgrade the attribute still reads the Extended Support value, and that is correct: it is an attribute, not a charge.

#### B6: The apply is not an RDS-only change — on one lineage

Whether `null_resource.build_and_deploy` re-runs on an apply depends on its trigger map, which differs by
lineage. **Read it from the plan's `before` triggers (phase 0C), not from the checkout.**

| Lineage | Triggers | An apply from a new commit |
|-------------------------|-----------------------|-----------------------|
| **Development** (`develop-main`) | ALB DNS, ECR repository, `var.git_branch`, **`source_revision`** (the HEAD commit) | **Rebuilds the image and force-deploys every ECS service.** The window must cover the RDS downtime plus a full rollout |
| **Production** (the release lineage) | ALB DNS, ECR repository, `var.git_branch` — a branch name, while the release is a tag | **A no-op.** The apply is the database alone; the image push and the service cycle are separate manual steps |

On the development lineage, **what gets built is the local working tree**: `ecr_build_push.sh` uses the
local checkout as its build context, so apply from a branch cut from what is deployed, carrying only the
Terraform change. The script **refuses a dirty worktree**, and it runs *after* the RDS modification has been
issued. A `-target` apply is not a way out: it skips the scheduler Lambda whose parameter group name must
follow B1. **Fix:** confirm `git status --porcelain` is empty, and know what application change rides along:

```bash
git diff --stat <the deployed ref>..HEAD -- studio/app studio/__main_unit__.py frontend/src
# Expected: empty, or a diff you have deliberately decided to ship.
# Files outside these paths - tests, scripts, docs - do not reach the runtime image.
```

### The Terraform change

```diff
 resource "aws_db_parameter_group" "main" {
-  family = "mysql<FROM>"
-  name   = "${local.env_prefix}-ssl"
+  family      = "mysql<TO>"
+  name_prefix = "${local.env_prefix}-ssl-"
   ...
+  # Pinned to the values <FROM> was measured to be running, so the upgrade changes the
+  # engine version only. Neither family sets these, so a family diff does not show them.
+  # NOT upgrade scaffolding - these stay after the two instance attributes come out.
+  parameter { name = "innodb_dedicated_server"      value = "0" }
+  parameter { name = "innodb_buffer_pool_size"      value = "{DBInstanceClassMemory*3/4}" }
+  parameter { name = "innodb_buffer_pool_instances" value = "8" }
+  parameter { name = "innodb_redo_log_capacity"     value = "2147483648" }
+  parameter { name = "innodb_io_capacity"           value = "200" }
+  parameter { name = "innodb_io_capacity_max"       value = "2000" }
+  lifecycle { create_before_destroy = true }
 }

 resource "aws_db_instance" "main" {
-  engine_version                  = "<FROM>"
+  engine_version                  = "<TO>"
+  allow_major_version_upgrade     = true   # remove once both environments are upgraded
+  apply_immediately               = true   # remove once both environments are upgraded
 }
```

**The pinned parameters are the larger half of this change, and not optional.** Phase 0A's step 8 / step 9
pair measures what the outgoing version runs and what the incoming one defaults to; every value that differs
and is not wanted is pinned here. The values and the count are **version-specific** — six for 8.0 to 8.4
(see Edge Case Handling). A major-only `engine_version` resolves to the region default for that major. **Do not keep
`allow_major_version_upgrade` permanently**: without it a future `engine_version` edit cannot perform a major
upgrade silently.

### Resource identity after the upgrade

An in-place upgrade takes the instance offline, upgrades the engine on the same storage, and brings it back.

| Attribute | After upgrade | After a rollback restore |
|-------------------------|-----------------------|-----------------------|
| DB instance ARN | Unchanged | Unchanged - identifier-derived |
| Instance identifier | Unchanged | Unchanged, once the restore lands on it |
| Endpoint hostname | Unchanged | Unchanged - identifier-derived |
| `DbiResourceId` | Unchanged | **Changes** - assigned per instance creation |
| Parameter group name | **Changes** - name_prefix generates a new suffix | Set explicitly on the restore |
| `BackupRetentionPeriod` | Unchanged | Preserved in practice, but not a restore parameter |

A changed `DbiResourceId` matters in one place: **the RDS Proxy target must be re-registered.** The proxy deregisters its target when the instance is deleted and does not register a replacement on its own. On development `ensure_rds_proxy_target()` does this on the next scheduler start; on production it is a manual `register-db-proxy-targets`. Note that the proxy's `RdsResourceId` is the *instance identifier*, not `DbiResourceId`, so comparing the two proves nothing; the evidence is a target that exists and is `AVAILABLE`.

### Helpers

Every function this document calls is defined in `infrastructure/scripts/rds_upgrade_helpers.sh`. Source it
once per session, after the setup block under [Procedure](#procedure); the file defines functions only.

```bash
source infrastructure/scripts/rds_upgrade_helpers.sh
```

| Function | Does |
|-------------------------|-----------------------|
| `ssm_sh` | Runs the script on stdin on the environment's background instance over SSM. Neither RDS instance is publicly accessible and the proxy is TLS-only, so every in-database check goes through it |
| `need VAR...` | Aborts if a named variable is empty. Run it before anything that interpolates into an `ssm_sh` heredoc: an empty expansion surfaces as a remote error that reads as an SSM or secret fault |
| `assert_rehearsal ID` | Refuses any identifier without `rehearsal` in it |
| `drop_clone ID`, `drop_snapshot ID` | Delete behind `assert_rehearsal` and a typed confirmation of the target's real attributes. `drop_clone` keeps a final snapshot and the automated backups |
| `list_rehearsal_artefacts` | Every rehearsal-scoped instance, manual snapshot and parameter group |
| `sql_readings HOST` | The fixed set of version, `sql_mode`, authentication and InnoDB readings against `HOST`, over TLS with the application's credentials |
| `alembic_probe HOST MAJOR` | The application's own migration runner against `HOST`, aborting unless `HOST` reports version `MAJOR` |
| `run_background_jobs` | Every in-process background job once, each caught individually |
| `inv NAME PAYLOAD`, `ts_fmt`, `assert_prod`, `capture_baseline FILE` | Phase 3's helpers: a Lambda invoke with one verdict line (`SCHED` is the EventBridge envelope), CloudWatch timestamps to local time, and the production baseline capture, refused unless `PROD_DB` is still on `<FROM>` |
| `redact` | Replaces identifiers in a log before it is posted; the policy is the table under *Posting logs* |

**Never call `delete-db-instance` directly in this procedure.** The clone and the real instance differ by one
variable name in the same session - `CLONE` against `DB` - and in phase 0B that session is pointed at
production, so a mistyped identifier on a delete is the most damaging error available here; and
`--delete-automated-backups` defaults to true, so it takes the point-in-time window with it. Development has
no deletion protection (its scheduler deletes the instance nightly), so the guards are its only protection.
Do not put `rehearsal` in the name of anything you intend to keep.

#### Verifying the guards before use

With `ENV` set, all four checks must hold. **Never pass a real identifier to `drop_clone` or `drop_snapshot`
as a test**: if the predicate is broken, the wrapper goes on to describe the real resource and prompt, and a
live database is one keystroke away. The predicate is tested directly on real names, the wrappers only on
names that do not exist.

```bash
echo "${ENV:?set ENV before testing the guards}"
DB=${ENV}-optinist-cloud-rds

# 0. The helpers are shell state: source the file again in every new terminal.
declare -f drop_clone
# Expected: assert_rehearsal, confirm_target, --no-delete-automated-backups.

# 1. The accept set must be empty of real assets before starting
list_rehearsal_artefacts
# Expected: empty. Anything listed must be renamed or accounted for first.

# 2. The predicate refuses real identifiers - called directly, so no code path to a delete.
assert_rehearsal "$DB"
assert_rehearsal "$(aws rds describe-db-snapshots --db-instance-identifier "$DB" \
  --snapshot-type manual --query 'DBSnapshots[0].DBSnapshotIdentifier' --output text)"
# Expected: REFUSING twice, naming each identifier.

# 3. The wrappers consult the predicate. The strings resemble no real identifier.
drop_clone    "guard-test-instance-must-be-refused"
drop_snapshot "guard-test-snapshot-must-be-refused"
# Expected: REFUSING twice, no preview, no prompt, nothing sent to AWS. A preview here
# means the predicate is not wired into the wrapper - abort.

# 4. The accept path works, on the identifier 0A will really pass. What protects this call
#    is that the clone does not exist yet.
drop_clone "${ENV}-optinist-rds-upgrade-rehearsal"
# Expected: past assert_rehearsal, then confirm_target fails to describe a non-existent
# instance and returns before prompting. If the clone DOES exist (re-running partway
# through 0A) it reaches the prompt: answer with anything but the identifier.
```

#### Posting logs

Execution logs are posted to the tracking issues, which are public. Pipe them through `redact` rather than
masking by hand: editing a comment does not unpublish anything. The filter does not know about values it has
never seen, so check the result before posting. **This table is the single source for the substitution
policy**; the tracking issues reference it rather than restating it.

| In the output | Post as | Why |
|-------------------------|-----------------------|-----------------------|
| The environment prefix | `<ENV>` | The convention the repository's earlier execution logs use |
| The AWS account id | `<ACCOUNT_ID>` | Enables cross-account enumeration |
| The proxy endpoint's account token | `proxy-<TOKEN>` | A resolvable hostname |
| An **instance** endpoint's account token - the label between the identifier and the region | `<TOKEN>` | Same reason. Most steps never print an endpoint; step 10 does, because proving which database it reached is the point of it |
| Instance ids, `DbiResourceId` values, SSM association ids | `i-A`, `db-A`, `<ASSOC_A>` | Per-resource identifiers |
| Instance class, volume type and size; private DNS names and IPs; database usernames | Generalise | Capacity, subnet layout and configuration values |
| **Durations, engine versions, `sql_mode`, precheck findings, metric values, cost figures** | **Keep as measured** | Outcome measurements. Later phases and the acceptance criteria are written against them, so redacting these breaks the work's own definition of done |
Resource *name patterns* are fine with `<ENV>` substituted - they are names, not capacity, and access to them
is governed by IAM.

---

## Procedure

Set the environment once per session. `ENV` is the Terraform `var.environment` value.

```bash
export AWS_PROFILE=<the profile for this account>
export AWS_REGION=ap-northeast-1
export ENV=<the Terraform var.environment value>
export TF_ENV=<the backends/ and environments/ file basename>
export FROM=<the current major version, e.g. 8.0>
export TO=<the target major version, e.g. 8.4>
DB=${ENV}-optinist-cloud-rds
# zsh: without this a '#' in pasted input is not a comment - `VAR=x  # note` leaves VAR
# unset, silently, and a comment inside a pasted function's case is a parse error.
setopt interactive_comments 2>/dev/null || true
source infrastructure/scripts/rds_upgrade_helpers.sh
echo "profile=$AWS_PROFILE env=$ENV tf_env=$TF_ENV from=$FROM to=$TO db=$DB"
```

```bash
# The live parameter group's name, read rather than assumed: before the first upgrade it
# is <ENV>-optinist-ssl, after it name_prefix generates the name (B1), so a literal is
# correct exactly once. Re-read LIVE_PG after any apply that touches the group.
LIVE_PG=$(aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].DBParameterGroups[0].DBParameterGroupName' --output text)
aws rds describe-db-parameter-groups --db-parameter-group-name "$LIVE_PG" \
  --query 'DBParameterGroups[0].{name:DBParameterGroupName,family:DBParameterGroupFamily}'
# Expected: a name, and family mysql<FROM>. A mysql<TO> family means the apply has
# ALREADY run and this group is not a valid copy source for a rollback group - stop.
```

> **`ENV` names resources. `TF_ENV` names files. On production they differ.**
>
> | | `ENV` | `TF_ENV` |
> |---|---|---|
> | Development | `development` | `development` |
> | **Production** | **`subscr`** | **`production`** |
>
> The two coincide on development, so `backends/${ENV}.hcl` works there and hides the mistake; on production
> it names a file that does not exist and fails at the **first** terraform command of the window. `TF_ENV`
> is used **only** for the `backends/` and `environments/` paths; everywhere else, `ENV`.

`<FROM>`, `<TO>` and `<ENV>` remain in prose and in expected-output comments as placeholders; every *command*
uses `$FROM`, `$TO` and `$ENV`. The only angle-bracket placeholders left in commands are values only the
operator can supply: this block's, 0C's deployment checkout path, B6's deployed ref, criterion 6's `PROD_DB`
and `PROD_PROFILE` (deliberately not `DB`, which would silently repoint the capture), phase 5's other
environment, and the rollback's snapshot id. Anywhere else a placeholder is a defect in this document.

### Prerequisites

| Requirement | Why | If missing |
|-------------------------|-----------------------|-----------------------|
| AWS profile for the account | Every step | Nothing runs |
| `environments/<env>.tfvars` with real values | `terraform plan` in phases 0C, 1 and 4 | No plan, so no gate |
| `jq` | The plan-JSON gates, **and `ssm_sh`** - which runs in the day-before rehearsal, before any plan exists | The gate degrades to reading the plan by eye, which B4 exists to prevent; and `ssm_sh` fails while looking like an SSM problem |
| Clean toplevel worktree | The applies in phases 1 and 4 | The apply fails after the RDS modification is issued - B6 |
| SSM access to an in-VPC instance | The in-database checks | Those checks become manual |

Both tfvars files are gitignored and are not in a fresh checkout. Obtain them before starting.

### Establish the recovery floor before touching anything

The newest manual snapshot is what an accidental delete falls back to, and it is usually older than assumed.
**Run this for the environment the next phase operates on**, not both: a production API call does not belong
in phase 0.

```bash
# Automated backups: the point-in-time window, and whether there is one at all
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{retention:BackupRetentionPeriod,delProt:DeletionProtection}'
# Expected: retention greater than zero. Zero means no automated backups and no PITR.
# DeletionProtection: true on production, false on development.

aws rds describe-db-instance-automated-backups \
  --query "DBInstanceAutomatedBackups[?DBInstanceIdentifier=='${DB}'].{status:Status,
           from:RestoreWindow.EarliestTime,to:RestoreWindow.LatestTime}" --output table
# Expected: read the whole list - on a scheduled environment each earlier cycle is its own
# `retained` row (see the Executive Summary).

# Manual snapshots: these survive an instance delete, so they are the true floor
aws rds describe-db-snapshots --db-instance-identifier "$DB" --snapshot-type manual \
  --query 'reverse(sort_by(DBSnapshots,&SnapshotCreateTime))[].{id:DBSnapshotIdentifier,
           ev:EngineVersion,created:SnapshotCreateTime}' --output table
# Expected: read the newest date. That is the data an accidental delete costs once the
# automated backups are gone with it.
```

If the newest manual snapshot is old, take a fresh one before starting: phases 1 and 4 take theirs after the
rehearsal phases, and 0B runs a session pointed at production.

### Abort conditions

Stop immediately if any of these is true. Each has a command that detects it in the phase where it applies.

| Condition | Why it aborts |
|-------------------------|-----------------------|
| The plan shows `create`, `delete` or a replacement for `aws_db_instance.main`, or the instance is not `available` immediately before the apply | B4. Applying would leave an empty database behind the same endpoint |
| Development work will run past the scheduled stop | B3. The upgrade must not span a night |
| `PrePatchCompatibility.log` contains errors | The same failure occurs on the real instance |
| The manual pre-upgrade snapshot has not reached `available` | A snapshot still being created is not a recovery point |
| `git status --porcelain` is non-empty at the toplevel | B6. The apply fails after the RDS modification is issued |
| It is night, a weekend or a holiday and the target is development | The scheduler may have deleted the instance |
| The phase 0A rollback drill did not complete | The rollback has no proven recovery point |
| A rehearsal clone is still alive or still references the live parameter group | The apply cannot destroy the outgoing group while anything references it |
| The newest manual snapshot predates the work by more than the acceptable data loss | That snapshot is the floor if an instance is deleted with its automated backups |
| `drop_clone` does not refuse a real instance identifier when tested | The guard against the procedure's most damaging typo is not working |

### Phase 0A: rehearse the upgrade and the rollback on a clone of development

Nearly free: the nightly snapshot already exists, so this costs a few hours of one instance. Nothing live is touched and nothing enters Terraform state.

```bash
CLONE=${ENV}-optinist-rds-upgrade-rehearsal
# SNAP is the scheduler's nightly snapshot.
SNAP=${DB}-dev-scheduler
```

#### The upgrade — steps 1 to 11

Restore a clone, upgrade it, and measure what changed. **Only step 3 carries a deadline**: the scheduled stop
recreates the nightly snapshot under a fixed identifier and must delete the existing one first, so a restore
still reading it can make the stop fail. After step 3 nothing in 0A reads that snapshot again.

##### Step 1 — confirm the source snapshot

```bash
# Confirm the source snapshot is usable and on the outgoing version
aws rds describe-db-snapshots --db-snapshot-identifier "$SNAP" \
  --query 'DBSnapshots[0].{status:Status,ev:EngineVersion,created:SnapshotCreateTime}'
# Expected: available, <FROM>.x. On a Monday the snapshot is Friday's: record which day.
```

##### Step 2 — a private parameter group for the clone

```bash
# A private parameter group for the clone, copied from the live one.
#    Do not attach the live group: a clone on it blocks phase 1's destroy of it, and a
#    parameter experiment on the clone would land on the live instance.
aws rds copy-db-parameter-group \
  --source-db-parameter-group-identifier "$LIVE_PG" \
  --target-db-parameter-group-identifier rehearsal-ssl-from \
  --target-db-parameter-group-description "outgoing-family rehearsal copy"

#    The copy's output does not show the user-set parameters, so check them.
aws rds describe-db-parameters --db-parameter-group-name rehearsal-ssl-from --source user \
  --query 'Parameters[].{name:ParameterName,value:ParameterValue}' --output table
# Expected: the same user-set parameters as the live group. A copy that lost
# require_secure_transport makes step 8's "connection succeeded" meaningless.
```

##### Step 3 — restore the clone

```bash
# The restore parameters, read from the live instance (restore_rds() is the reference).
read -r CLASS STORAGE SUBNET SG <<<"$(aws rds describe-db-instances \
  --db-instance-identifier "$DB" --output text \
  --query 'DBInstances[0].[DBInstanceClass,StorageType,DBSubnetGroup.DBSubnetGroupName,
           VpcSecurityGroups[0].VpcSecurityGroupId]')"

aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier "$CLONE" --db-snapshot-identifier "$SNAP" \
  --db-instance-class "$CLASS" --storage-type "$STORAGE" --port 3306 \
  --db-subnet-group-name "$SUBNET" --vpc-security-group-ids "$SG" \
  --db-parameter-group-name rehearsal-ssl-from \
  --no-publicly-accessible --no-multi-az
aws rds wait db-instance-available --db-instance-identifier "$CLONE"
```

##### Step 4 — confirm what the clone came up as

```bash
# Neither of the two things that matter here is in the restore call's own response.
aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].{status:DBInstanceStatus,retention:BackupRetentionPeriod,
           rid:DbiResourceId,pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus}'
# Expected: available, retention greater than zero, and pg rehearsal-ssl-from / in-sync.
# Retention zero: no automatic pre-upgrade snapshot, so the drill cannot run. pg still
# the DEFAULT group once available: attach it with modify-db-instance before continuing.
# Note DbiResourceId: the drill asserts it changed after its restore.

# RDS copied the live instance's tags through the snapshot, so the clone claims to be the
# live instance (Name, ManagedBy=terraform) in cost reports and drift audits. Retag it.
aws rds add-tags-to-resource --resource-name "$(aws rds describe-db-instances \
  --db-instance-identifier "$CLONE" --query 'DBInstances[0].DBInstanceArn' --output text)" \
  --tags Key=Name,Value="$CLONE" Key=ManagedBy,Value=manual Key=Purpose,Value=rehearsal
```

##### Step 5 — resolve the target version and family, and build the target parameter group

```bash
# The target version and family, resolved as the provider resolves engine_version = "<TO>".
read -r TARGET TO_FAMILY <<<"$(aws rds describe-db-engine-versions --engine mysql \
  --engine-version "$TO" --default-only --output text \
  --query 'DBEngineVersions[0].[EngineVersion,DBParameterGroupFamily]')"
echo "target=$TARGET family=$TO_FAMILY"
# Expected: a concrete x.y.z and mysql<TO>. Empty means no default in this region - stop.

#    Confirm the resolved version is reachable from where the clone is now.
aws rds describe-db-engine-versions --engine mysql \
  --engine-version "$(aws rds describe-db-instances --db-instance-identifier "$CLONE" \
    --query 'DBInstances[0].EngineVersion' --output text)" \
  --query "DBEngineVersions[0].ValidUpgradeTarget[?EngineVersion=='${TARGET}'].{v:EngineVersion,
           major:IsMajorVersionUpgrade,auto:AutoUpgrade}" --output table
# Expected: one row, IsMajorVersionUpgrade true, AutoUpgrade false. Empty means the
# default is not reachable from here - pick from the full ValidUpgradeTarget list.
#    Record TARGET: phase 1 must land on the same version.

#    The target-family group, with the parameters read from the live group.
aws rds create-db-parameter-group --db-parameter-group-name rehearsal-ssl-to \
  --db-parameter-group-family "$TO_FAMILY" --description "target-family rehearsal"

#    JSON, not shorthand: a value may be an RDS expression such as
#    {DBInstanceClassMemory*3/4}, and the shorthand parser fails on the brace.
SRC_JSON=$(aws rds describe-db-parameters --db-parameter-group-name "$LIVE_PG" \
  --source user --query 'Parameters[].[ParameterName,ParameterValue,ApplyType]' --output text \
  | jq -Rn '[inputs | split("\t")
      | {ParameterName: .[0], ParameterValue: .[1],
         ApplyMethod: (if .[2] == "static" then "pending-reboot" else "immediate" end)}]')
printf '%s\n' "$SRC_JSON" | python3 -m json.tool
# Expected: one object per user-set parameter. Empty means the source group name is wrong.

aws rds modify-db-parameter-group --db-parameter-group-name rehearsal-ssl-to \
  --parameters "$SRC_JSON"

#    Read it back: the modify call reports only the group name.
aws rds describe-db-parameters --db-parameter-group-name rehearsal-ssl-to --source user \
  --query 'Parameters[].{name:ParameterName,value:ParameterValue,applyType:ApplyType}' --output table
# Expected: the live group's names and values. A missing one does not exist in the new family.
```

##### Step 6 — upgrade, and record the duration

```bash
# Family change and version change in ONE ModifyDBInstance: B3, rehearsed here.
date -u
aws rds modify-db-instance --db-instance-identifier "$CLONE" \
  --engine-version "$TARGET" --db-parameter-group-name rehearsal-ssl-to \
  --allow-major-version-upgrade --apply-immediately
aws rds wait db-instance-available --db-instance-identifier "$CLONE"
date -u
```

**Take the duration from the event stream, not the wall clock.** The `date -u` pair includes the waiter's
polling lag, and RDS reports `available` for a while after `--apply-immediately` before the status moves, so
the wall clock over-reads by several times. Phase 1 step 11 and phase 4 read the same three intervals:

```bash
aws rds describe-events --source-identifier "$CLONE" --source-type db-instance \
  --duration 120 --query 'Events[].{t:Date,msg:Message}' --output table
```

| From | To | What it is | Used for |
|-------------------------|-----------------------|-----------------------|-----------------------|
| `The downtime started` | `DB instance restarted` | **The outage users experience** | The number the production announcement quotes |
| `The downtime started` | `engine major version upgrade complete` | The same, upper bound | A safety margin on the above |
| `The pre-check started` | the last `Finished DB Instance backup` | The whole operation | The window the operator has to hold |

**A waiter timeout is not a failure.** `aws rds wait db-instance-available` gives up after 30 minutes and
exits non-zero while the upgrade carries on. If it errors, do not re-issue the modify: run the wait again
until the status settles on `available` with the new version, then read the event stream.

##### Step 7 — the precheck log

From the DB log file API, so the clone needs no log exports. **The log does not exist until the upgrade has
run**, so an empty result means the upgrade has not finished, not that the check was clean. **List before
fetching**: filtering on one guessed name silently produces an empty file, which reads like "no problems".

```bash
aws rds describe-db-log-files --db-instance-identifier "$CLONE" \
  --query 'DescribeDBLogFiles[].{name:LogFileName,size:Size,written:LastWritten}' --output table
# Expected: `mysqlUpgrade` or `PrePatchCompatibility.log` with a LastWritten from the
# upgrade just run. If neither is newer than the upgrade, wait and list again.

for f in $(aws rds describe-db-log-files --db-instance-identifier "$CLONE" --output text \
  --query "DescribeDBLogFiles[?contains(LogFileName,'Upgrade')
           || contains(LogFileName,'upgrade')
           || contains(LogFileName,'PrePatch')].LogFileName"); do
  echo "===== $f"
  aws rds download-db-log-file-portion --db-instance-identifier "$CLONE" \
    --log-file-name "$f" --starting-token 0 --output text
done | tee /tmp/prepatch.log
wc -l /tmp/prepatch.log
# Expected: non-zero. An empty file means the filter matched nothing - go back to the listing.

# The log ends with its own tally. Read that, not a grep count of "error".
grep -E '^(Errors|Warnings|Database Objects Affected):' /tmp/prepatch.log
awk -F': *' '/^Errors:/{print ($2==0 ? "PASS: Errors=0" : "ABORT: Errors=" $2)}' /tmp/prepatch.log
# Expected: PASS. A non-zero Errors count fails the same way live and aborts the plan.

# Warnings do not block, but every one gets read: they are the behaviour that changes.
grep -nE '^[0-9]+\)|^\tNo issues found' /tmp/prepatch.log
# Expected: every check accounted for; record what each finding means for this stack.
```

##### Step 8 — the in-database state

```bash
CLONE_HOST=$(aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].Endpoint.Address' --output text)
sql_readings "$CLONE_HOST"
```

`mysql_native_password` is a **server option, not a system variable** (`SELECT @@mysql_native_password` fails with `ERROR 1193`), so its configured value is read from the parameter group:

```bash
aws rds describe-db-parameters --db-parameter-group-name rehearsal-ssl-to \
  --query "Parameters[?ParameterName=='mysql_native_password'].[ParameterValue,ApplyType,IsModifiable]" \
  --output text
# Expected: ON  static  False - RDS pins it on. An empty result means the parameter does
# not exist in this family: see the auth row in the table below.
```

| Query | Expected | What it settles |
|-------------------------|-----------------------|-----------------------|
| `VERSION()` | `<TO>.x` | The upgrade reached the engine, not just `PendingModifiedValues` |
| `@@sql_mode` | Unchanged from `<FROM>` | Query acceptance behaviour does not change across the upgrade |
| `information_schema.PLUGINS` for `%native_password%` | A row, `ACTIVE` | The precheck warns the plugin is off by default in upstream `<TO>`. This says whether the running server still has it |
| **The connection itself** | Succeeded | The strongest evidence available here, and it costs nothing: if `mysql.user` shows the connecting account on `mysql_native_password`, then a successful login **is** proof the plugin authenticates on `<TO>`. Read the two rows together rather than either alone |
| The InnoDB values | **May change, and the RDS family diff does not predict it** | Buffer pool, redo log and IO capacity defaults move between majors (see Edge Case Handling), and RDS pins most of them in neither family, so only the running server shows the change |
| `@@binlog_format` | May change to `ROW` | Harmless with no replicas and no external consumers |
| `mysql.user` plugins | Existing users keep their plugin | The proxy's client auth type keeps working. The precheck flags these users as using a deprecated method - flagging is not breaking |

The connection succeeding at all is the `require_secure_transport` check: TLS against a group that requires it, on the new family.

##### Step 9 — the same readings on the live `<FROM>` instance

Only the pair says what **changed**, and the "before" half exists only while the live instance is on
`<FROM>`. Run it any time before phase 1.

```bash
LIVE_HOST=$(aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].Endpoint.Address' --output text)
sql_readings "$LIVE_HOST"
# Expected: VERSION() is <FROM>.x. If it is <TO>.x you are pointed at the clone.
```

Then read the **configured** side for both families, because `innodb_buffer_pool_size` is the one value RDS may pin rather than let the engine derive:

```bash
for G in "$LIVE_PG" rehearsal-ssl-to; do
  echo "== $G"
  aws rds describe-db-parameters --db-parameter-group-name "$G" \
    --query "Parameters[?ParameterName=='innodb_buffer_pool_size' ||
             ParameterName=='innodb_dedicated_server' ||
             ParameterName=='innodb_io_capacity'].[ParameterName,ParameterValue,Source]" \
    --output text
done
# A formula on one side and nothing on the other is the whole difference. Empty on both
# sides means the engine derives it, and the measured numbers are the only evidence.
```

Record the two sets side by side. **A difference in `innodb_buffer_pool_size` is the one that changes a decision** - see Edge Case Handling.

##### Step 10 — alembic

The only step that exercises the application's own stack - SQLAlchemy, pymysql, alembic - against the new
engine. On a clone already at head the migration is a **no-op**; what is proved is that the runner connects,
authenticates and negotiates TLS. `alembic_probe` proves the target first and aborts if it is not the clone,
because the deployed configuration points `MYSQL_SERVER` at the proxy in front of the **live** instance.

```bash
need CLONE_HOST TO
alembic_probe "$CLONE_HOST" "$TO"
# Expected, in order: MYSQL_SERVER is the clone's endpoint, VERSION() is <TO>.x, current
# and heads report the same revision, and current is unchanged afterwards.
#
# Failure modes:
#   "ABORT: DB_USER / DB_NAME are absent" - fix the mapping in the helper, not the credentials.
#   "1045 Access denied for user 'root'" - the DB_* to MYSQL_* mapping did not happen.
#   "ABORT: expected ... got <FROM>.x" - this reached the live instance through the proxy.
#     Nothing was migrated. Re-read CLONE_HOST.
#   current BEHIND heads - a migration really applied; investigate why a clone of a live
#     database was not at head before trusting the rest of 0A.
```

**Why this belongs in phase 0.** The phase 1 apply would find the same failure, since `cloud-startup.sh` runs
the migration on every start - but ECS's revert to the previous task definition **does not undo the engine
upgrade**, so recovery would be a snapshot rollback or a hotfix under time pressure. Here it costs one command
against a throwaway clone.

##### Step 11 — rehearse the parameter pins the apply will carry

Where the step 8 / step 9 pair shows a default that moved and is not wanted, the phase 1 parameter group pins
it back (see Edge Case Handling). **This step proves the pins produce the intended running state before the apply relies
on them** - that a formula evaluates on the new family, and that a `static` parameter reaches the running
server rather than sitting at `pending-reboot`, which looks exactly like a successful apply. Skip it only if
the pair showed nothing worth pinning, and **run it before the drill**, which restores the clone to `<FROM>`.

```bash
# The pins to rehearse, derived from the step 8 / step 9 pair: version-specific.
# JSON, not shorthand, for the reason step 5 gives.
PG=rehearsal-ssl-to
PINS_JSON='[
  {"ParameterName":"innodb_dedicated_server","ParameterValue":"0","ApplyMethod":"pending-reboot"},
  {"ParameterName":"innodb_buffer_pool_size","ParameterValue":"{DBInstanceClassMemory*3/4}","ApplyMethod":"pending-reboot"},
  {"ParameterName":"innodb_redo_log_capacity","ParameterValue":"2147483648","ApplyMethod":"pending-reboot"},
  {"ParameterName":"innodb_io_capacity","ParameterValue":"200","ApplyMethod":"pending-reboot"},
  {"ParameterName":"innodb_io_capacity_max","ParameterValue":"2000","ApplyMethod":"pending-reboot"}
]'
# 1. Prove the group is attached to nothing but the clone: most pins are dynamic and
#    would take effect on a live instance using the group.
aws rds describe-db-instances \
  --query "DBInstances[?DBParameterGroups[?DBParameterGroupName=='${PG}']].DBInstanceIdentifier" \
  --output text
# Expected: exactly the clone's identifier, and nothing else. Anything else aborts.

# 2. Apply the pins behind both guards. Every pin is pending-reboot, including the
#    dynamic ones, so one reboot applies them together.
assert_rehearsal "$PG" && assert_rehearsal "$CLONE" && \
  aws rds modify-db-parameter-group --db-parameter-group-name "$PG" --parameters "$PINS_JSON"

aws rds describe-db-parameters --db-parameter-group-name "$PG" --source user \
  --query 'Parameters[].[ParameterName,ParameterValue,ApplyMethod]' --output text
# Expected: the pins read back. One bad value fails the whole modify: if a pin is missing,
# stop - there is nothing waiting to be applied.

aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].DBParameterGroups[0].ParameterApplyStatus' --output text
# Expected: pending-reboot - the "before" half of the reboot check.

# 3. Reboot, which a static parameter needs. RDS can take minutes to begin the shutdown,
#    reporting available throughout, so a waiter returns at once on the pre-reboot state;
#    the event stream is the only deterministic signal, as in step 6.
SINCE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
assert_rehearsal "$CLONE" && \
  aws rds reboot-db-instance --db-instance-identifier "$CLONE" >/dev/null

# Poll for a restart newer than the call; --start-time keeps an earlier attempt's out.
for i in $(seq 1 60); do
  EV=$(aws rds describe-events --source-identifier "$CLONE" --source-type db-instance \
    --start-time "$SINCE" --query "Events[?contains(Message,'restarted')].Date" \
    --output text)
  [ -n "$EV" ] && { echo "restarted at $EV"; break; }
  sleep 10
done
aws rds wait db-instance-available --db-instance-identifier "$CLONE"
# Expected: "restarted at ..." within a few minutes. If the loop ends without printing,
# the reboot has not happened and the readings below would be the PRE-reboot <TO>
# defaults, which reads as the pins having failed.

aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].{status:DBInstanceStatus,pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus}'
# Expected: available, the rehearsal group, and pgs now in-sync rather than
# pending-reboot. incompatible-parameters means one of the pins is not viable.
```

Then `sql_readings "$CLONE_HOST"` again: the pinned variables should now report the `<FROM>` figures on a `<TO>` server.

A bad `static` parameter can leave the instance in `incompatible-parameters`. On a throwaway clone that costs nothing and does not block the drill, which restores from the pre-upgrade snapshot; record which pin caused it and drop it from the phase 1 group.

#### The rollback drill — steps 12 to 14

**This group is the reason 0A exists**: the only place the rollback is executed rather than described. It
rehearses the snapshot-restore runbook's steps 1, 3 and 5 (see [Rollback](#rollback)) — the restore source,
the restore parameters, and the delete-and-restore under the same identifier. The runbook's other steps
(traffic, deletion protection, the proxy target) and the Terraform revert have nothing to act on with a clone.

**0A can be split across days here.** The drill restores from the clone's *own* automatic pre-upgrade
snapshot, so the scheduled stop no longer constrains anything; a reasonable split is steps 1 to 10 on one
day and step 11, the drill and the teardown on the next. Nothing touches the clone overnight — the scheduler
acts on the one identifier in its `RDS_INSTANCE_ID`, not on tags — but it keeps billing.

If the terminal stayed open the shell still holds every variable and helper, but **credentials expire**, so
expect an `ExpiredToken` on the first call. In a new shell, re-run the session setup (which sources the
helpers) and then:

```bash
CLONE=${ENV}-optinist-rds-upgrade-rehearsal
echo "env=$ENV from=$FROM to=$TO db=$DB clone=$CLONE"; declare -f drop_clone >/dev/null && echo helpers-ok

# The drill's restore needs these; the values survive an overnight cycle.
read -r CLASS STORAGE SUBNET SG <<<"$(aws rds describe-db-instances \
  --db-instance-identifier "$DB" --output text \
  --query 'DBInstances[0].[DBInstanceClass,StorageType,DBSubnetGroup.DBSubnetGroupName,
           VpcSecurityGroups[0].VpcSecurityGroupId]')"

aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].{status:DBInstanceStatus,ev:EngineVersion,
           pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus,
           endpoint:Endpoint.Address}'
# Expected: available, <TO>.x, the target-family rehearsal group, in-sync.
```

Steps 8 to 11 need the in-VPC path, so wait for the environment's morning start before resuming them; the
drill and the teardown use only the RDS API.

##### Steps 12 to 14 — the rollback drill

**This runs the runbook's steps 1, 3 and 5 against the clone, not a second copy of them.** Substitute:

| The runbook uses | Here |
|-------------------------|-----------------------|
| `$DB` | **`$CLONE`** |
| `$RESTORE_PG` | **`rehearsal-ssl-from`** |
| A **manual** snapshot, with automated ones as the fallback | **An automated one.** The clone has no manual snapshot, so the selection below replaces step 1's listing |
| Clearing `deletion_protection` first | **Not needed.** The clone never had it |

Four things are specific to the drill:

```bash
# 1. SELECT the restore source. Daily backups match the version filter too.
aws rds describe-db-snapshots --db-instance-identifier "$CLONE" --snapshot-type automated \
  --query 'reverse(sort_by(DBSnapshots,&SnapshotCreateTime))[].[SnapshotCreateTime,
           EngineVersion,DBSnapshotIdentifier]' --output text
# Expected: the pre-upgrade snapshots on <FROM> ("preupgrade" in the identifier) plus any
# daily backup. Anything on <TO> is not a rollback point.

PRE=$(aws rds describe-db-snapshots --db-instance-identifier "$CLONE" \
  --snapshot-type automated \
  --query "reverse(sort_by(DBSnapshots[?starts_with(EngineVersion,'${FROM}')],
           &SnapshotCreateTime))[0].DBSnapshotIdentifier" --output text)
echo "$PRE"
# Expected: the newest <FROM> entry. "None" means RDS took none - check step 4's retention.

# Everything the restore needs, checked while the clone is still here to fall back on.
[ -n "$PRE" ] && [ "$PRE" != None ] || echo "ABORT: no ${FROM} snapshot to restore from"
aws rds describe-db-snapshots --db-snapshot-identifier "$PRE" \
  --query 'DBSnapshots[0].{status:Status,ev:EngineVersion,created:SnapshotCreateTime}'
# Expected: available, <FROM>.x. One still "creating" is not a restore source.
aws rds describe-db-parameter-groups --db-parameter-group-name rehearsal-ssl-from \
  --query 'DBParameterGroups[0].[DBParameterGroupName,DBParameterGroupFamily]' --output text
# Expected: the group, on the <FROM> family. Its absence is B3 waiting to happen.
echo "class=$CLASS storage=$STORAGE subnet=$SUBNET sg=$SG"
# Expected: all four non-empty.

# 2. GUARD both calls, chained with && (`|| return 1` is not a reliable stop at a shell's
#    top level). An unguarded restore on a mistyped identifier succeeds and creates an
#    instance nobody wanted.
# 3. TIME both halves. t1-t0 is the delete, t2-t1 the restore.
date -u                                                    # t0
assert_rehearsal "$CLONE" && \
  aws rds delete-db-instance --db-instance-identifier "$CLONE" \
    --final-db-snapshot-identifier "${CLONE}-broken" --no-delete-automated-backups
aws rds wait db-instance-deleted --db-instance-identifier "$CLONE"
date -u                                                    # t1

# 4. CONFIRM the restore source outlived the delete - the property the rollback depends on.
aws rds describe-db-snapshots --db-snapshot-identifier "$PRE" \
  --query 'DBSnapshots[0].{status:Status,ev:EngineVersion}'
# Expected: still available, still <FROM>.x. If it has gone, ${CLONE}-broken holds the
# <TO> state and nothing is lost.

assert_rehearsal "$CLONE" && \
  aws rds restore-db-instance-from-db-snapshot \
    --db-instance-identifier "$CLONE" --db-snapshot-identifier "$PRE" \
    --db-instance-class "$CLASS" --storage-type "$STORAGE" --port 3306 \
    --db-subnet-group-name "$SUBNET" --vpc-security-group-ids "$SG" \
    --db-parameter-group-name rehearsal-ssl-from \
    --no-publicly-accessible --no-multi-az
aws rds wait db-instance-available --db-instance-identifier "$CLONE"
date -u                                                    # t2

aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].{ev:EngineVersion,endpoint:Endpoint.Address,rid:DbiResourceId,
           pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus}'
# Expected: <FROM>.x; the endpoint identical to before the drill; DbiResourceId DIFFERENT
# (why a real rollback re-registers the proxy target); the outgoing-family group, in-sync.
# default.mysql<FROM> means the instance is not carrying require_secure_transport.
```

**Record t0, t1 and t2** — only the restore is unavoidable in a rollback decision. Wall clock is right here,
unlike step 6: nothing can use the instance until the restore completes.

**Metadata is not proof.** Re-read `CLONE_HOST` and run `sql_readings` once more: TLS against the
outgoing-family group, `VERSION()` at `<FROM>.x` from the engine, and the `<FROM>` baseline reproduced from a
snapshot. **This is the last opportunity — the teardown removes the instance.**

#### Teardown

As soon as the drill is finished, and in any case before phase 1: a clone still referencing the live
parameter group makes the phase 1 apply fail to destroy it with `InvalidDBParameterGroupState`.

```bash
drop_clone "$CLONE"

# drop_clone keeps the final snapshot and the automated backups on purpose. The final
# snapshot can still be creating when the delete completes, so wait before dropping it.
aws rds wait db-snapshot-available --db-snapshot-identifier "${CLONE}-final"
drop_snapshot "${CLONE}-final"
drop_snapshot "${CLONE}-broken"

# The retained automated backups: TWO sets, one per delete (the drill, then drop_clone),
# under the same identifier with different DbiResourceIds. This loop is the one
# destructive step with no per-item confirmation: pointed at the live instance it would
# delete the environment's entire retained backup history. Hence the guard, and listing
# before deleting.
assert_rehearsal "$CLONE" && \
  aws rds describe-db-instance-automated-backups \
    --query "DBInstanceAutomatedBackups[?DBInstanceIdentifier=='${CLONE}'].[DBInstanceAutomatedBackupsArn,
             DbiResourceId,Status]" --output text
# Expected: two rows, with this clone's two DbiResourceId values. Anything else: stop.

assert_rehearsal "$CLONE" && \
  for ARN in $(aws rds describe-db-instance-automated-backups \
    --query "DBInstanceAutomatedBackups[?DBInstanceIdentifier=='${CLONE}'].DBInstanceAutomatedBackupsArn" \
    --output text); do
    aws rds delete-db-instance-automated-backup --db-instance-automated-backups-arn "$ARN"
  done

aws rds delete-db-parameter-group --db-parameter-group-name rehearsal-ssl-from
aws rds delete-db-parameter-group --db-parameter-group-name rehearsal-ssl-to

# Expected: nothing rehearsal-shaped remains. list_rehearsal_artefacts covers three of
# the four kinds this phase created; the fourth:
aws rds describe-db-instance-automated-backups \
  --query "DBInstanceAutomatedBackups[?DBInstanceIdentifier=='${CLONE}'].[DBInstanceAutomatedBackupsArn,Status]" \
  --output text
```

### Phase 0B: rehearse on a clone of production - conditional

The only phase before phase 4 that touches production, and not always necessary. Skipping it keeps the whole
of phase 0 inside development. Two gates decide:

```bash
# Gate 1 - does production's schema differ from development's? The precheck is mostly
# schema-driven, and the schema is whatever alembic has applied. Once per environment,
# with ENV and PROXY pointed at each:
sql_run "$PROXY" <<< 'SELECT version_num FROM alembic_version;'
# Expected: the same revision in both. If they differ, 0A's precheck did not cover
# production's schema. Even when they match, the data-dependent table checks run over
# rows development does not have - which gate 2 prices.

# Gate 2 - how different are the data volumes? 0A's measured duration transfers unless
# production holds materially more data.
for E in "$ENV" <the other environment>; do
  echo "== $E"
  aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name FreeStorageSpace \
    --dimensions "Name=DBInstanceIdentifier,Value=${E}-optinist-cloud-rds" \
    --start-time "$(date -u -v-2H +%Y-%m-%dT%H:%M:%SZ)" \
    --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --period 300 --statistics Average \
    --query 'sort_by(Datapoints,&Timestamp)[-1].Average' --output text
done
# Expected: subtract each from AllocatedStorage to get used space. Comparable means 0A's
# duration plus a margin is the announcement; when last measured the two environments
# were within a few percent of each other.
```

| Gate 1 (alembic revisions) | Gate 2 (used data volume) | Decision |
|-------------------------|-----------------------|-----------------------|
| Same | Comparable | **Skip 0B.** Use 0A's duration plus a margin for the announcement |
| Same | Production materially larger | Run 0B for the timing. The precheck is already covered |
| **Differ** | Either | **Run 0B.** Production's schema was not tested by 0A |

**If 0B is skipped, the precheck runs for the first time in phase 4.** It fails safe - the upgrade does not
complete and the instance stays on `<FROM>`, so it costs the window rather than the data - but phase 4 needs a
stated response for that outcome.

**If 0B runs**: 0A steps 2 to 8 with `ENV` and the SSM target pointed at production, `rehearsal-ssl-from`
copied from production's group, and the drill not repeated. **Creating the source snapshot is the only
operation against production**; the exposure is the session, in which `DB` is production.

```bash
# Re-run the session setup with ENV set to production, then:
CLONE=${ENV}-optinist-rds-upgrade-rehearsal
SNAP=${ENV}-optinist-rehearsal-source-$(date +%Y%m%d-%H%M)
# Named with `rehearsal` in it so the teardown guard accepts it: $SNAP is a snapshot of
# PRODUCTION, and the newest manual snapshot is what an accidental instance delete
# falls back to.
aws rds create-db-snapshot --db-instance-identifier "$DB" --db-snapshot-identifier "$SNAP"
aws rds wait db-snapshot-available --db-snapshot-identifier "$SNAP"
aws rds describe-db-snapshots --db-snapshot-identifier "$SNAP" \
  --query 'DBSnapshots[0].{status:Status,ev:EngineVersion}'
# Expected: available, <FROM>.x
```

Then 0A's teardown - one retained backup set rather than two, and no `${CLONE}-broken` - plus
`drop_snapshot "$SNAP"`.

#### Whether 0B ran or not

```bash
# Confirm no rehearsal artefact survives into phase 1, in either case
list_rehearsal_artefacts
# Expected: empty
```

### Phase 0C: dry-run the plan gate

`terraform plan` is read-only, so **phase 1 steps 7 to 9 can run days ahead of the window**, with time to fix
what they find. Three conditions: the Terraform edit must be **committed in the tree being planned** (an
unmodified checkout produces "no changes" and the gate proves nothing); run it **from the directory Terraform
actually applies from**, which may be a separate deployment checkout; and **record every resource the plan
contains**, because the deploy trigger's `before` map is what settles B6 for this lineage, and a key present
in `before` and absent in `after` means the configuration is *older* than the state.

```bash
# After the session setup (profile, region, ENV, TF_ENV) in this terminal:
cd <the deployment checkout>/infrastructure/terraform

# Prove which tree is about to be planned: with two checkouts of one repository this is
# the check that matters most.
git rev-parse --abbrev-ref HEAD
git log -1 --oneline
git status --porcelain
grep -nE 'family|name_prefix|engine_version|allow_major_version_upgrade' infrastructure.tf
# Expected: the phase 1 branch, a commit carrying the edit, and the INCOMING family and
# engine version in the grep. An outgoing value means the wrong checkout.

echo "ENV=$ENV"
# Expected: the development environment's value. Stop if it is anything else: step 7's
# init attaches this directory to that environment's remote state.
```

Then phase 1 steps 7 to 9, and `rm -f "$PLAN"`. 0C cannot prove the replacement succeeds - a name collision
only surfaces at apply time.

### Phase 1: apply to development

Weekdays, while the environment is up, and finishing before the scheduled stop (B3, B4).

**Fourteen steps, cited by phase 4 as "phase 1 step N". Run them in one shell session**: `PRESNAP` (step 4),
`PLAN` (step 7) and `NEWPG` (step 12) are set in one step and read in a later one.

#### Before the apply — steps 1 to 6

Nothing here changes the database. **Steps 1 and 6 cannot be done afterwards.**

##### Step 1 — the `<FROM>` readings exist, and the pins are decided

```bash
# The pins must already be in the Terraform change; a pin absent here ships silently.
grep -oE 'name += +"innodb_[a-z_]+"' infrastructure/terraform/infrastructure.tf | sort
# Expected: one line per pin the 0A step 8 / step 9 pair justified - six for 8.0 -> 8.4:
# dedicated_server, buffer_pool_size, buffer_pool_instances, redo_log_capacity,
# io_capacity, io_capacity_max.

# The "before" half of the pair. On production there is no 0A, so it is taken here -
# after the apply the <FROM> instance no longer exists.
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{ev:EngineVersion,endpoint:Endpoint.Address}'
# Expected: <FROM>.x. Then sql_readings against that endpoint, unless the readings are
# already on file.
```

##### Step 2 — read the stop schedule, and extend it only if needed

```bash
# The deadline: an apply interrupted by the scheduled stop is B4.
aws events describe-rule --name "${ENV}-dev-schedule-stop" \
  --query '[Name,ScheduleExpression,State]' --output text
# Expected: the stop cron, and ENABLED. Convert it to local time: that is the deadline.
```

```bash
# The override is capped at twelve hours FROM THE MOMENT IT IS SET, so set it only when an
# overrun becomes likely, and late enough to clear the deadline.
aws lambda invoke --function-name "${ENV}-dev-scheduler" \
  --payload '{"action":"override","hours":12}' \
  --cli-binary-format raw-in-base64-out /dev/stdout
# Expected, in the FUNCTION's response rather than the invoke's own status:
#   {"statusCode": 200, "action": "override", "hours": N, "expires_at": "..."}
# A 400 with "OVERRIDE_PARAM_NAME not configured" is the silent failure. Compare
# expires_at against the stop time: the function cannot be queried for its override
# state, so expires_at is the evidence. The next start clears the override.
```

**Production has no scheduler, so phase 4 skips this step.**

##### Step 3 — the instance is available

```bash
# Anything but available aborts (B4).
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{status:DBInstanceStatus,ev:EngineVersion}'
# Expected: available, <FROM>.x
```

##### Step 4 — the manual pre-upgrade snapshot

A manual snapshot is the **only** recovery point that survives an instance delete.

```bash
# PRESNAP, not SNAP: SNAP elsewhere names a restore *source* that gets deleted, and two
# meanings in one session is how a recovery point gets deleted by a copied line.
# THE NAME IS A REQUIREMENT: the Rollback section names this snapshot as the restore
# source, and the shell that held $PRESNAP may be gone by then.
PRESNAP=${ENV}-optinist-pre-upgrade-$(date +%Y%m%d-%H%M)
aws rds create-db-snapshot --db-instance-identifier "$DB" --db-snapshot-identifier "$PRESNAP"
aws rds wait db-snapshot-available --db-snapshot-identifier "$PRESNAP"
aws rds describe-db-snapshots --db-snapshot-identifier "$PRESNAP" \
  --query 'DBSnapshots[0].{id:DBSnapshotIdentifier,status:Status,ev:EngineVersion,
           created:SnapshotCreateTime}'
# Expected: available, <FROM>.x. A snapshot still creating is not a recovery point.
```

##### Step 5 — retain an outgoing-family parameter group

The apply destroys the managed group, and the outgoing engine cannot use the new family. **Both verifications
are part of the step**: a copy that silently did not happen is otherwise discovered during the restore, after
it has been committed. The copy is not idempotent, so if it was made early, verify rather than re-run it.

```bash
aws rds copy-db-parameter-group \
  --source-db-parameter-group-identifier "$LIVE_PG" \
  --target-db-parameter-group-identifier "${ENV}-optinist-ssl-rollback" \
  --target-db-parameter-group-description "outgoing-family rollback target"

aws rds describe-db-parameter-groups \
  --db-parameter-group-name "${ENV}-optinist-ssl-rollback" \
  --query 'DBParameterGroups[0].{name:DBParameterGroupName,family:DBParameterGroupFamily}'
# Expected: the name, and family mysql<FROM>

aws rds describe-db-parameters --db-parameter-group-name "${ENV}-optinist-ssl-rollback" \
  --source user \
  --query '{found:length(Parameters),
            params:Parameters[].{name:ParameterName,value:ParameterValue}}'
# Expected: found == 2 - require_secure_transport = 1 and time_zone = UTC. `found` is in
# the query because a `Parameters[]` query over an empty copy prints nothing and exits 0,
# and a copy without require_secure_transport restores an instance that rejects the
# application's TLS-only connections. The same pattern guards every count in this document.
```

##### Step 6 — confirm the recovery points are real, before applying

The automatic pre-upgrade snapshots do not exist until the upgrade takes them, so verify their
**precondition**. The four recovery points this apply leaves behind:

| Recovery point | Created by | Expires | Restores to |
|-------------------------|-----------------------|-----------------------|-----------------------|
| `<ENV>-optinist-pre-upgrade-<date>` | **Step 4** | **Never** - manual snapshots persist until deleted | `<FROM>`, the state immediately before the apply. The primary recovery point: known contents, found by name, survives an instance delete |
| `<ENV>-optinist-ssl-rollback` | **Step 5** | Never | The parameter group that `<FROM>` instance needs |
| Automatic pre-upgrade snapshots | RDS, up to two, immediately before the upgrade | With the retention period | `<FROM>` |
| Point-in-time recovery | The automated backup | With the retention period | Any point inside a running day on development (see the Executive Summary); any point on production |

**A rollback after a night has passed must also revert the scheduler Lambda's parameter group**, or the next
nightly restore aims the new family at an old snapshot (B3 in reverse); the [Rollback](#rollback) section's
Terraform revert covers it.

```bash
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{retention:BackupRetentionPeriod,delProt:DeletionProtection,
           window:PreferredBackupWindow}'
# Expected: retention greater than zero. Zero means RDS takes no automatic pre-upgrade
# snapshot at all. Re-read it today: a restore can silently drop it.

aws rds describe-db-instance-automated-backups \
  --query "DBInstanceAutomatedBackups[?DBInstanceIdentifier=='${DB}'].{status:Status,
           rid:DbiResourceId,from:RestoreWindow.EarliestTime,to:RestoreWindow.LatestTime}" \
  --output table
# Expected: one `active` row for the instance running now, whose window runs to a few
# minutes ago - the row that covers "just before this apply".
```

#### The gate — steps 7 to 9

Steps 7 and 8 are mechanical. **Step 9 is judgement.**

##### Step 7 — clean worktree, then plan to a file outside the tree

```bash
git status --porcelain
# Expected: empty (B6)

# From the repository root: a failed `cd` leaves the shell where it was.
cd "$(git rev-parse --show-toplevel)/infrastructure/terraform"
pwd

# PHASE 4 ONLY: the backend is switched and PROVED at phase 4's own block; do not repeat
# the init here without the two proofs.
terraform init -backend-config="backends/${TF_ENV}.hcl" -reconfigure

# The plan file goes OUTSIDE the tree: it holds every variable value (mktemp gives it
# 0600), and on the development lineage an untracked file makes the tree dirty, which
# ecr_build_push.sh refuses AFTER the RDS modification has been issued.
PLAN=$(mktemp -t tfplan)
terraform plan -var-file="environments/${TF_ENV}.tfvars" -out="$PLAN"
```

##### Step 8 — the gate assertions

The gate passes on two resources - `aws_db_instance.main` showing `update` rather than a replacement, and
the parameter group showing one create and one delete. The plan will contain more, and every entry applies.

```bash
terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.type=="aws_db_instance")
  | "\(.address) -> \(.change.actions|join(","))"'
# Expected: aws_db_instance.main -> update. Any create or delete aborts the work.

terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.type=="aws_db_parameter_group")
  | "\(.address) -> \(.change.actions|join(","))"'
# Expected: one create and one delete - the create_before_destroy replacement

terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.type=="aws_db_parameter_group"
    and (.change.actions|index("create")))
  | {name: .change.after.name, name_prefix: .change.after.name_prefix,
     family: .change.after.family}'
# Expected: name null (known after apply), name_prefix set, family mysql<TO>. A literal
# name means B1 is unfixed and the apply will fail.

# The pinned parameters. A bare brace is literal in HCL; a mistyped ${...} is interpolation.
terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.type=="aws_db_parameter_group"
    and (.change.actions|index("create")))
  | .change.after.parameter[]? | "\(.name) = \(.value) [\(.apply_method)]"'
# Expected: one line per pin, values intact. Missing pins mean this plan is for a
# different change than phase 1 will apply.

# The deploy trigger, before and after: whether the apply rebuilds the image (B6).
terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.address=="null_resource.build_and_deploy")
  | {before: .change.before.triggers, after: .change.after.triggers}'
# Expected: the two maps differ in exactly the way the change explains. A key in before
# and absent in after means the configuration is OLDER than the state (0C).

# Every resource the plan touches, attributed: replace_paths names the attribute that
# forces each replacement.
terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.change.actions != ["no-op"])
  | "\(.address) -> \(.change.actions|join(","))  replace_paths=\(.change.replace_paths // [])"' | sort
# Expected from the engine change: the instance updating in place, the parameter group
# replaced on family and name_prefix, and whatever derives the group's name. Everything
# else is drift that rides along with the apply - triage it (step 9) before applying.
```

**Judge the instance by the SET of changed attributes, never by their count**: the action is `update`, and
**every** changed attribute is one the configuration intends - `engine_version`, and whichever of
`allow_major_version_upgrade`, `apply_immediately` and `deletion_protection` the state does not already
match. **An attribute outside that set stops the window; a set smaller than expected does not.**

##### Step 9 — classify the whole plan by root cause

**Before applying.** The extra entries are separate decisions riding along, and the phase log records which
bucket each falls in:

| Bucket | How to recognise it | What to do |
|-------------------------|-----------------------|-----------------------|
| The engine change | The instance, the parameter group, and whatever derives the group's name | Intended |
| The deploy (**B6**) | `null_resource` replacements whose `triggers` carry the git commit | Expected. Its rollout time belongs in the announced window |
| Runtime state versus configuration | A count or an instance the configuration declares statically while something scales it at runtime | Decide deliberately. The apply will overwrite the runtime state |
| Drift | An AMI data source with `most_recent`, a JSON body, a recomputed hash | Usually harmless, but read the ones that replace rather than update |

**A replacement is not an update.** Anything showing `delete,create` is down between the two; a NAT instance
in that state takes the private subnets with it and the in-VPC checks in steps 11 to 14 fail meanwhile:
**retry them once the apply settles**, and record the gap so a soak metric is not attributed to the engine.

#### The apply and what it must prove — steps 10 to 14

**Step 10 is the irreversible one.** Steps 11 to 14 are four separate claims.

##### Step 10 — apply

```bash
terraform apply "$PLAN"
# If the apply fails destroying the outgoing parameter group with
# InvalidDBParameterGroupState, re-run it: the instance is already on the new group and
# nothing is lost. If it persists, a clone or a manual instance still references the group.
```

##### Step 11 — the upgrade happened rather than being queued

```bash
# B2: an upgrade that was accepted but deferred leaves the instance serving <FROM> with
# the incoming version sitting in PendingModifiedValues.
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{ev:EngineVersion,status:DBInstanceStatus,
           pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus,pending:PendingModifiedValues}'
# Expected: <TO>.x, available, in-sync, pending == {}
```

**Measure this environment's own outage here, from the event stream** — the figure the next run quotes, and on
production what settles the `app_setup` SSM association's result (its database wait is five minutes).

```bash
# NOT the wall clock - see 0A step 6 for the three intervals to read.
aws rds describe-events --source-identifier "$DB" --source-type db-instance \
  --duration 120 --query 'Events[].{t:Date,msg:Message}' --output table
# --duration is in MINUTES: widen it if the apply started earlier.
```

##### Step 12 — the pins took effect

**A pin that did not apply looks exactly like a clean apply**: the group shows the value, the instance shows
`available`, and the engine runs the `<TO>` default.

```bash
# The group's name is generated by name_prefix (B1), so read it.
NEWPG=$(aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].DBParameterGroups[0].DBParameterGroupName' --output text)

# The configured side, and whether anything is still waiting for a reboot.
aws rds describe-db-parameters --db-parameter-group-name "$NEWPG" --source user \
  --query '{found:length(Parameters),
            params:Parameters[].[ParameterName,ParameterValue,ApplyType,ApplyMethod]}'
# Expected: found == 8 for this upgrade - require_secure_transport, time_zone, the six
# pins. Count rather than scan. A `static` pin not in effect below needs a reboot.

# The running side.
LIVE_HOST=$(aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].Endpoint.Address' --output text)
sql_readings "$LIVE_HOST"
# Expected: every pinned variable reports the <FROM> figure from the 0A pair.
```

Record three columns next to 0A's pair - `<FROM>` measured, `<TO>` default, `<TO>` pinned - which is what
makes the soak's metrics attributable.

##### Step 13 — the scheduler Lambda follows the renamed group

```bash
# B1: the consumer a plan does not obviously show.
aws lambda get-function-configuration --function-name "${ENV}-dev-scheduler" \
  --query 'Environment.Variables.RDS_PARAMETER_GROUP_NAME'
# Expected: the name_prefix-generated name, not the old literal
```

Production has no scheduler, so phase 4 skips this; the instance is the sole consumer there (B1).

##### Step 14 — the proxy target is healthy

```bash
aws rds describe-db-proxy-targets --db-proxy-name "${ENV}-optinist-rds-proxy" \
  --query '{found:length(Targets),
            targets:Targets[].{id:RdsResourceId,state:TargetHealth.State,
            reason:TargetHealth.Reason}}'
# Expected: found == 1, AVAILABLE across the apply. An in-place upgrade does not move
# DbiResourceId, so no re-registration should be needed; a restore does.
```

### Phase 2: the nightly destroy and restore cycle

This proves B3: **an incoming-version snapshot restored against the incoming-family group**, the path every
later restore of the upgraded environment takes. The cycle runs whether anyone watches it or not; the
retention period and the proxy re-registration have both failed on this stack. **Six checks in two sittings**: check 1 in the
evening after the stop, checks 2 to 6 after the morning restore (the stop *deletes* the instance, so the
morning group run in the evening returns `DBInstanceNotFound`).

#### The evening, after the stop — check 1

```bash
# 1. The evening snapshot must itself be on the new version
aws rds describe-db-snapshots --db-snapshot-identifier "${DB}-dev-scheduler" \
  --query 'DBSnapshots[0].{ev:EngineVersion,status:Status,created:SnapshotCreateTime}'
# Expected: <TO>.x, available, tonight's timestamp. Still <FROM> means the upgrade never
# completed - and this snapshot is what the morning restore consumes.
```

#### The morning, after the restore — checks 2 to 6

One block: `SINCE` is set in check 3 and read in checks 4 and 5b. **Check 6 is the engine's account of the
restore** where the others are the control plane's.

```bash
# 2. The morning restore
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{ev:EngineVersion,status:DBInstanceStatus,
           pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus,pending:PendingModifiedValues,
           retention:BackupRetentionPeriod}'
# Expected: <TO>.x, available, in-sync, pending == {}, retention unchanged.
# Retention is not a restore parameter, so it is the one property the chain can lose.

# 3. The restore raised no InvalidParameterCombination, the failure B3 predicts
SINCE=$(( ($(date +%s) - 12*3600) * 1000 ))
aws logs filter-log-events --log-group-name "/aws/lambda/${ENV}-dev-scheduler" \
  --start-time "$SINCE" --filter-pattern 'InvalidParameterCombination' \
  --query 'events[].message' --output text
# Expected: empty

# 4. Any other error from the restore path
aws logs filter-log-events --log-group-name "/aws/lambda/${ENV}-dev-scheduler" \
  --start-time "$SINCE" --filter-pattern '?"error:" ?Traceback ?"status\": \"error"' \
  --query 'events[].message' --output text
# Expected: empty

# 5. The proxy target exists: the proxy deregisters a deleted instance and does not
#    re-register one reappearing under the same identifier by itself.
aws rds describe-db-proxy-targets --db-proxy-name "${ENV}-optinist-rds-proxy" \
  --query 'Targets[].{id:RdsResourceId,state:TargetHealth.State,reason:TargetHealth.Reason}'
# Expected: one target, AVAILABLE

# 5b. The re-registration itself - the part that has failed on this stack before -
#     reported under "rds_proxy" in the scheduler's start results.
aws logs filter-log-events --log-group-name "/aws/lambda/${ENV}-dev-scheduler" \
  --start-time "$SINCE" --filter-pattern 'rds_proxy' \
  --query 'events[].message' --output text | tail -5
# Expected: "deferred_still_creating" on the first start pass, then "registered" on the
# verify-start. "already_registered" means no deregistration happened - worth
# understanding. "deferred_still_creating" on the VERIFY pass means the restore is stuck.

# 6. The application's own path: the readings through the proxy.
PROXY=$(aws rds describe-db-proxies --db-proxy-name "${ENV}-optinist-rds-proxy" \
  --query 'DBProxies[0].Endpoint' --output text)
sql_readings "$PROXY"
# Expected: the pinned values at their <FROM> figures. A failure here but not in check 2
# is a proxy auth problem, not an engine one.
```

**One cycle is the requirement; a second adds no coverage.** `restore_rds()` passes every restore parameter
explicitly — including the parameter group — so the snapshot id is the only input that varies between cycles
and the call reads nothing about the snapshot's provenance. The one property not passed,
`BackupRetentionPeriod`, is what check 2 measures on the restored instance, so the loop closes inside one
cycle. A second cycle is assurance for this environment's own nightly operation, not a gate.

### Phase 3: soak to exit criteria

**One night is the floor and the exit is a checklist, not a date.** Nothing this phase protects against is
found by elapsed time: every scheduled path has a cadence of 24 hours or less (see
[Configuration](#scheduled-jobs-that-touch-the-database)), so one nightly cycle covers all of them, an
application SQL incompatibility is found by exercising code paths, and development carries no load for a
performance regression to show on. **Finish the phase by running the criteria, not by letting time pass.**
A failure during the soak is an upgrade artefact only if it does not reproduce locally: the docker-compose
stacks pin the same major version, and the one divergence, `sql_mode`, is the same before and after.

| # | Exit criterion | Carried out by | Cost of skipping |
|---|-----------------------|-----------------------|-----------------------|
| 1 | Phase 2's six checks pass on **one** cycle, including the retention period | [Phase 2](#phase-2-the-nightly-destroy-and-restore-cycle). A second cycle is assurance, not coverage | Not compressible below one night, and not reducible |
| 2 | The deployed e2e lanes are green with `E2E_FAIL_ON_SKIP=1`, which also fails a run with zero executed tests | [Testing](#testing). The release set plus criterion 4 may substitute for the two expensive lanes, **conditional on criterion 4 running in full** | The only application-level evidence that the new version runs the application's real queries. **Do not skip** |
| 3 | The engine-version assertion is green | [Testing](#testing). Every assertion in it is also a manual step in phase 4, so it may follow phase 4 | Future regression cover only. Safe to defer |
| 4 | Every scheduled job has run once on the new version with no SQL error, by invoking it | Below | Paths that fail silently inside a job; and part of criterion 2 once the lanes are substituted. **Do not skip** |
| 5 | The log queries are clean | Below | Partly covered by 2 and 4. Can be shortened |
| 6 | Production baseline metrics captured | Below. **Cannot be done after phase 4** | The ability to answer "did the upgrade make it slower?", permanently. **Never skip** |

#### Criterion 4 — invoke every scheduled job

Each payload is the literal `input` the EventBridge target sends, so a manual invoke reproduces the scheduled
invocation. **This step writes and deletes**: the cleanup jobs remove data, and `ExpirationLifecycleJob`
performs a day's expiry processing at once. On a shared environment, announce it with the upgrade.

> **Do not invoke `<ENV>-dev-scheduler`.** It sits in the same naming scheme, but it **stops and restores the
> database** — invoking it deletes the instance you are soaking. The list below deliberately omits it.

`inv` requires a 200 **and** no `FunctionError`: a handler that raises still returns 200, and a Lambda that
never ran carries no `FunctionError` at all.

```bash
inv free-manager        "{$SCHED,\"detail\":{\"action\":\"monitor\"}}"
inv premium-manager     "{$SCHED,\"detail\":{\"action\":\"monitor\"}}"
inv premium-cleanup     "{$SCHED,\"detail\":{\"action\":\"cleanup\"}}"
inv common-user-manager "{$SCHED,\"detail\":{\"action\":\"manage_users\"}}"
inv cost-tracker   '{}'
inv public-cleanup '{}'
# Expected: ok on every line.
```

**`free_cleanup` is reached by none of this.** It holds about fifteen raw statements, has no EventBridge schedule and no visible invoker. Record it as an uncovered path rather than invoking it blind: it deletes, and firing a deletion Lambda whose trigger and payload are not established is a worse risk than the gap.

The in-process background jobs have no Lambda, so call them directly:

```bash
run_background_jobs
# Expected: all three markers - `container: ecs-<ENV>-background-...`, FIVE `== <JobName>`
# lines, and `FAILED: none` last. Anything less means the block did not run, not that it
# passed. A correct run takes minutes, not seconds.
```

#### Criterion 5 — the log queries

**Choose the window from the apply, not the clock.** On an environment with a nightly stop a 24-hour window
always contains a band of `9501 Timed-out waiting to acquire database connection` from the Lambdas running
while the instance is deleted; that band predates the upgrade. Production has no nightly stop, so **a `9501`
in production's window is a finding**. And **filter on exception class names, not numeric error codes**:
CloudWatch matches a quoted string as a substring, so `1290` matches `clientConnection=1290121671`.

```bash
SINCE=$(( ($(date +%s) - 24*3600) * 1000 ))
# For "is this still happening?", the moment the rollout finished rather than 24 hours ago:
AFTER=$(( $(date -j -f '%Y-%m-%d %H:%M:%S' '<YYYY-MM-DD HH:MM:SS>' +%s) * 1000 ))
echo "AFTER=$AFTER  ($(date -r $((AFTER/1000)) '+%F %T'))"

aws logs describe-log-streams \
  --log-group-name "/aws/rds/instance/${DB}/error" \
  --order-by LastEventTime --descending --max-items 3 \
  --query 'logStreams[].[lastEventTimestamp,logStreamName]' --output text | ts_fmt
# Expected: a lastEventTimestamp AFTER the apply. Here silence is the failure: the export
# did not survive the family change.

aws logs filter-log-events --log-group-name "/aws/rds/proxy/${ENV}-optinist-rds-proxy" \
  --start-time "$SINCE" \
  --filter-pattern '?"Access denied" ?"Authentication failed" ?ConnectionRefusedError ?"error 1290"' \
  --query 'events[].message' --output text
# Expected: empty

for LG in "/ecs/${ENV}-optinist-cloud-taskdef" \
          "/ecs/${ENV}-background-optinist-cloud-taskdef" \
          "/ecs/${ENV}-premium-optinist-cloud-taskdef" \
          "/ecs/${ENV}-public-optinist-cloud-taskdef"; do
  echo "== $LG"
  aws logs filter-log-events --log-group-name "$LG" --start-time "$SINCE" \
    --filter-pattern '?OperationalError ?ProgrammingError ?InternalError ?IntegrityError' \
    --query 'events[].message' --output text | head -c 1500
  echo
done
# Expected: empty
```

**The Lambda handlers need their own query.** A handler can catch a SQL exception, log it, and still return
200 with a success-shaped body, so criterion 4's verdict does not cover this:

```bash
for L in free-manager premium-manager premium-cleanup common-user-manager cost-tracker \
         public-cleanup free-cleanup; do
  N=$(aws logs filter-log-events --log-group-name "/aws/lambda/${ENV}-$L" \
        --start-time "$AFTER" \
        --filter-pattern '?"9501" ?OperationalError ?ProgrammingError ?InternalError ?Traceback' \
        --query 'length(events)' --output text)
  echo "$L: $N"
done
# Expected: every number zero (several zeros per Lambda is normal: one per page). Count
# rather than print: `head` shows the OLDEST matches and silently truncates.
```

**The startup leader election needs its own query.** It is wrapped in a `try/except` that logs and returns,
so a service at `running == desired` does not prove its `GET_LOCK` succeeded.

```bash
aws logs filter-log-events --log-group-name "/ecs/${ENV}-public-optinist-cloud-taskdef" \
  --start-time "$SINCE" \
  --filter-pattern '?"Startup sync" ?"Startup sync error"' \
  --query 'events[].message' --output text | tail -20
# Expected: no `Startup sync error`, and at least one line showing the election resolved.
# An EMPTY result is not a pass: the lock was never taken and this check says nothing.
```

```bash
aws logs filter-log-events --log-group-name "/ecs/${ENV}-background-optinist-cloud-taskdef" \
  --start-time "$SINCE" \
  --filter-pattern '?storage_tracking ?premium_expiration ?StorageReconciliation ?DataCleanup ?ExpirationLifecycle' \
  --query 'events[].[timestamp,message]' --output text | ts_fmt | tail -30
# Expected: NOT empty, with entries after the apply: the container's own scheduler running
# these unattended, separate evidence from criterion 4. Read what each job did -
# ExpirationLifecycleJob logging "No remote bucket configured" counts as invoked, not
# exercised.
```

#### Criterion 6 — the production baseline

Capture it for **production** before phase 4; it cannot be retaken afterwards. Every command is read-only; the
risk is reading the *wrong thing* from a shell whose every variable points at development — so **use
`PROD_DB` and `PROD_PROFILE`, and never reassign `$DB` or `export AWS_PROFILE`**, which would hand a later
`terraform` command a production context. The capture goes under `$HOME`, not `mktemp`: it has to survive
until phase 5's comparison weeks later.

```bash
# The only hand-substitution point in this step. PROD_* deliberately.
PROD_DB=<production db instance identifier>
PROD_PROFILE=<production profile name>
OUT=$HOME/baseline-production-$(date +%Y%m%d-%H%M).txt
```

```bash
# Confirm the identity before capturing. A development identifier here stops the step.
aws rds describe-db-instances --profile "$PROD_PROFILE" \
  --db-instance-identifier "$PROD_DB" \
  --query 'DBInstances[0].{id:DBInstanceIdentifier,ev:EngineVersion,
           status:DBInstanceStatus,pg:DBParameterGroups[0].DBParameterGroupName}'
# Expected: production's identifier, <FROM>.x, available.

# Behind assert_prod, so a failed gate creates no file at all.
capture_baseline "$OUT"

# The capture is only captured once it is on disk. An empty file is not a reading.
wc -l "$OUT" && grep -c "^== " "$OUT"
# Expected: eight headings, and 14 daily points under each.
```

**`BufferCacheHitRatio` returns no datapoints on standard RDS MySQL** (an Aurora metric), so its section is
empty as expected; `ReadIOPS`, `ReadLatency` and `FreeableMemory` carry the buffer-pool question (see
[Metrics to compare](#metrics-to-compare-against-the-baseline)). When posting, **keep the metric values**.

### Phase 4: apply to production

No scheduler, so the timing is free, but the instance is offline for the whole upgrade.

**On this lineage the apply is the database change alone (B6)**, so **announce the measured upgrade duration
plus a margin** — 0B's figure if it ran, otherwise 0A's — and re-read `var.git_branch` if the release process
changes. **The release is two actions**: the apply, then a manual image push and ECS service cycle. Neither
this document nor the release procedure states the combined order, so write it into the window's tracking
issue beforehand, adding a same-morning `<FROM>` health-lane baseline before the plan and a re-read of the ECS
and proxy checks after the service cycle.

**If 0B was skipped, the precheck runs here for the first time.** It fails safe — the instance stays on
`<FROM>` and the cost is the window — and the response is decided in advance: capture the log, attempt no
in-window fix, finish the rest of the release, and take a second window within days.

**Run phase 1 steps 1 to 14 with `ENV` set to the production value**, with these differences:

| | On production |
|-------------------------|-----------------------|
| **`TF_ENV`, not `ENV`, names the backend and tfvars files** | `$ENV` is `subscr`; the files are `backends/production.hcl` and `environments/production.tfvars`. The two coincide on development, which is why the mistake is invisible there (see [Procedure](#procedure)) |
| **Step 1** — the `<FROM>` readings and the pins | **The only place the "before" half can be taken**: production has no phase 0A, and after the apply the `<FROM>` instance is gone. It changes nothing, so complete it before the window |
| **Step 2** — the stop schedule and the override | **Skipped.** No scheduler, no deadline |
| **Step 13** — the scheduler Lambda's parameter group | **Skipped.** Where the scheduler is absent `aws_db_instance.main` is the sole consumer of the generated name (B1), and the apply covers it |
| **Step 7's `terraform init`** | **Not repeated.** The backend is switched and proved in this phase's own block below |

**Check the scheduler's absence rather than assuming it**: `dev_schedule.tf` is gated by
`var.enable_dev_schedule`, which nothing in the repository settles.

```bash
grep -n enable_dev_schedule "environments/${TF_ENV}.tfvars"
# Expected: no match, or "= false" - either means the scheduler is absent and steps 2 and
# 13 are correctly skipped. "= true" means they must be RUN.
```

#### The day before the window — rehearse every read-only check

Run every read-only check against production the day before, and spend the window on the three things that
change something: the snapshot, the parameter-group copy and the apply. **A rehearsal proves the command,
not the state**, so a rehearsed check is still run inside the window.

| Rehearse | What the rehearsal settles |
|-------------------------|-----------------------|
| Phase 1 steps 1, 3 and 6; the ECS, alarm and proxy checks below | Every resource name resolves, the queries return the shape the assertions expect, and `ssm_sh` reaches production |
| **The health lane** | A complete `e2e/.env.prod`, the case count, and a green baseline on `<FROM>` — so a failure afterwards cannot be mistaken for one the upgrade caused |

```bash
# The application's own path: the proxy endpoint, from inside the VPC.
PROXY=$(aws rds describe-db-proxies --db-proxy-name "${ENV}-optinist-rds-proxy" \
  --query 'DBProxies[0].Endpoint' --output text)
[ -n "$PROXY" ] && [ "$PROXY" != None ] || { echo "ABORT: proxy endpoint not resolved" >&2; }
sql_readings "$PROXY"
# Expected before the window: <FROM>.x. After the apply: <TO>.x. A failure here while the
# instance is available is a proxy problem - production gets no automatic re-registration.
```

**The health lane reads its target from `frontend/e2e/.env.prod`** (keys in `.env.prod.example`); the file's
values override the shell, so hand-exported variables are silently ignored.

```bash
( cd frontend && ls -l e2e/.env.prod && \
  for k in BASE_URL API_URL HEALTH_ENV RDS_PROXY_HOST RDS_SECRET_ID \
           RDS_SSM_INSTANCE_NAME STRIPE_SECRET_ENV TEST_USER_EMAIL TEST_USER_PASSWORD; do
    grep -qE "^${k}=." e2e/.env.prod && echo "ok   $k" || echo "MISSING $k"
  done )
# Expected: ok on every line. RDS_SSM_INSTANCE_NAME is ${ENV}-optinist-background and
# HEALTH_ENV is the var.environment value.

# In a SUBSHELL, so the working directory is not left changed.
( cd frontend && E2E_TARGET=prod yarn test:e2e e2e/17-aws-health.spec.ts --retries 0 \
    --grep-invert "@slow|@disruptive|HEALTH-25|HEALTH-26" )
```

Rules for that invocation:

- **`HEALTH_ENV` is the variable, not `TARGET_ENV`**, which is a TypeScript constant: set it and the lane
  grades **development's** compute layer while pointing SQL at production.
- **Off development the lane requires `RDS_PROXY_HOST`, `RDS_SSM_INSTANCE_NAME`, `RDS_SECRET_ID` and
  `STRIPE_SECRET_ENV`** (a missing one fails all 29 cases) and `BASE_URL` (unset, it **skips** all 29).
- **`--retries 0`**: the default retry doubles the load on production. **Not `--headed`**: nothing here drives
  a browser.
- **`--grep-invert` replaces the config's own `grepInvert`**, so `@slow|@disruptive` must be repeated. It
  deselects the two cases that are not read-only, and **the same set must be deselected after the apply**:

  | Deselected | Why | What covers it instead |
  |-------------------------|-----------------------|-----------------------|
  | `HEALTH-25` | POSTs a registration to production (an existing address, refused with 400 — but an application bug that accepted it would write first) | The ALB route it uniquely covers cannot change in an engine upgrade; the account's row state is covered by the free-account and instance-assignment cases |
  | `HEALTH-26` | 20 concurrent requests at the public tier — the lane's only timing-dependent assertion, so the likeliest to fail for a reason that is not the upgrade | The serial public-tier case and the proxy-path query; and the release's manual service cycle restarts all four tiers against the new engine |

- **`E2E_FAIL_ON_SKIP=1` is deliberately absent**: on production `HEALTH-27` skips whenever the ALB alarm has
  no retained ALARM transition, and `HEALTH-29` with no unpublished experiment. **Read the reporter's skip
  list and account for each entry** instead of trusting the exit code.

**Measured on production on 8.0, as the before half of the pair: `26 executed, 1 skipped, 27 mapped`** (read
off the skip-summary reporter's `N executed` line), in about two minutes — budget that in the window. The one
skip was `HEALTH-27`; `HEALTH-15` passed but printed `error rate not decided`, so a failure there afterwards
is a real signal while a pass proves little. **Anything other than that shape afterwards is a change.** The
lane writes nothing, but `HEALTH-25`, `HEALTH-26` and the login are real traffic. **Record the results.**

**Then phase 1 steps 4 and 5 against production**, from the reference, not from a copy — a restated command
is a command that drifts, and step 5's two verifications are the half most easily dropped.

**Then the backend switch, which is production-only and has no phase 1 counterpart:**

```bash
# Forgetting this applies this environment's plan to the other environment's state - a
# deployment clone has been found pointed at the OTHER environment's state bucket.
cd "$(git rev-parse --show-toplevel)/infrastructure/terraform"
pwd
terraform init -backend-config="backends/${TF_ENV}.hcl" -reconfigure
terraform workspace show

# Proof 1 - the backend config itself. A local file read, no AWS call.
python3 -c "
import json; b=json.load(open('.terraform/terraform.tfstate')).get('backend',{})
print('bucket:', b.get('config',{}).get('bucket'))
"
cat "backends/${TF_ENV}.hcl"
# Expected: the bucket printed above is the bucket in the .hcl file.

# Proof 2 - an attribute that names the environment.
terraform state show aws_db_instance.main | grep -E '^[[:space:]]+(identifier|engine_version)'
# Expected: identifier is $DB, and engine_version is <FROM>. `terraform state list` is NOT
# a proof: the address is identical in both environments.
```

Then **phase 1 steps 3 to 12** in order, and after the apply **phase 1 step 14** plus:

```bash
# Every service rolled cleanly - all four. `found` and `failures` are in the query because
# a name that does not resolve goes to `failures`, which a `services[]` query discards.
aws ecs describe-services --cluster "${ENV}-optinist-cloud-cluster" \
  --services "${ENV}-optinist-cloud-service" "${ENV}-premium-optinist-cloud-service" \
             "${ENV}-background-optinist-cloud-service" "${ENV}-public-optinist-cloud-service" \
  --query '{found:length(services),failures:failures,
            services:services[].{name:serviceName,status:status,desired:desiredCount,
            running:runningCount,rollout:deployments[0].rolloutState}}'
# Expected: found == 4, failures == [], each row ACTIVE, COMPLETED, running == desired.
# Desired is NOT uniform: on production, free, premium and background run 1, public 2.

# `found` again: describe-alarms answers an unknown name with an empty list and exit 0.
aws cloudwatch describe-alarms \
  --alarm-names "${ENV}-optinist-rds-cpu-high" "${ENV}-optinist-rds-connections-high" \
                "${ENV}-optinist-rds-storage-low" \
  --query '{found:length(MetricAlarms),
            alarms:MetricAlarms[].{name:AlarmName,state:StateValue,
            updated:StateUpdatedTimestamp}}'
# Expected: found == 3, OK for all three. INSUFFICIENT_DATA immediately after the
# upgrade resolves within a couple of evaluation periods.
```

A service stuck short of its desired count while the instance is healthy is a connection pool holding a dead connection: force a new deployment on that service. A rollout problem, not a rollback trigger.

### Phase 5: verify and clean up

```bash
for E in development subscr; do
  aws rds describe-db-instances --db-instance-identifier "${E}-optinist-cloud-rds" \
    --query 'DBInstances[0].{id:DBInstanceIdentifier,ev:EngineVersion,
             endpoint:Endpoint.Address,status:DBInstanceStatus,
             pg:DBParameterGroups[0].DBParameterGroupName,
             pgs:DBParameterGroups[0].ParameterApplyStatus,els:EngineLifecycleSupport}'
done
# Expected: <TO>.x, available, in-sync, and the original identifier and endpoint.
# EngineLifecycleSupport still naming Extended Support is correct - it is an
# attribute, not a charge (B5).

# Extended Support charge gone. GROUPED by usage type, not filtered to one: a filter that
# matches nothing returns zero for every day, which reads like the charge having stopped.
aws ce get-cost-and-usage --granularity DAILY \
  --time-period "Start=$(date -u -v-7d +%Y-%m-%d),End=$(date -u +%Y-%m-%d)" \
  --metrics UnblendedCost \
  --filter '{"Dimensions":{"Key":"SERVICE","Values":["Amazon Relational Database Service"]}}' \
  --group-by Type=DIMENSION,Key=USAGE_TYPE \
  --query 'ResultsByTime[].{day:TimePeriod.Start,
           types:Groups[].[Keys[0],Metrics.UnblendedCost.Amount]}'
# Expected: the list is NOT EMPTY on any day (empty means the query is broken), and a usage
# type containing `ExtendedSupport` appears before the apply and is absent after it. The
# apply's own day is PARTIAL and Cost Explorer lags: the confirming read is two days later.

# Inventory what is temporary. This lists rather than deletes on purpose.
aws rds describe-db-snapshots --snapshot-type manual \
  --query 'DBSnapshots[?contains(DBSnapshotIdentifier,`pre-upgrade`)
           || contains(DBSnapshotIdentifier,`rehearsal`)].DBSnapshotIdentifier' --output text
aws rds describe-db-parameter-groups \
  --query 'DBParameterGroups[?contains(DBParameterGroupName,`rehearsal`)
           || contains(DBParameterGroupName,`rollback`)].DBParameterGroupName' --output text

# The scheduler's own snapshot must survive. Never delete it.
aws rds describe-db-snapshots --snapshot-type manual \
  --query 'DBSnapshots[?contains(DBSnapshotIdentifier,`dev-scheduler`)].DBSnapshotIdentifier' \
  --output text
# Expected: still present
```

**Deleting the pre-upgrade snapshots and the retained rollback parameter groups closes the rollback window**:
do it against the retention duration agreed before phase 1 — see
[How long the rollback window stays open](#how-long-the-rollback-window-stays-open).

#### The cleanup PR

Three changes, which travel together: the per-resource assertion below gives the same evidence within one PR.

| Change | File | Plan effect |
|-------------------------|-----------------------|-----------------------|
| Remove `allow_major_version_upgrade` and `apply_immediately` | `infrastructure/terraform/infrastructure.tf` | **An in-place `update` on `aws_db_instance.main`, carrying exactly those two attributes.** They are write-only against the API but recorded in state |
| Update the MySQL client package, if it has fallen behind | `infrastructure/scripts/app_setup.sh` | Updates `aws_s3_object.app_setup_script`, whose `etag = filemd5(...)` tracks the script |
| Add the snapshot-restore runbook | `infrastructure/documentation/` | None |

**Do not assert an empty plan. Assert per resource**, as the phase 1 and phase 4 gates do, because
`null_resource.build_and_deploy` differs by lineage (B6).

```bash
cd "$(git rev-parse --show-toplevel)/infrastructure/terraform"
PLAN=$(mktemp -t tfplan-cleanup)
terraform plan -var-file="environments/${TF_ENV}.tfvars" -out="$PLAN"
terraform show -json "$PLAN" | jq -r '
  .resource_changes[]
  | select(.change.actions != ["no-op"])
  | "\(.address) -> \(.change.actions|join(","))"'
# Expected, and nothing else:
#   null_resource.build_and_deploy  -> delete,create on DEVELOPMENT; absent on PRODUCTION
#                                      (B6) - appearing there means this apply is a rollout
#   aws_s3_object.app_setup_script  -> update (only if app_setup.sh changed)
#   aws_db_instance.main            -> update, with exactly two changed attributes:
#                                      allow_major_version_upgrade true -> null and
#                                      apply_immediately true -> false. A third stops the work.
# The guard is restored by the CONFIGURATION, not by this apply, so any later apply
# absorbs this diff and none needs scheduling for it alone.
```

---

## Rollback

There is no engine downgrade: returning to the previous version means restoring a snapshot taken before it.
That works because **the engine version is a property of the snapshot** — `RestoreDBInstanceFromDBSnapshot`
has no engine version parameter, the same API fact that causes B3. **The restore itself is the snapshot-restore
runbook in [MAINTENANCE_PROCEDURES.md](MAINTENANCE_PROCEDURES.md#emergency-restore-the-database-from-a-snapshot)**;
this section adds what an upgrade rollback needs on top of it.

### Recovery points

| Source | Created by | Engine version |
|-------------------------|-----------------------|-----------------------|
| Manual pre-upgrade snapshot | Phase 1 step 4, phase 4 | `<FROM>` |
| Automatic pre-upgrade snapshots | RDS, up to two, when the retention period is greater than zero | `<FROM>` |
| Automated backup retention | Continuous | `<FROM>` up to the upgrade |
| Retained rollback parameter group | Phase 1 step 5, phase 4 | Outgoing family |

Verify a snapshot's engine version before relying on it. Three properties the 0A drill measured: an
automatic pre-upgrade snapshot survives its instance's deletion **only if the delete passes
`--no-delete-automated-backups`** (the flag defaults to true); restoring onto the original identifier
reproduces the endpoint hostname but **`DbiResourceId` is new**, which is what forces the proxy
re-registration; and each delete that retains its backups leaves its own set under the same identifier, so a
rollback plus its eventual cleanup means two. **Metadata is not proof**: connect over TLS and read the version
from the engine.

### The rollback procedure

Run the session setup with `ENV` set to the environment in trouble, then the runbook's steps **1 to 6** with
the values below, the Terraform revert, the runbook's step **7**, and on development the scheduler check.
Phase 0A rehearses the runbook's steps 1, 3 and 5 on a clone; the rest have nothing to act on there.

```bash
DB=${ENV}-optinist-cloud-rds
RESTORE_PG=${ENV}-optinist-ssl-rollback
# The retained outgoing-family group from phase 1 step 5. An outgoing-version instance
# cannot attach the new family, and the restore fails on this argument AFTER it has been
# committed - so prove the group exists first.
aws rds describe-db-parameter-groups --db-parameter-group-name "$RESTORE_PG" \
  --query 'DBParameterGroups[0].{name:DBParameterGroupName,family:DBParameterGroupFamily}'
# Expected: family mysql<FROM>. If it is missing, stop and create it by copying any
# surviving outgoing-family group before going further.
```

| Runbook step | For an upgrade rollback |
|-------------------------|-----------------------|
| 1 — choose the restore source | The manual snapshot `<ENV>-optinist-pre-upgrade-<date>` from phase 1 step 4 or phase 4, on `<FROM>`; RDS's automatic pre-upgrade snapshots are the fallback. Older manual snapshots on other versions are not this work's artefacts: neither restore from them nor delete them in phase 5 |
| 5 — restore | With `RESTORE_PG` set as above, so the restored instance attaches the outgoing family |
| 7 — traffic back | **After the Terraform revert below**, so the services connect to the re-converged instance |

**Revert the Terraform change and re-converge, between the runbook's steps 6 and 7.** Keep
`apply_immediately = true` in place for this apply so the parameter group swap is not deferred.

```bash
# git revert is clean only while the upgrade commit is the newest change to
# infrastructure.tf - after phase 5's cleanup PR it is not. Check before using it.
NEWEST_TF_COMMIT=$(git log -1 --format=%H -- infrastructure/terraform/infrastructure.tf)
git show --stat "$NEWEST_TF_COMMIT"
# Expected: only the engine version and parameter group family lines. If it carries
# application changes, or application commits have landed since, do NOT revert (B6):
# edit the two lines back by hand on a branch off current HEAD instead.
git revert --no-edit "$NEWEST_TF_COMMIT"

cd "$(git rev-parse --show-toplevel)/infrastructure/terraform"
terraform init -backend-config="backends/${TF_ENV}.hcl" -reconfigure
PLAN=$(mktemp -t tfplan-rollback)
terraform plan -var-file="environments/${TF_ENV}.tfvars" -out="$PLAN"
terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.type=="aws_db_instance")
  | "\(.address) -> \(.change.actions|join(","))"'
# Expected: update, or no change at all. A create or delete here would destroy the
# instance just restored, so anything else aborts.
terraform apply "$PLAN"
```

**Development only, and the step most easily missed**: the scheduler must point back at an outgoing-family
group, or tonight's restore aims the new family at an old snapshot - B3 in reverse.

```bash
PG=$(aws lambda get-function-configuration --function-name "${ENV}-dev-scheduler" \
  --query 'Environment.Variables.RDS_PARAMETER_GROUP_NAME' --output text)
aws rds describe-db-parameter-groups --db-parameter-group-name "$PG" \
  --query 'DBParameterGroups[0].DBParameterGroupFamily'
# Expected: mysql<FROM>. Note that the scheduler's own fixed-name snapshot is still on
# <TO> until the next evening stop regenerates it.
```

Then `sql_readings` against the proxy endpoint: `VERSION()` must report `<FROM>`, from the engine.

### How long the rollback window stays open

The mechanism does not degrade with time: a manual snapshot never expires, and nothing about the restore gets
harder. What closes the window is phase 5's deletion of `<ENV>-optinist-pre-upgrade-<date>` and
`<ENV>-optinist-ssl-rollback`, so **agree a retention duration before phase 1 and do not run those deletions
until it has elapsed**; both are close to free to keep. What does change with time:

- **The data loss grows**, and it starts inside the apply window: nothing in the forward path stops traffic,
  so **the maintenance announcement is the mitigation** — say in it that a rollback loses data created during
  the window. Days into the soak, a rollback discards every write since the snapshot.
- **`git revert` stops being the clean path** once application commits have landed.
- **Engine versions are eventually deprecated by AWS**, so a retained snapshot is not a permanent guarantee.

On development a late rollback must also put the nightly cycle back: the revert points the Lambda at an
outgoing-family group, and the next evening's stop regenerates the scheduler snapshot at the old version.

---

## Edge Case Handling

### The database is slower after the upgrade

**Problem:** A major version changes InnoDB memory and IO defaults, and **a family diff does not show most of them.** Two mechanisms produce the same symptom: RDS stops pinning a value and the engine derives it (`innodb_buffer_pool_size`, visible in a family diff), or RDS pins it in neither family and the engine default changed underneath (the larger group — between 8.0 and 8.4 `innodb_io_capacity`, `innodb_io_capacity_max`, `innodb_adaptive_hash_index`, `innodb_change_buffering` and `innodb_buffer_pool_instances`), which a family diff cannot show. **A value that looks derived from another may not be**: `innodb_buffer_pool_instances` did not come back when the pool was pinned back, because its own default had changed. Re-read every moved value after the pins are applied (0A step 11).

**Solution:**
- The symptom is slower, not wrong: `ReadIOPS` or `WriteIOPS` rising, latency up. Not an error.
- **Attribute by the step 8 / step 9 pair** — the same running server, before and after — not by diffing family defaults.
- `innodb_io_capacity` deserves a decision: a large increase tells the page cleaner it has IO headroom the volume may not have. Pin it and `innodb_io_capacity_max` to the outgoing values, and tune deliberately afterwards as separate work.
- Pin `innodb_buffer_pool_size` if the pair shows the pool shrinking, and `innodb_buffer_pool_instances` alongside it. The engine requires the pool to be a multiple of `innodb_buffer_pool_chunk_size` times the instance count.
- A metric that moved against the phase 3 baseline with **no** corresponding difference in the pair is an application question.

---

## Monitoring and Metrics

### Log groups

The groups are listed under *Reference: Log Groups and Error Patterns* in `MAINTENANCE_PROCEDURES.md`. Two
upgrade-specific facts: the RDS error log export must still receive data after a family change (criterion 5's
first query), and the `general` and `slowquery` exports produce nothing because both logs are at the engine
default of off - their silence is not a regression.

### Metrics to compare against the baseline

**These are the eight the criterion 6 baseline captures**, so the two lists stay in step.

| Metric | Why | Expected after upgrade |
|-------------------------|-----------------------|-----------------------|
| `BufferCacheHitRatio` | Buffer pool sizing | **Returns no datapoints on standard RDS MySQL** - an Aurora metric. The three rows below carry the question in its place |
| `ReadIOPS` | The substitute for the above: on a cache-served workload it is flat enough that a sizing change stands out | May rise with a smaller buffer pool |
| `FreeableMemory` | The other side of pool sizing | Unchanged |
| `ReadLatency`, `WriteLatency` | What a user notices about a smaller pool, and the write side of an `innodb_io_capacity` change | May rise with the IOPS |
| `WriteIOPS` | Background flushing, if `innodb_io_capacity` rose | May rise independently of any application change |
| `CPUUtilization`, `DatabaseConnections` | Baseline comparison; pool health after the rollout | Unchanged; back to the pre-upgrade level |

---

## Configuration

### Terraform attributes involved

| Attribute | Resource | Purpose during an upgrade |
|-------------------------|-----------------------|-----------------------|
| `family` | `aws_db_parameter_group.main` | ForceNew. Changing it replaces the group |
| `name_prefix` | `aws_db_parameter_group.main` | Replaces `name` so the replacement does not collide - B1 |
| `engine_version` | `aws_db_instance.main` | Major-only value, treated as a prefix |
| `allow_major_version_upgrade` | `aws_db_instance.main` | Required for the upgrade. Removed afterwards |
| `apply_immediately` | `aws_db_instance.main` | Prevents deferral to the maintenance window - B2. Removed afterwards |
| `auto_minor_version_upgrade` | `aws_db_instance.main` | **Not declared** - it takes the provider default of `true`, which is why a major-only version works. Setting it to `false` would break that, so check before assuming it is set |
| `backup_retention_period` | `aws_db_instance.main` | Must be greater than zero for automatic pre-upgrade snapshots |

### Scheduler environment variables

Listed under *Terraform Configuration* in `DEV_SCHEDULE_GUIDE.md`. The one this procedure changes is
`RDS_PARAMETER_GROUP_NAME`, the group a nightly restore attaches, which must follow B1's rename (phase 1
step 13).

### Scheduled jobs that touch the database

The longest cadence is 24 hours, which is why one nightly cycle covers every scheduled path.

| Cadence | Jobs |
|-------------------------|-----------------------|
| 5 minutes | `free-manager`, `PublishedExperimentSyncJob` - the fastest cadence |
| 10 minutes | `common-user-manager` |
| 15 minutes | `premium-manager` |
| 1 hour | `premium-cleanup`, `cost-tracker`, `DataCleanupJob`, `StorageReconciliationJob`, `PremiumExpirationSweep` |
| Daily | `public-cleanup`, `dev-scheduler` |
| 24 hours | `ExpirationDeletion` - the longest |
| Monthly | Image Builder pipeline - builds an AMI, never touches the database |

Intervals for the in-process jobs are in `studio/app/common/core/subscription/constants.py`; the Lambda cadences are the EventBridge rules in `infrastructure/terraform/`.

---

## Testing

**The backend pytest lane does not reach the deployed database**: `make test_backend`, `make alembic_check`, `make premium_lock_it` and `make workflow_count_it` run against a containerised MySQL. Regression cover, not upgrade verification. Only the deployed e2e lanes reach the upgraded instance, through the RDS Proxy — the same path every ECS task uses — so they are evidence for the proxy authentication chain as well.

| Lane | What it covers on a deployed environment |
|-------------------------|-----------------------|
| `17-aws-health` | Read-only. Instance availability, alarms, encrypted connection, and real SQL reads through the proxy. Can be pointed at production |
| `15-premium-aws` | Advisory lock calls, through the premium assignment path |
| `16-storage-aws` | Storage aggregation against the deployed database |
| Browser specs | The application paths, with skips promoted to failures |

Two lanes are localhost-only; one subscription lane writes to the shared development database without an opt-in flag; the disruptive lane refuses to run when another account has been active recently.

### Choosing the lanes

**The two AWS-facing lanes beyond the health lane take well over an hour together, and criterion 4 reaches
more hand-written SQL than they do** — roughly 120 raw `execute()` statements in the Lambda packages, which
nothing else executes. **So the release e2e set plus criterion 4 substitutes for them, conditional on
criterion 4 running in full.** Run the health lane regardless. The residual risk, `GROUP BY` strictness, is
closed by measurement: step 8 records `sql_mode`.

### The engine-version assertion

**Deferred to after phase 4, and tracked in its own issue (see References).** It closes the B2 failure state
— instance on the old version with an upgrade queued — and is regression cover for the next change, not
verification of this one. An assertion against a hard-coded engine version ships in the same commit as the
`engine_version` edit, so it cannot fail during the upgrade it claims to guard.

---

## Key Functions Reference

| Function | File | Purpose during an upgrade |
|-------------------------|-----------------------|-----------------------|
| `restore_rds()`, `stop_rds()`, `ensure_rds_proxy_target()` | `infrastructure/terraform/dev_scheduler_package/dev_scheduler.py` | The restore's explicit parameters (no engine version: B3); the stop that deletes only its configured identifier; the proxy re-registration development gets and production does not |
| `runShellOverSsm()`, `runSql()` | `frontend/e2e/helpers.ts` | The in-VPC command and SQL mechanisms `ssm_sh` and `sql_run` reproduce |

---

## AWS Resources

| Resource | Name pattern |
|-------------------------|-----------------------|
| DB instance | `<ENV>-optinist-cloud-rds` |
| DB parameter group | `<ENV>-optinist-ssl-<suffix>` after B1's fix |
| DB subnet group | `<ENV>-optinist-rds-subnet-group` |
| RDS Proxy | `<ENV>-optinist-rds-proxy` |
| Database credentials | Secrets Manager, `<ENV>-optinist/database/config` |
| Scheduler Lambda | `<ENV>-dev-scheduler` |
| In-VPC SSM target | EC2 tagged `<ENV>-optinist-background` |
| ECS cluster | `<ENV>-optinist-cloud-cluster` |
| ECS services | `<ENV>-optinist-cloud-service`, `<ENV>-premium-optinist-cloud-service`, `<ENV>-background-optinist-cloud-service`, `<ENV>-public-optinist-cloud-service` |

---

## References

- `DEV_SCHEDULE_GUIDE.md` - the nightly schedule, overrides, and manual start and stop
- `INFRA_DEPLOYMENT_PROCEDURE.md` - backend switching and the apply flow
- `MAINTENANCE_PROCEDURES.md` - routine database maintenance and the storage-full runbook
- `DOCUMENTATION_STYLE_GUIDE.md` - conventions this document follows

AWS documentation:

- [Upgrades of the RDS for MySQL DB engine](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_UpgradeDBInstance.MySQL.html) - automatic pre-upgrade snapshots, and that the engine version cannot be reverted
- [Supported Regions and DB engines for RDS Proxy](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.RDS_Fea_Regions_DB-eng.Feature.RDSProxy.html)
- [Upgrade strategies for Amazon RDS for MySQL 8.0 to 8.4](https://aws.amazon.com/blogs/database/upgrade-strategies-for-amazon-rds-for-mysql-8-0-to-8-4/) - the precheck categories and `PrePatchCompatibility.log`

First applied for MySQL 8.0 to 8.4 under issue #877, whose child issues carry the per-phase execution records, the combined release order used in phase 4, and the deferred engine-version assertion (#902).
