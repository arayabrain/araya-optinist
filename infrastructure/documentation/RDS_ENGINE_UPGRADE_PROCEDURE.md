# RDS Engine Upgrade: MySQL Major Version Procedure

## Executive Summary

- **This procedure covers an in-place MySQL major version upgrade** of the RDS instances in both environments, using `ModifyDBInstance` rather than a parallel instance or a blue/green deployment.
- **The engine change is two lines of Terraform.** Everything else in this document exists because the surrounding configuration does not tolerate those two lines without preparation.
- **Six constraints (B1 to B6)** must be handled before any apply. They are properties of this stack, not of any particular version pair, so they recur on every major upgrade.
- **The development scheduler's nightly destroy and restore cycle is the dominant constraint.** It makes the upgrade a same-day operation that must not span a night, and it makes the nightly cycle itself a mandatory verification step.
- **A terraform apply in this repository is also an application deploy.** The build trigger is the repository HEAD commit, so the apply rebuilds the image and rolls every ECS service.
- **Rollback is a snapshot restore.** There is no engine downgrade, and the restore creates a new DB instance rather than reverting the existing one.

**Version placeholders used throughout:** `<FROM>` is the current major version, `<TO>` the target, and `<ENV>` the Terraform `var.environment` value. First applied for MySQL 8.0 to 8.4 (see [References](#references)).

---

## Key Architectural Principles

1. **In-place upgrade, not a parallel instance**
   - `RestoreDBInstanceFromDBSnapshot` has no engine version parameter, so an `<FROM>` snapshot always restores as `<FROM>`. A parallel instance therefore requires snapshot, restore, then the same engine upgrade, with the upgrade duration unchanged and a restore stacked in front of it.
   - The copy is also frozen at the snapshot, so keeping it current means building binlog replication by hand.
   - The in-place upgrade keeps the existing, fully warm storage volume.

2. **The apply must modify the instance, never replace it**
   - A replacement creates an empty database behind the same identifier and endpoint, so the application reconnects transparently and the loss is silent.
   - This is enforced mechanically, by asserting against the plan JSON, not by reading the plan.

3. **The parameter group swap and the engine upgrade must land in the same apply**
   - The scheduler's restore passes a parameter group name but no engine version, so a parameter group whose family is ahead of the snapshot's engine version breaks every subsequent restore.
   - The instance must reach `available` on `<TO>` before the same day's scheduled stop.

4. **Verification is mechanical, or it does not count**
   - Every gate in this procedure is an asserted command output or a plan-JSON query.
   - The failure mode this guards against is a deferred upgrade that Terraform reports as successful.

5. **Recovery points are created before the apply, and closed deliberately afterwards**
   - A manual snapshot and a retained parameter group of the outgoing family are both prerequisites, and both are verified rather than assumed.
   - **Do not count on the automated retention as a long window on development.** The retention period reads the same in both environments, but the development instance is recreated nightly, so its point-in-time window only spans the current instance's lifetime. The manual snapshot is what makes a rollback possible there.
   - Deleting the manual snapshot and the retained parameter group is what closes the rollback window, so it is a decision with an agreed duration rather than cleanup.

---

## Architecture Overview

### Phase flow

```
┌──────────────────────────────────────────────────────────┐
│ 0. Rehearse on throwaway clones                          │
│    → 0A clone of development: upgrade AND rollback drill  │
│    → 0B clone of production: conditional, see its gate     │
│    → 0C plan-only gate dry run                           │
└──────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────┐
│ 1. Apply to development (live)                            │
│    → weekday, must finish before the scheduled stop       │
│    → plan gate, then apply, then confirm not deferred     │
└──────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────┐
│ 2. Two nightly destroy/restore cycles                     │
│    → proves the restore works against a <TO> snapshot     │
│    → second cycle proves the steady state                 │
└──────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────┐
│ 3. Soak to exit criteria (floor: two nights)               │
│    → deployed test lanes, scheduled jobs, logs, baseline  │
└──────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────┐
│ 4. Apply to production (live)                             │
│    → window covers DB downtime plus a full ECS rollout    │
└──────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────┐
│ 5. Verify, close the rollback window, clean up config     │
└──────────────────────────────────────────────────────────┘
```

### What each phase touches

| Phase | Target | Live impact | Code change | Reversal |
|-------------------|-----------------------|-----------------------|-----------------------|-----------------------|
| 0A | Throwaway clone of development | None | No | Delete the clone |
| 0B | Throwaway clone of production | **Creates a snapshot of production. Nothing else** | No | Delete the clone and the snapshot |
| 0C | Terraform state | None - read only | No | Nothing to reverse |
| 1 | Development instance | Offline for the upgrade | Yes - Terraform | Snapshot restore |
| 2 | Development instance | None beyond the normal cycle | No | Snapshot restore |
| 3 | Development instance | Test and job traffic | Yes - test assertion | Snapshot restore |
| 4 | Production instance | Offline, plus ECS rollout | No - applies phase 1 code | Snapshot restore |
| 5 | Both | None | Yes - config removal | Revert the cleanup |

### Ordering constraints

- **Phase 1 must not span a night.** Weekdays only, started early enough to finish before the scheduled stop.
- **Phase 2 cannot be compressed.** A nightly cycle happens once per night, so two nights is a floor. It is the only floor in the procedure.
- **Phase 0's clones must be gone before phase 1**, or the apply cannot destroy the outgoing parameter group.
- **Phase 4 needs two artefacts from earlier phases**: a measured downtime for the announcement — from 0B when it runs, otherwise from 0A plus a margin — and phase 3's production baseline metrics, which cannot be captured afterwards.
- **Phase 0 touches production only in 0B, and only to create a snapshot.** 0A and 0C are entirely within development. When 0B's gate says skip, phase 0 performs no production operation at all.

---

## Implementation Details

### The six constraints

These are referenced as B1 to B6 from the tracking issues.

#### B1: The parameter group name is fixed, so create_before_destroy collides

**File:** `infrastructure/terraform/infrastructure.tf`

Both `family` and `name` are ForceNew on `aws_db_parameter_group`, so changing the family replaces the resource. With `create_before_destroy = true`, Terraform creates the replacement first, under the same name, and AWS rejects it with `DBParameterGroupAlreadyExists`.

`create_before_destroy` is therefore not a help here; with a hard-coded name it is what makes the apply fail.

**Fix:** use `name_prefix` in place of `name`, and update the resource's `Name` tag to match. This is durable across future family changes. A one-off rename embedding the target version also works, at the cost of another rename next time.

Consumers follow automatically because every reference derives from `aws_db_parameter_group.main.name`: the instance's `parameter_group_name`, the scheduler Lambda's `RDS_PARAMETER_GROUP_NAME` environment variable, and the parameter group ARN in the scheduler's IAM policy.

#### B2: apply_immediately is unset, so the upgrade is deferred and then lost

The provider default is `false`, so `ModifyDBInstance` records the new version in `PendingModifiedValues` and RDS performs the upgrade at the next maintenance window. **Terraform reports a successful update in place either way**, so nothing looks wrong.

On development the maintenance window falls at a time when the scheduler has already deleted the instance. The pending upgrade disappears with it, leaving Terraform state on `<TO>` and the instance on `<FROM>` permanently, while the parameter group has already moved - which then triggers B3 on the next restore.

**Fix:** set `apply_immediately = true` for the upgrade apply. Neither this nor `allow_major_version_upgrade` is read back from the API, so removing them afterwards produces **no diff on `aws_db_instance.main`**. That is the assertion to make — not that the whole plan is empty, which it never is here: see B6, where committing anything changes `source_revision` and so always replaces `null_resource.build_and_deploy`.

#### B3: An outgoing-version snapshot restored against the new parameter group family fails

**File:** `infrastructure/terraform/dev_scheduler_package/dev_scheduler.py`

`restore_rds()` passes `DBParameterGroupName` to `restore_db_instance_from_db_snapshot` but no `EngineVersion`, so a restore always comes back at the snapshot's engine version. An `<FROM>` snapshot combined with a `<TO>`-family parameter group is rejected with `InvalidParameterCombination`, and the development database does not come up in the morning.

**Fix:** this is an ordering constraint, not a code change. The parameter group swap and the engine upgrade must complete in the same `ModifyDBInstance`, and the instance must be `available` on `<TO>` before that day's scheduled stop. The snapshot taken that evening is then `<TO>` and every subsequent cycle is consistent.

#### B4: Applying while the development instance is destroyed recreates it empty

If the scheduler has already deleted the instance, Terraform sees it missing and creates a fresh, empty one. Identifier, DB name, subnet group and security groups are all identical, so the proxy and the application reconnect transparently and nobody notices until the next day.

**Fix:** gate it mechanically rather than by reviewing the plan visually. Confirm `DBInstanceStatus` is `available` immediately before applying, and assert against the plan JSON that `aws_db_instance` carries no `create` or `delete` action.

#### B5: Do not declare engine_lifecycle_support

Declaring the Extended-Support-disabled value to prevent silently re-entering paid Extended Support later looks attractive, but the AWS provider only sends `EngineLifecycleSupport` on the create, restore-from-snapshot, point-in-time-restore and replica paths. There is no handling in the update path, so adding it to a live instance produces a diff on every plan that no apply can clear.

**Fix:** leave it out. After the upgrade the attribute continues to read the Extended Support value, and that is correct - it is an attribute, not a charge, and a version inside standard support is not billed for Extended Support.

#### B6: The apply is not an RDS-only change

`null_resource.build_and_deploy` triggers on `source_revision`, the repository HEAD commit. Committing the Terraform change therefore changes the trigger, so the apply also rebuilds and pushes the image and force-deploys every ECS service in the cluster.

**What gets built is the local working tree, not a branch.** The trigger is the HEAD commit, but
`infrastructure/scripts/ecr_build_push.sh` runs `docker build` against the local checkout as its
context, and takes the branch name from the local HEAD only to label the image. `var.git_branch` is a
trigger input and an application setting; nothing clones it. So the apply ships whatever is in the
working directory at that moment.

That distinction is easy to miss and it is the one that matters:

1. **The apply is an application deploy of the current checkout.** Applying from a branch that carries
   unrelated application changes ships them in the same motion, and a symptom afterwards then has two
   candidate causes. Choose the branch deliberately - one cut from whatever is deployed, carrying only
   the Terraform change, keeps attribution clean.
2. `infrastructure/scripts/ecr_build_push.sh` refuses to build from a dirty worktree, and its check counts untracked files at the repository toplevel. With scratch files present the apply fails partway through, after the RDS modification has already been issued.
3. The production window must cover the RDS downtime plus a full ECS rollout.

**Fix:** treat it as a combined infrastructure and application deploy. Confirm `git status --porcelain` is empty at the toplevel first, and apply from a working tree whose application diff against what is deployed is empty or trivial.

```bash
# Before applying, know what application change is riding along
git diff --stat <the deployed ref>..HEAD -- studio/app studio/__main_unit__.py frontend/src
# Expected: empty, or a diff you have deliberately decided to ship.
# Files outside these paths - tests, scripts, docs - do not reach the runtime image.
```

A `-target` apply scoped to the RDS resources avoids the rebuild but skips the scheduler Lambda whose parameter group name must follow B1, leaving the nightly restore pointing at a deleted parameter group. If `-target` is used it must include the Lambda and its IAM policy, which is more fragile than cleaning the worktree.

### The Terraform change

```diff
 resource "aws_db_parameter_group" "main" {
-  family = "mysql<FROM>"
-  name   = "${local.env_prefix}-ssl"
+  family      = "mysql<TO>"
+  name_prefix = "${local.env_prefix}-ssl-"
   ...
 }

 resource "aws_db_instance" "main" {
-  engine_version                  = "<FROM>"
+  engine_version                  = "<TO>"
+  allow_major_version_upgrade     = true   # remove once both environments are upgraded
+  apply_immediately               = true   # remove once both environments are upgraded
 }
```

A major-only `engine_version` resolves to the region default for that major version, which works because `auto_minor_version_upgrade` is enabled and the provider treats the value as a prefix.

**Do not keep `allow_major_version_upgrade` permanently.** Removing it means a future `engine_version` edit cannot perform a major upgrade silently.

### Resource identity after the upgrade

An in-place upgrade takes the instance offline, upgrades the engine on the same storage, and brings it back. No second instance is created and no data is copied.

| Attribute | After upgrade | After a rollback restore |
|-------------------------|-----------------------|-----------------------|
| DB instance ARN | Unchanged | Unchanged - identifier-derived |
| Instance identifier | Unchanged | Unchanged, once the restore lands on it |
| Endpoint hostname | Unchanged | Unchanged - identifier-derived |
| Port | Unchanged | Unchanged |
| `DbiResourceId` | Unchanged | **Changes** - assigned per instance creation |
| Parameter group name | **Changes** - name_prefix generates a new suffix | Set explicitly on the restore |
| Tags | Unchanged | Preserved in practice |
| `BackupRetentionPeriod` | Unchanged | Preserved in practice, but not a restore parameter |

The parameter group is the only identity that moves on an upgrade, and every consumer derives it from the Terraform expression, so a full apply keeps them in step by itself.

A changed `DbiResourceId` matters in one place: **the RDS Proxy target must be re-registered.** The proxy deregisters its target when the instance is deleted and does not register a replacement on its own. On development `ensure_rds_proxy_target()` does this on the next scheduler start; on production it is a manual `register-db-proxy-targets`.

### Shared helper: run a command inside the VPC

Neither RDS instance is publicly accessible and the proxy is TLS-only, so the in-database checks run from an in-VPC instance over SSM. This is the same mechanism the e2e suite uses in `runShellOverSsm`.

```bash
# Reads the remote script from stdin and prints its stdout.
ssm_sh() {
  local iid cid status tmp
  iid=$(aws ec2 describe-instances \
    --filters Name=tag:Name,Values="${ENV}-optinist-background" \
              Name=instance-state-name,Values=running \
    --query 'Reservations[0].Instances[0].InstanceId' --output text)
  [ -n "$iid" ] && [ "$iid" != None ] || { echo "no running SSM target" >&2; return 1; }
  tmp=$(mktemp); jq -Rs '{commands:[.]}' > "$tmp"
  cid=$(aws ssm send-command --instance-ids "$iid" --document-name AWS-RunShellScript \
    --parameters "file://$tmp" --query Command.CommandId --output text)
  rm -f "$tmp"
  while :; do
    status=$(aws ssm get-command-invocation --command-id "$cid" --instance-id "$iid" \
      --query Status --output text)
    case "$status" in
      Success) break ;;
      Pending|InProgress|Delayed) sleep 3 ;;
      *) aws ssm get-command-invocation --command-id "$cid" --instance-id "$iid" \
           --query StandardErrorContent --output text >&2; return 1 ;;
    esac
  done
  aws ssm get-command-invocation --command-id "$cid" --instance-id "$iid" \
    --query StandardOutputContent --output text
}

# Expected: prints the remote stdout, or the remote stderr and a non-zero status
```

SQL is then run through the `mariadb` client that instance carries, reading credentials from Secrets Manager. The secret id is `${ENV}-optinist/database/config`.

### Shared helper: delete a rehearsal clone safely

The clone and the real instance differ by one variable name in the same shell session - `CLONE`
against `DB` - and in phase 0B that session is pointed at production. A mistyped identifier on
`delete-db-instance` is therefore the single most damaging error available in this procedure, and two
API defaults make it worse than it looks:

- **`--delete-automated-backups` defaults to true.** Deleting an instance removes its automated
  backups, and with them the point-in-time recovery window, unless the flag is given explicitly.
  `stop_rds()` in `dev_scheduler.py` passes `DeleteAutomatedBackups=False` for exactly this reason,
  which is why retained automated backups exist for the nightly cycle.
- **`--skip-final-snapshot` leaves nothing behind at all.** Combined with the above, an accidental
  delete falls back to the newest *manual* snapshot, which may be weeks old.

Neither `deletion_protection` nor the Terraform `skip_final_snapshot` setting helps here:
`deletion_protection` is not currently enabled on these instances, and `skip_final_snapshot` governs
only what Terraform does on destroy, not a CLI call.

So never call `delete-db-instance` directly in this procedure. Use these:

Every identifier this procedure is allowed to delete carries `rehearsal` in its name. That is the one
predicate the guard needs, and it covers clones, their snapshots and their parameter groups alike.

```bash
# Refuses anything that is not a rehearsal-scoped identifier. The name is the only thing
# standing between a typo and a deleted database, so assert on it rather than on care.
assert_rehearsal() {
  case "$1" in
    *rehearsal*) return 0 ;;
    *) echo "REFUSING: '$1' is not a rehearsal-scoped identifier" >&2; return 1 ;;
  esac
}

# Deletes a clone, but keeps a final snapshot and the automated backups anyway. They
# are cheap on a throwaway, and the habit is what makes a mistyped identifier
# survivable rather than terminal.
drop_clone() {
  assert_rehearsal "$1" || return 1
  aws rds delete-db-instance --db-instance-identifier "$1" \
    --final-db-snapshot-identifier "$1-final" --no-delete-automated-backups
  aws rds wait db-instance-deleted --db-instance-identifier "$1"
}

# Snapshots need the same guard. Phase 0B's source snapshot is taken from the production
# instance, so an unguarded delete here can destroy a real recovery point - the newest
# manual snapshot is exactly what an accidental instance delete falls back to.
drop_snapshot() {
  assert_rehearsal "$1" || return 1
  aws rds delete-db-snapshot --db-snapshot-identifier "$1"
}
```

Verify both guards before relying on them, against names that must never be accepted:

```bash
drop_clone    "${ENV}-optinist-cloud-rds"
drop_snapshot "${ENV}-pre-upgrade-20260101-0000"
# Expected: REFUSING twice, and nothing sent to AWS. Anything else means the guard is
# not working, which is an abort condition - see Abort conditions above.
```

### Shared helper: sanitise a log before posting it

Execution logs are posted to the tracking issues, which are public. Replace environment-specific values with a filter rather than by hand: manual masking fails under time pressure, and **editing a comment does not unpublish anything** - GitHub keeps prior revisions readable, GH Archive is a permanent public record of public issue events, and notification emails have already gone out.

```bash
# Usage: <command> 2>&1 | redact
redact() {
  local acct
  acct=$(aws sts get-caller-identity --query Account --output text)
  sed -E \
    -e "s/${acct}/<ACCOUNT_ID>/g" \
    -e 's/\b(development|subscr)-optinist/<ENV>-optinist/g' \
    -e 's/\/ecs\/(development|subscr)-/\/ecs\/<ENV>-/g' \
    -e 's/proxy-[a-z0-9]{8,}/proxy-<TOKEN>/g' \
    -e 's/\bi-[0-9a-f]{8,}/i-A/g' \
    -e 's/\bdb-[A-Z0-9]{10,}/db-A/g' \
    -e 's/db\.[a-z0-9]+\.[a-z]+/<INSTANCE_CLASS>/g'
}

# Expected: the same text with identifiers replaced. Durations, engine versions,
# sql_mode values, precheck findings, metric values and cost figures are left intact -
# acceptance criteria are written against them.
```

The filter is a convenience, not a guarantee: it does not know about values it has never seen. Check the result before posting.

**This table is the single source for the substitution policy.** The tracking issues reference it rather
than restating it, so it only has to be corrected in one place.

| In the output | Post as | Why |
|-------------------------|-----------------------|-----------------------|
| The environment prefix | `<ENV>` | Matches PR #620, the worked example in this repository |
| The AWS account id | `<ACCOUNT_ID>` | Enables cross-account enumeration |
| The proxy endpoint's account token | `proxy-<TOKEN>` | A resolvable hostname |
| Instance ids and `DbiResourceId` values | `i-A`, `db-A` | Per-resource identifiers |
| Instance class, volume type and size | Generalise | Capacity information: it lets someone size an attack without reconnaissance |
| **Durations, engine versions, `sql_mode`, precheck findings, metric values, cost figures** | **Keep as measured** | Outcome measurements. Later phases and the acceptance criteria are written against them, so redacting these breaks the work's own definition of done |

Resource *name patterns* are fine with `<ENV>` substituted - `<ENV>-optinist-cloud-rds`,
`/ecs/<ENV>-optinist-cloud-taskdef` and the like. They are names, not capacity, and access to them is
governed by IAM.

---

## Procedure

Set the environment once per session. `ENV` is the Terraform `var.environment` value.

```bash
export AWS_REGION=ap-northeast-1
export ENV=development          # or the production value
DB=${ENV}-optinist-cloud-rds
```

### Prerequisites

| Requirement | Why | If missing |
|-------------------------|-----------------------|-----------------------|
| AWS profile for the account | Every step | Nothing runs |
| `environments/<env>.tfvars` with real values | `terraform plan` in phases 0C, 1 and 4 | No plan, so no gate |
| `jq` | The plan-JSON gates | The gate degrades to reading the plan by eye, which B4 exists to prevent |
| Clean toplevel worktree | The applies in phases 1 and 4 | The apply fails after the RDS modification is issued - B6 |
| SSM access to an in-VPC instance | The in-database checks | Those checks become manual |

Both tfvars files are gitignored and are not in a fresh checkout. Obtain them before starting.

### Establish the recovery floor before touching anything

Know what the worst case already costs, before any phase runs. This is the number an accidental delete
falls back to, and it is usually older than people assume.

```bash
# Automated backups: the point-in-time window, and whether there is one at all
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{retention:BackupRetentionPeriod,delProt:DeletionProtection}'
# Expected: retention greater than zero. Zero means no automated backups and no PITR.
# DeletionProtection false means a mistyped delete succeeds immediately.

aws rds describe-db-instance-automated-backups \
  --query "DBInstanceAutomatedBackups[?DBInstanceIdentifier=='${DB}'].{status:Status,
           from:RestoreWindow.EarliestTime,to:RestoreWindow.LatestTime}" --output table
# Expected: read the active row's window, and do not assume it matches the retention
# period. On an instance that is deleted and restored nightly the window spans only the
# current instance's lifetime - hours, not weeks - because each night's instance carries
# its own. Earlier nights appear as separate retained rows covering only themselves.

# Manual snapshots: these survive an instance delete, so they are the true floor
aws rds describe-db-snapshots --db-instance-identifier "$DB" --snapshot-type manual \
  --query 'reverse(sort_by(DBSnapshots,&SnapshotCreateTime))[].{id:DBSnapshotIdentifier,
           ev:EngineVersion,created:SnapshotCreateTime}' --output table
# Expected: read the newest date. That is how much data an accidental delete costs
# once the automated backups are gone with it.
```

If the newest manual snapshot is old, take a fresh one before starting. Phases 1 and 4 each take one
anyway, but those come after the rehearsal phases, and 0B operates a session pointed at production.

### Abort conditions

Stop immediately if any of these is true. Each one has a command that detects it in the phase where
it applies, so none of them requires a judgement call.

| Condition | Why it aborts |
|-------------------------|-----------------------|
| The plan shows `create`, `delete` or a replacement for `aws_db_instance.main` | B4. Applying would leave an empty database behind the same endpoint |
| `describe-db-instances` returns anything but `available` immediately before the apply | B4. The instance may be mid-delete or mid-restore |
| Development work will run past the scheduled stop | B3. The upgrade must not span a night |
| `PrePatchCompatibility.log` contains errors | The same failure occurs on the real instance |
| The manual pre-upgrade snapshot has not reached `available` | A snapshot still being created is not a recovery point |
| `git status --porcelain` is non-empty at the toplevel | B6. The apply fails after the RDS modification is issued |
| It is night, a weekend or a holiday and the target is development | The scheduler may have deleted the instance |
| The phase 0A rollback drill did not complete, or the automatic pre-upgrade snapshots were absent or on the wrong version | The rollback has no proven recovery point |
| A rehearsal clone is still alive or still references the live parameter group | Edge case 1. The apply cannot destroy the outgoing group |
| The tfvars file for the target environment is unavailable | Without a plan there is no gate |
| The newest manual snapshot predates the work by more than the acceptable data loss | That snapshot is the floor if an instance is deleted with its automated backups |
| `drop_clone` does not refuse a real instance identifier when tested | The guard against the procedure's most damaging typo is not working |

### Phase 0A: rehearse the upgrade and the rollback on a clone of development

Nearly free: the nightly snapshot already exists, so this costs a few hours of one instance. Nothing live is touched and nothing enters Terraform state.

```bash
CLONE=${ENV}-optinist-rds-upgrade-rehearsal
SNAP=${DB}-dev-scheduler          # the scheduler's nightly snapshot

# 1. Confirm the source snapshot is usable and on the outgoing version
aws rds describe-db-snapshots --db-snapshot-identifier "$SNAP" \
  --query 'DBSnapshots[0].{status:Status,ev:EngineVersion,created:SnapshotCreateTime}'
# Expected: available, <FROM>.x, last night's timestamp

# 2. A private parameter group for the clone, copied from the live one.
#    Do not attach the live group - see edge case 1.
aws rds copy-db-parameter-group \
  --source-db-parameter-group-identifier "${ENV}-optinist-ssl" \
  --target-db-parameter-group-identifier rehearsal-ssl-from \
  --target-db-parameter-group-description "outgoing-family rehearsal copy"

# 3. Read the restore parameters from the live instance rather than hardcoding them.
#    restore_rds() in dev_scheduler.py is the reference for which values a restore needs.
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

# 4. The clone must have inherited a backup retention period, or RDS takes no
#    automatic pre-upgrade snapshot and step 7 has nothing to work with.
aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].{retention:BackupRetentionPeriod,rid:DbiResourceId}'
# Expected: retention greater than zero

# 5. A target-family parameter group carrying the same user-set parameters
aws rds create-db-parameter-group --db-parameter-group-name rehearsal-ssl-to \
  --db-parameter-group-family "mysql<TO>" --description "target-family rehearsal"
aws rds modify-db-parameter-group --db-parameter-group-name rehearsal-ssl-to \
  --parameters "ParameterName=require_secure_transport,ParameterValue=1,ApplyMethod=immediate" \
               "ParameterName=time_zone,ParameterValue=UTC,ApplyMethod=immediate"

# 6. Upgrade, and record the duration
date -u
aws rds modify-db-instance --db-instance-identifier "$CLONE" \
  --engine-version "<TO>" --db-parameter-group-name rehearsal-ssl-to \
  --allow-major-version-upgrade --apply-immediately
aws rds wait db-instance-available --db-instance-identifier "$CLONE"
date -u
```

Then the precheck log. This comes from the DB log file API rather than CloudWatch, so the clone needs no log exports.

```bash
aws rds describe-db-log-files --db-instance-identifier "$CLONE" \
  --query 'DescribeDBLogFiles[?contains(LogFileName,`PrePatchCompatibility`)].LogFileName' \
  --output text | while read -r f; do
    aws rds download-db-log-file-portion --db-instance-identifier "$CLONE" \
      --log-file-name "$f" --starting-token 0 --output text
  done | tee /tmp/prepatch.log
grep -icE '^\[?(error|fatal)' /tmp/prepatch.log
# Expected: 0. Any error here fails the same way on the real instance.

grep -inE 'warning|notice' /tmp/prepatch.log
# Expected: read every line. Warnings are not automatically benign.
```

Then the in-database state:

```bash
CLONE_HOST=$(aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].Endpoint.Address' --output text)

ssm_sh <<SH
set -e
CFG=\$(aws secretsmanager get-secret-value --region ${AWS_REGION} \
  --secret-id ${ENV}-optinist/database/config --query SecretString --output text)
export MYSQL_PWD=\$(printf '%s' "\$CFG" | python3 -c 'import json,sys;print(json.load(sys.stdin)["password"])')
DBU=\$(printf '%s' "\$CFG" | python3 -c 'import json,sys;print(json.load(sys.stdin)["username"])')
DBN=\$(printf '%s' "\$CFG" | python3 -c 'import json,sys;print(json.load(sys.stdin)["database"])')
mariadb --ssl -h $CLONE_HOST -u "\$DBU" --connect-timeout=10 "\$DBN" -t <<'SQL'
SELECT VERSION();
SELECT @@sql_mode;
SELECT @@innodb_buffer_pool_size, @@innodb_dedicated_server, @@binlog_format, @@log_output;
SELECT user, host, plugin FROM mysql.user ORDER BY user;
SQL
SH
```

| Query | Expected | What it settles |
|-------------------------|-----------------------|-----------------------|
| `VERSION()` | `<TO>.x` | The upgrade reached the engine, not just `PendingModifiedValues` |
| `@@sql_mode` | Unchanged from `<FROM>` | Query acceptance behaviour does not change across the upgrade |
| `@@innodb_buffer_pool_size` | May change - see edge case 5 | The one measurable delta of a family change |
| `@@binlog_format` | May change to `ROW` | Harmless with no replicas and no external consumers |
| `mysql.user` plugins | Existing users keep their plugin | The proxy's client auth type keeps working |

The connection succeeding at all is the `require_secure_transport` check: TLS against a group that requires it, on the new family.

Then alembic. `DB_HOST` wins over `MYSQL_SERVER` in the URL builder, so override both.

```bash
ssm_sh <<SH
set -e
C=\$(docker ps --format '{{.Names}}' | grep -m1 -- -background-optinist-cloud-container)
docker exec -e DB_HOST=$CLONE_HOST -e MYSQL_SERVER=$CLONE_HOST -w /app "\$C" \
  sh -c 'alembic current && alembic upgrade head && alembic current'
SH
# Expected: the same revision before and after. Head is already applied on a clone
# of a live database, so this confirms rather than migrates.
```

Then the rollback drill, which is the reason 0A exists.

**What this drill is a rehearsal of:** the [Rollback](#procedure-1) procedure, which is the authoritative
version and is written for a live instance. This drill covers only its restore mechanics - steps 0 and 2
there - because a clone has no traffic to stop, is deliberately kept off the RDS Proxy, and is outside
Terraform state. So the drill cannot exercise that procedure's steps 1, 3, 4 and 5; Phase 2 covers proxy
re-registration for real, and the rest are only ever exercised in an actual rollback.

Read the Rollback section for the concepts - why a restore *is* the rollback, which recovery points
exist, and how long the window stays open. They are not repeated here.

```bash
# 7. Locate the automatic pre-upgrade snapshot this clone's own upgrade produced.
PRE=$(aws rds describe-db-snapshots --db-instance-identifier "$CLONE" \
  --snapshot-type automated \
  --query 'reverse(sort_by(DBSnapshots[?starts_with(EngineVersion,`<FROM>`)],
           &SnapshotCreateTime))[0].DBSnapshotIdentifier' --output text)
echo "$PRE"
# Expected: a snapshot id. "None" means RDS took none, so check the retention period
# from step 4 - and note that the same absence on a real instance would mean the
# Rollback procedure has lost one of its recovery points.

# 8. Free the identifier, then restore under it. This is the path that keeps
#    Terraform convergent. Time both halves.
#    assert_rehearsal first: this is the one delete in the procedure that is meant to
#    happen, which makes it the one most likely to be re-run against the wrong name.
assert_rehearsal "$CLONE" || return 1
date -u
aws rds delete-db-instance --db-instance-identifier "$CLONE" \
  --final-db-snapshot-identifier "${CLONE}-broken" --no-delete-automated-backups
aws rds wait db-instance-deleted --db-instance-identifier "$CLONE"
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier "$CLONE" --db-snapshot-identifier "$PRE" \
  --db-instance-class "$CLASS" --storage-type "$STORAGE" --port 3306 \
  --db-subnet-group-name "$SUBNET" --vpc-security-group-ids "$SG" \
  --db-parameter-group-name rehearsal-ssl-from \
  --no-publicly-accessible --no-multi-az
aws rds wait db-instance-available --db-instance-identifier "$CLONE"
date -u

# 9. Confirm what came back
aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].{ev:EngineVersion,endpoint:Endpoint.Address,rid:DbiResourceId,
           pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus}'
# Expected: EngineVersion back at <FROM>.x - the engine version is a property of the
# snapshot; the endpoint hostname identical to before the drill; DbiResourceId
# different, which is why a real rollback must re-register the proxy target.
```

Record the measured durations. They replace estimates in any rollback decision.

Tear down the same day, and in any case before phase 1:

```bash
drop_clone "$CLONE"

# The clone's own snapshots and retained automated backups, now that the instance is
# gone. These exist because drop_clone deliberately keeps them; clean them up here
# rather than by weakening the delete.
drop_snapshot "${CLONE}-final"
drop_snapshot "${CLONE}-broken"
for ARN in $(aws rds describe-db-instance-automated-backups \
  --query "DBInstanceAutomatedBackups[?DBInstanceIdentifier=='${CLONE}'].DBInstanceAutomatedBackupsArn" \
  --output text); do
  aws rds delete-db-instance-automated-backup --db-instance-automated-backups-arn "$ARN"
done

aws rds delete-db-parameter-group --db-parameter-group-name rehearsal-ssl-from
aws rds delete-db-parameter-group --db-parameter-group-name rehearsal-ssl-to

# Expected: nothing rehearsal-shaped remains
aws rds describe-db-instances \
  --query 'DBInstances[?contains(DBInstanceIdentifier,`rehearsal`)].DBInstanceIdentifier' \
  --output text
aws rds describe-db-snapshots --snapshot-type manual \
  --query 'DBSnapshots[?contains(DBSnapshotIdentifier,`rehearsal`)].DBSnapshotIdentifier' \
  --output text
```

### Phase 0B: rehearse on a clone of production - conditional

**This is the only phase before phase 4 that touches production at all, and it is not always
necessary.** Decide with the gate below rather than by default. Skipping it keeps the whole of phase 0
inside the development environment, which is worth something on its own: it removes an occasion for a
mistyped identifier against production, in a procedure where that is the most damaging error available.

0B exists to provide two things 0A cannot. Both are conditional.

#### Gate 1: does the production schema differ from development's?

The precheck is mostly schema-driven - reserved words, removed features, foreign key name lengths,
views and routines referencing removed functions. The schema is whatever alembic has applied, and both
environments run `alembic upgrade head` at startup. **If both are at the same revision, 0A's precheck
already covered production's schema.**

They are not automatically the same: the environments track different branches, so production can be
behind.

```bash
# Read-only. One query per environment, through the proxy.
# Run the ssm_sh SQL block from phase 0A with this statement, once per environment.
SELECT version_num FROM alembic_version;
# Expected: the same revision in both. If they differ, production's schema is not what
# 0A tested and 0B should run.
```

What remains uncovered by 0A even when the revisions match is the **data-dependent** part of the
precheck - the table-validation checks, which run over real rows that development does not have. That
residual risk is what gate 2 prices.

#### Gate 2: how different are the data volumes?

The announced downtime comes from 0A unless production holds materially more data.

```bash
for E in <development env> <production env>; do
  echo "== $E"
  aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name FreeStorageSpace \
    --dimensions "Name=DBInstanceIdentifier,Value=${E}-optinist-cloud-rds" \
    --start-time "$(date -u -v-2H +%Y-%m-%dT%H:%M:%SZ)" \
    --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --period 300 --statistics Average \
    --query 'sort_by(Datapoints,&Timestamp)[-1].Average' --output text
done
# Expected: subtract each from AllocatedStorage to get used space. If the two are
# comparable, 0A's measured duration transfers and only needs a margin. When this was
# last measured the two environments were within a few percent of each other.
```

#### The decision

| Gate 1 (alembic revisions) | Gate 2 (used data volume) | Decision |
|-------------------------|-----------------------|-----------------------|
| Same | Comparable | **Skip 0B.** Use 0A's duration plus a margin for the announcement |
| Same | Production materially larger | Run 0B for the timing. The precheck is already covered |
| **Differ** | Either | **Run 0B.** Production's schema was not tested by 0A |

**If 0B is skipped, carry one consequence into phase 4.** The precheck then runs for the first time
during the real upgrade. It fails safe - a precheck failure means the upgrade does not complete and the
instance stays on the outgoing version, so it costs the window rather than the data - but the window
needs a stated response for that outcome rather than treating it as impossible.

#### If 0B runs

Same sequence as 0A, with these differences:

- `ENV` and the SSM target point at production.
- The source is a fresh manual snapshot of production rather than a scheduler snapshot. **Name it with
  `rehearsal` in it**, so the teardown guards accept it and nothing else.
- The rollback drill is not repeated - 0A proved the procedure, and a production-sized restore only
  re-measures a duration that scales with the same data.

**The only operation 0B performs against production is creating a snapshot**, which is additive.
Everything else - the parameter groups, the restore, the upgrade, every check - happens on the clone.
The exposure is not the operation but the session: `DB` and `CLONE` are both defined in it, and `DB` is
production.

```bash
export ENV=<production env> SSM_NAME=<production env>-optinist-background
DB=${ENV}-optinist-cloud-rds
CLONE=${ENV}-optinist-rds-upgrade-rehearsal
SNAP=${ENV}-optinist-rehearsal-source-$(date +%Y%m%d-%H%M)

# The one production operation in this phase. Additive: it creates, it does not modify.
aws rds create-db-snapshot --db-instance-identifier "$DB" --db-snapshot-identifier "$SNAP"
aws rds wait db-snapshot-available --db-snapshot-identifier "$SNAP"
aws rds describe-db-snapshots --db-snapshot-identifier "$SNAP" \
  --query 'DBSnapshots[0].{status:Status,ev:EngineVersion}'
# Expected: available, <FROM>.x
```

Then 0A steps 2 to 6 and its check blocks, with `rehearsal-ssl-from` copied from production's parameter
group. Teardown, with every delete guarded:

```bash
drop_clone "$CLONE"
drop_snapshot "${CLONE}-final"
drop_snapshot "$SNAP"
for ARN in $(aws rds describe-db-instance-automated-backups \
  --query "DBInstanceAutomatedBackups[?DBInstanceIdentifier=='${CLONE}'].DBInstanceAutomatedBackupsArn" \
  --output text); do
  aws rds delete-db-instance-automated-backup --db-instance-automated-backups-arn "$ARN"
done
aws rds delete-db-parameter-group --db-parameter-group-name rehearsal-ssl-from
aws rds delete-db-parameter-group --db-parameter-group-name rehearsal-ssl-to
```

`drop_snapshot "$SNAP"` is the call that matters here. `$SNAP` is a snapshot of production, and the
newest manual snapshot is what an accidental instance delete falls back to, so a mistyped identifier at
this step can remove a real recovery point. The guard accepts only `rehearsal`-scoped names, which is
why the source snapshot is named that way.

#### Whether 0B ran or not

```bash
# Confirm no rehearsal artefact survives into phase 1, in either case
aws rds describe-db-instances \
  --query 'DBInstances[?contains(DBInstanceIdentifier,`rehearsal`)].DBInstanceIdentifier' \
  --output text
aws rds describe-db-snapshots --snapshot-type manual \
  --query 'DBSnapshots[?contains(DBSnapshotIdentifier,`rehearsal`)].DBSnapshotIdentifier' \
  --output text
aws rds describe-db-parameter-groups \
  --query 'DBParameterGroups[?contains(DBParameterGroupName,`rehearsal`)].DBParameterGroupName' \
  --output text
# Expected: empty for all three
```

### Phase 0C: dry-run the plan gate

`terraform plan` is read-only, so the B4 gate can run days ahead instead of inside the phase 1 window. Running it early turns "the plan looked right" into a decision already made, with time to fix what it finds.

**This phase needs the Terraform edit to exist, uncommitted, in the working tree.** The gate asserts
that the instance shows `update` and that the parameter group is created under a generated name - both
of which are properties of the change, so an unmodified checkout produces "no changes" instead and the
gate proves nothing. Make the edit, run 0C, and leave committing it to the phase 1 branch.

Two useful consequences of leaving it uncommitted:

- `source_revision` is taken from the HEAD commit, so an uncommitted edit does **not** replace
  `null_resource.build_and_deploy`. The 0C plan therefore shows the RDS changes and nothing else,
  which makes the gate easier to read than the phase 1 plan will be.
- Nothing has been pushed, so a gate failure costs only a local edit.

```bash
cd infrastructure/terraform
terraform init -backend-config="backends/${ENV}.hcl" -reconfigure
terraform plan -var-file="environments/${ENV}.tfvars" -out=tfplan-dryrun

terraform show -json tfplan-dryrun | jq -r '
  .resource_changes[] | select(.type=="aws_db_instance")
  | "\(.address) -> \(.change.actions|join(","))"'
# Expected: aws_db_instance.main -> update
# Any create, delete or "create,delete" aborts the work

terraform show -json tfplan-dryrun | jq -r '
  .resource_changes[] | select(.type=="aws_db_parameter_group")
  | "\(.address) -> \(.change.actions|join(","))"'
# Expected: one create and one delete - the create_before_destroy replacement

terraform show -json tfplan-dryrun | jq -r '
  .resource_changes[] | select(.type=="aws_db_parameter_group"
    and (.change.actions|index("create")))
  | {name: .change.after.name, name_prefix: .change.after.name_prefix,
     family: .change.after.family}'
# Expected: name null (known after apply), name_prefix set, family mysql<TO>.
# A literal name means B1 is unfixed and the apply will fail.
```

0C cannot prove the replacement succeeds - a name collision only surfaces at apply time.

### Phase 1: apply to development

Weekdays, while the environment is up, and finishing before the scheduled stop (B3, B4).

```bash
# 1. Hold off the scheduler. The override expires by itself.
aws lambda invoke --function-name "${ENV}-dev-scheduler" \
  --payload '{"action":"override","hours":6}' \
  --cli-binary-format raw-in-base64-out /dev/stdout

# 2. The instance must be there. Anything but available aborts (B4).
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{status:DBInstanceStatus,ev:EngineVersion}'
# Expected: available, <FROM>.x

# 3. Manual snapshot, in addition to the automated retention. A manual snapshot is the
#    only recovery point that survives an instance delete, so this is not optional and
#    it is not complete until it reads available.
SNAP=${ENV}-pre-upgrade-$(date +%Y%m%d-%H%M)
aws rds create-db-snapshot --db-instance-identifier "$DB" --db-snapshot-identifier "$SNAP"
aws rds wait db-snapshot-available --db-snapshot-identifier "$SNAP"
aws rds describe-db-snapshots --db-snapshot-identifier "$SNAP" \
  --query 'DBSnapshots[0].{id:DBSnapshotIdentifier,status:Status,ev:EngineVersion,
           created:SnapshotCreateTime}'
# Expected: available, <FROM>.x. This is the rollback point - do not proceed on a
# snapshot still reported as creating.

# 4. Retain an outgoing-family parameter group for rollback. The apply destroys the
#    managed one, and the outgoing engine cannot use the new family.
aws rds copy-db-parameter-group \
  --source-db-parameter-group-identifier "${ENV}-optinist-ssl" \
  --target-db-parameter-group-identifier "${ENV}-optinist-ssl-rollback" \
  --target-db-parameter-group-description "outgoing-family rollback target"

#    Verify it. A rollback attaches this group, so a copy that silently did not happen
#    is only discovered during the restore - after the restore has been committed.
aws rds describe-db-parameter-groups \
  --db-parameter-group-name "${ENV}-optinist-ssl-rollback" \
  --query 'DBParameterGroups[0].{name:DBParameterGroupName,family:DBParameterGroupFamily}'
# Expected: the name, and family mysql<FROM>

aws rds describe-db-parameters --db-parameter-group-name "${ENV}-optinist-ssl-rollback" \
  --source user \
  --query 'Parameters[].{name:ParameterName,value:ParameterValue}' --output table
# Expected: the same user-set parameters as the live group - require_secure_transport
# and time_zone. A copy that lost them restores an instance that rejects the
# application's TLS-only connections.

# 5. Clean worktree (B6), then plan to a file
git status --porcelain
# Expected: empty
cd infrastructure/terraform
terraform init -backend-config="backends/${ENV}.hcl" -reconfigure
terraform plan -var-file="environments/${ENV}.tfvars" -out=tfplan
```

Run the same two gate assertions as phase 0C against `tfplan`, then:

```bash
terraform apply tfplan

# Confirm the upgrade happened rather than being queued (B2)
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{ev:EngineVersion,status:DBInstanceStatus,
           pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus,pending:PendingModifiedValues}'
# Expected: <TO>.x, available, in-sync, pending == {}

# The scheduler Lambda must now point at the renamed parameter group (B1)
aws lambda get-function-configuration --function-name "${ENV}-dev-scheduler" \
  --query 'Environment.Variables.RDS_PARAMETER_GROUP_NAME'
# Expected: the name_prefix-generated name, not the old literal

aws rds describe-db-proxy-targets --db-proxy-name "${ENV}-optinist-rds-proxy" \
  --query 'Targets[].{id:RdsResourceId,state:TargetHealth.State,reason:TargetHealth.Reason}'
# Expected: one target, AVAILABLE
```

#### What you can roll back to after this apply

Confirm this list is real before applying, not after. The rollback procedure is in
[Rollback](#rollback); this is what it has to work with.

| Recovery point | Created by | Expires | Restores to |
|-------------------------|-----------------------|-----------------------|-----------------------|
| `<ENV>-pre-upgrade-<date>` | Step 3 above | **Never** - manual snapshots persist until deleted | `<FROM>`, the state immediately before the apply |
| `<ENV>-optinist-ssl-rollback` | Step 4 above | Never | The parameter group that `<FROM>` instance needs |
| Automatic pre-upgrade snapshots | RDS, up to two, immediately before the upgrade | With the retention period | `<FROM>` |
| Point-in-time recovery | The automated backup | See the warning below | Any point in the window |

**On development, point-in-time recovery is a same-day net, not a 35-day one.** The retention period
reads 35, but the instance is recreated every night, so its automated backup window only spans the
*current* instance's lifetime - typically hours, starting at that morning's restore. Earlier nights
survive as separate retained backups, each covering only its own night. Production, which is never
deleted, does have the full window.

The practical consequence for this phase: **the manual snapshot from step 3 is the only fresh,
full-fidelity recovery point development has.** It is not a belt-and-braces extra on top of a 35-day
net. Treat it accordingly.

**A rollback after a night has passed needs one more thing.** The evening stop replaces the scheduler's
fixed-name snapshot with a `<TO>` one, and the Lambda points at the `<TO>`-family parameter group. So a
rollback must also revert the Lambda's parameter group, or the next nightly restore aims the new family
at an old snapshot - B3 in reverse. The [Rollback](#rollback) procedure's step 4 covers it; it is easy
to miss when the rollback is framed as "undo the apply".

### Phase 2: two nightly destroy and restore cycles

This proves B3. Within each cycle, check 1 runs in the evening after the stop; the rest need the morning restore.

```bash
# 1. The evening snapshot must itself be on the new version
aws rds describe-db-snapshots --db-snapshot-identifier "${DB}-dev-scheduler" \
  --query 'DBSnapshots[0].{ev:EngineVersion,status:Status,created:SnapshotCreateTime}'
# Expected: <TO>.x, available, tonight's timestamp.
# Still <FROM> means the upgrade never completed.

# 2. The morning restore
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{ev:EngineVersion,status:DBInstanceStatus,
           pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus,pending:PendingModifiedValues,
           retention:BackupRetentionPeriod}'
# Expected: <TO>.x, available, in-sync, pending == {}, retention unchanged.
# Retention is the one property the snapshot chain can lose - see edge case 4.

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

# 5. The proxy target re-registered against the new DbiResourceId
aws rds describe-db-proxy-targets --db-proxy-name "${ENV}-optinist-rds-proxy" \
  --query 'Targets[].{id:RdsResourceId,state:TargetHealth.State,reason:TargetHealth.Reason}'
# Expected: one target, AVAILABLE

# 6. The application reaches it through the proxy. Use ssm_sh with the proxy
#    endpoint in place of the clone host.
aws rds describe-db-proxies --db-proxy-name "${ENV}-optinist-rds-proxy" \
  --query 'DBProxies[0].Endpoint' --output text
# Expected: a failure here but not in step 2 is a proxy auth problem, not an engine one
```

Both cycles matter. The first proves the restore works against a new-version snapshot at all; the second proves the steady state, where the snapshot one cycle produces is the snapshot the next consumes.

### Phase 3: soak to exit criteria

**Two nights is the floor and the exit is a checklist, not a date.** Almost nothing this phase protects against is found by elapsed time: the nightly cycle scales with cycles rather than days, an application SQL incompatibility is found by exercising code paths, and a performance regression is not observable on development because it carries no meaningful load.

Every scheduled path that touches the database has a cadence of 24 hours or less, so two nightly cycles covers all of them and a week only repeats the daily ones. See [Configuration](#scheduled-jobs-that-touch-the-database) for the cadences.

| # | Exit criterion |
|---|-----------------------|
| 1 | Phase 2's checks pass on two consecutive cycles, including the retention period |
| 2 | The deployed e2e lanes are green, with skips promoted to failures |
| 3 | The engine-version assertion is green - see [Testing](#testing) |
| 4 | Every scheduled job has run at least once on the new version with no SQL error, by invoking it rather than waiting |
| 5 | The log queries are clean |
| 6 | Production baseline metrics captured - this cannot be done after phase 4 |

Criterion 4, invoking rather than waiting. Each payload is the literal `input` the EventBridge target sends, so a manual invoke reproduces the scheduled invocation.

```bash
inv() { aws lambda invoke --function-name "${ENV}-$1" --payload "$2" \
  --cli-binary-format raw-in-base64-out /dev/stdout | head -c 2000; echo; }
SCHED='"source":"aws.events","detail-type":"Scheduled Event"'

inv free-manager        "{$SCHED,\"detail\":{\"action\":\"monitor\"}}"
inv premium-manager     "{$SCHED,\"detail\":{\"action\":\"monitor\"}}"
inv premium-cleanup     "{$SCHED,\"detail\":{\"action\":\"cleanup\"}}"
inv common-user-manager "{$SCHED,\"detail\":{\"action\":\"manage_users\"}}"
inv cost-tracker   '{}'
inv public-cleanup '{}'
# Expected: a success status per invoke. public-cleanup is otherwise daily,
# so this removes a full day of waiting.
```

The in-process background jobs have no Lambda, so call them directly:

```bash
ssm_sh <<'SH'
set -e
C=$(docker ps --format '{{.Names}}' | grep -m1 -- -background-optinist-cloud-container)
docker exec -w /app "$C" python - <<'PY'
from studio.app.common.core.background.expiration_lifecycle_job import ExpirationLifecycleJob
from studio.app.common.core.background.premium_expiration_sweep_job import PremiumExpirationSweepJob
from studio.app.common.core.background.storage_reconciliation_job import StorageReconciliationJob
from studio.app.common.core.background.cleanup_job import DataCleanupJob
from studio.app.common.core.background.sync_job import PublishedExperimentSyncJob

for job in (ExpirationLifecycleJob, PremiumExpirationSweepJob,
            StorageReconciliationJob, DataCleanupJob, PublishedExperimentSyncJob):
    print(f"== {job.__name__}", flush=True)
    job.run()
PY
SH
# Expected: each job prints its name then completes. ExpirationLifecycleJob runs on a
# 24-hour interval, so this is the invoke that saves the most waiting.
```

Criterion 5, the logs. The first three must be empty; the fourth confirms the invokes above landed.

```bash
SINCE=$(( ($(date +%s) - 24*3600) * 1000 ))

aws logs describe-log-streams \
  --log-group-name "/aws/rds/instance/${DB}/error" \
  --order-by LastEventTime --descending --max-items 3 \
  --query 'logStreams[].{stream:logStreamName,last:lastEventTimestamp}'
# Expected: a recent lastEventTimestamp. Silence means the export broke,
# not that nothing went wrong.

aws logs filter-log-events --log-group-name "/aws/rds/proxy/${ENV}-optinist-rds-proxy" \
  --start-time "$SINCE" \
  --filter-pattern '?"Access denied" ?"authentication" ?ConnectionRefusedError ?1290' \
  --query 'events[].message' --output text
# Expected: empty

for LG in "/ecs/${ENV}-optinist-cloud-taskdef" "/ecs/${ENV}-background-optinist-cloud-taskdef"; do
  echo "== $LG"
  aws logs filter-log-events --log-group-name "$LG" --start-time "$SINCE" \
    --filter-pattern '?OperationalError ?ProgrammingError ?InternalError ?"1064" ?"3065"' \
    --query 'events[].message' --output text
done
# Expected: empty

aws logs filter-log-events --log-group-name "/ecs/${ENV}-background-optinist-cloud-taskdef" \
  --start-time "$SINCE" \
  --filter-pattern '?premium_expiration ?free_manager ?cost_tracker ?storage_tracking' \
  --query 'events[].message' --output text | tail -40
# Expected: NOT empty - this shows the criterion 4 jobs ran. An empty result means
# nothing was exercised, not that nothing failed.
```

Do not read the `general` and `slowquery` exports as a signal either way: both logs are at the engine default of off, so they produce nothing before or after an upgrade.

Criterion 6, the baseline. Capture it for production before phase 4; it cannot be taken afterwards.

```bash
for M in CPUUtilization ReadIOPS WriteIOPS DatabaseConnections \
         BufferCacheHitRatio ReadLatency FreeableMemory; do
  echo "== $M"
  aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name "$M" \
    --dimensions "Name=DBInstanceIdentifier,Value=$DB" \
    --start-time "$(date -u -v-14d +%Y-%m-%dT%H:%M:%SZ)" \
    --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --period 86400 --statistics Average Maximum \
    --query 'sort_by(Datapoints,&Timestamp)[].{t:Timestamp,avg:Average,max:Maximum}' \
    --output table
done
# Expected: 14 daily points per metric. BufferCacheHitRatio is the one to watch -
# see edge case 5.
```

### Phase 4: apply to production

No scheduler, so the timing is free, but the instance is offline for the whole upgrade and the same apply rolls every ECS service (B6). Announce using the measured upgrade duration plus the rollout time — 0B's figure if it ran, otherwise 0A's plus a margin.

**If 0B was skipped, the precheck runs here for the first time.** It fails safe: a precheck failure
means the upgrade does not complete and the instance stays on `<FROM>`, so the cost is the window rather
than the data. Decide in advance whether that outcome means reschedule or investigate-in-place, so the
decision is not made under time pressure inside the window.

The sequence is phase 1's, with `ENV` set to the production value. Two steps are restated here rather
than left to the reference, because this is the environment where skipping them is unrecoverable.

```bash
# 1. Manual pre-upgrade snapshot. This is the production rollback point, and the only
#    recovery point that survives an instance delete. Take it and confirm it, every time.
SNAP=${ENV}-pre-upgrade-$(date +%Y%m%d-%H%M)
aws rds create-db-snapshot --db-instance-identifier "$DB" --db-snapshot-identifier "$SNAP"
aws rds wait db-snapshot-available --db-snapshot-identifier "$SNAP"
aws rds describe-db-snapshots --db-snapshot-identifier "$SNAP" \
  --query 'DBSnapshots[0].{id:DBSnapshotIdentifier,status:Status,ev:EngineVersion,
           size:AllocatedStorage,created:SnapshotCreateTime}'
# Expected: available, <FROM>.x. A snapshot still being created is not a recovery point.

# 2. Retain an outgoing-family parameter group, so a rollback has a group to attach
aws rds copy-db-parameter-group \
  --source-db-parameter-group-identifier "${ENV}-optinist-ssl" \
  --target-db-parameter-group-identifier "${ENV}-optinist-ssl-rollback" \
  --target-db-parameter-group-description "outgoing-family rollback target"

# 3. Switch the backend. Forgetting this corrupts the other environment's state.
terraform init -backend-config="backends/${ENV}.hcl" -reconfigure
terraform workspace show; terraform state list | grep -c aws_db_instance
# Expected: the state lists exactly one aws_db_instance, and it is production's
```

Then phase 1's remaining steps: confirm the instance is `available`, confirm the worktree is clean,
plan to a file, pass both gate assertions, and apply.

After the apply, in addition to phase 1's checks:

```bash
# Every service rolled cleanly. All four - the public tier is a separate service
# and is easy to forget.
aws ecs describe-services --cluster "${ENV}-optinist-cloud-cluster" \
  --services "${ENV}-optinist-cloud-service" "${ENV}-premium-optinist-cloud-service" \
             "${ENV}-background-optinist-cloud-service" "${ENV}-public-optinist-cloud-service" \
  --query 'services[].{name:serviceName,status:status,desired:desiredCount,
           running:runningCount,rollout:deployments[0].rolloutState}'
# Expected: ACTIVE, running == desired, COMPLETED for each

aws cloudwatch describe-alarms \
  --alarm-names "${ENV}-optinist-rds-cpu-high" "${ENV}-optinist-rds-connections-high" \
                "${ENV}-optinist-rds-storage-low" \
  --query 'MetricAlarms[].{name:AlarmName,state:StateValue,updated:StateUpdatedTimestamp}'
# Expected: OK for all three. INSUFFICIENT_DATA immediately after the upgrade is
# expected and resolves within a couple of evaluation periods.
```

A service stuck short of its desired count, or an application connection failing while the instance itself is healthy, means a connection pool is holding a dead connection. Force a new deployment on that service. That is a rollout problem, not a rollback trigger.

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

# Extended Support charge gone, at daily granularity
aws ce get-cost-and-usage --granularity DAILY \
  --time-period "Start=$(date -u -v-7d +%Y-%m-%d),End=$(date -u +%Y-%m-%d)" \
  --metrics UnblendedCost \
  --filter '{"Dimensions":{"Key":"USAGE_TYPE","Values":["APN1-ExtendedSupport:Yr1-Yr2:MySQL<FROM>"]}}' \
  --query 'ResultsByTime[].{day:TimePeriod.Start,cost:Total.UnblendedCost.Amount}' --output table
# Expected: falling to zero from the day after the production apply

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

**Deleting the pre-upgrade snapshots and the retained rollback parameter groups closes the rollback
window.** They are the last thing standing between a late-surfacing problem and a restore, so that
deletion is a decision to take deliberately - against the retention duration agreed before phase 1, not
on the day the upgrade looks finished. See [How long the rollback window stays open](#how-long-the-rollback-window-stays-open). Both artefacts are
cheap to keep; neither is cheap to recreate once deleted.

#### The cleanup PR

Three changes, and they can travel together — see the gate below for why that is safe.

| Change | File | Plan effect |
|-------------------------|-----------------------|-----------------------|
| Remove `allow_major_version_upgrade` and `apply_immediately` | `infrastructure/terraform/infrastructure.tf` | **None on `aws_db_instance.main`** — that is the point |
| Update the MySQL client package, if it has fallen behind | `infrastructure/scripts/app_setup.sh` | **Updates `aws_s3_object.app_setup_script`** — the resource carries `etag = filemd5(...)`, so any edit to the script shows up |
| Add the snapshot-restore runbook | `infrastructure/documentation/` | None |

The custom AMI installs its client from a different package and needs nothing.

**Do not assert an empty plan.** It cannot be empty: committing anything changes `source_revision`, so
`null_resource.build_and_deploy` is always replaced (B6). Assert per resource instead, the same way the
Phase 1 and Phase 4 gates do.

```bash
cd infrastructure/terraform
terraform plan -var-file="environments/${ENV}.tfvars" -out=tfplan-cleanup
terraform show -json tfplan-cleanup | jq -r '
  .resource_changes[]
  | select(.change.actions != ["no-op"])
  | "\(.address) -> \(.change.actions|join(","))"'
# Expected, and nothing else:
#   null_resource.build_and_deploy       -> delete,create   (always, B6)
#   aws_s3_object.app_setup_script       -> update          (only if app_setup.sh changed)
#
# aws_db_instance.main must NOT appear. Its absence is the proof that
# allow_major_version_upgrade and apply_immediately were never read back from the API.
# If it appears, stop: something else changed, or the attributes were load-bearing.
```

Because this apply rolls every ECS service anyway (B6), splitting the three changes into separate PRs
buys nothing and costs an extra rollout per PR. Keep them together and rely on the per-resource
assertion.

---

## Rollback

There is no engine downgrade. AWS states it directly: after the upgrade completes, returning to the previous version means restoring a snapshot taken before it.

The snapshot restore is therefore the whole rollback, and it works because **the engine version is a property of the snapshot** - `RestoreDBInstanceFromDBSnapshot` has no engine version parameter. That is the same API fact that causes B3, used in the other direction.

### Recovery points

| Source | Created by | Engine version |
|-------------------------|-----------------------|-----------------------|
| Manual pre-upgrade snapshot | Phase 1 step 3, phase 4 | `<FROM>` |
| Automatic pre-upgrade snapshots | RDS, up to two, when the retention period is greater than zero | `<FROM>` |
| Automated backup retention | Continuous | `<FROM>` up to the upgrade |
| Retained rollback parameter group | Phase 1 step 4, phase 4 | Outgoing family |

Verify a snapshot's engine version before relying on it.

### Procedure

**This is the live procedure, and it is self-contained.** Phase 0A rehearses it on a throwaway clone;
nothing here requires reading that phase or substituting its variable names. Read this section only.

Five steps. Do not skip step 0 - a rollback that starts by deleting the evidence cannot be diagnosed.

```bash
# Set these once. ENV is the Terraform var.environment value for the environment in trouble.
export AWS_REGION=ap-northeast-1
export ENV=<the environment in trouble>
DB=${ENV}-optinist-cloud-rds
ROLLBACK_PG=${ENV}-optinist-ssl-rollback
```

**Step 0. Choose the restore source, and confirm it.** The manual pre-upgrade snapshot from phase 1 or
phase 4 is the primary one; RDS's automatic pre-upgrade snapshots are the fallback.

```bash
aws rds describe-db-snapshots --db-instance-identifier "$DB" --snapshot-type manual \
  --query 'reverse(sort_by(DBSnapshots[?contains(DBSnapshotIdentifier,`pre-upgrade`)],
           &SnapshotCreateTime))[].{id:DBSnapshotIdentifier,ev:EngineVersion,
           status:Status,created:SnapshotCreateTime}' --output table
# Expected: the phase 1 or phase 4 snapshot, available, on <FROM>.
# Nothing here means the rollback window was closed - see below.

aws rds describe-db-snapshots --db-instance-identifier "$DB" --snapshot-type automated \
  --query 'reverse(sort_by(DBSnapshots[?starts_with(EngineVersion,`<FROM>`)],
           &SnapshotCreateTime))[:3].{id:DBSnapshotIdentifier,ev:EngineVersion,
           created:SnapshotCreateTime}' --output table
# The fallback. Prefer the manual snapshot: it is the one whose contents are known.

RESTORE_FROM=<the chosen snapshot id>

# The parameter group the restored instance will attach. An outgoing-version instance
# cannot use the new family, so this must exist and be on the old one.
aws rds describe-db-parameter-groups --db-parameter-group-name "$ROLLBACK_PG" \
  --query 'DBParameterGroups[0].{name:DBParameterGroupName,family:DBParameterGroupFamily}'
# Expected: family mysql<FROM>. If this is missing, stop and create it by copying any
# surviving outgoing-family group before going further - the restore in step 2 fails on
# this argument otherwise, after it has already been committed.
```

**Step 1. Stop application traffic, and preserve the evidence.** Scale the services to zero so nothing
writes to an instance that is about to be replaced.

**Scaling to zero does not hold on its own.** The manager Lambdas re-scale resources on their own
schedules - `free-manager` every five minutes, `premium-manager` every fifteen - so a service set to
zero comes back before the restore finishes. Disable their EventBridge rules first. This is exactly what
the development scheduler's own stop path does, and for the same reason; the rule names are in its
`SCHEDULE_RULE_NAMES` and `DELAYED_RULE_NAMES` environment variables.

```bash
# Record the current state before changing any of it
aws ecs describe-services --cluster "${ENV}-optinist-cloud-cluster" \
  --services "${ENV}-optinist-cloud-service" "${ENV}-premium-optinist-cloud-service" \
             "${ENV}-background-optinist-cloud-service" "${ENV}-public-optinist-cloud-service" \
  --query 'services[].{name:serviceName,desired:desiredCount}' --output table
# Expected: note these counts. Step 5 restores them, and they are not all necessarily 1.

# Disable the rules that would re-scale what step 2 is about to replace
RULES="${ENV}-free-manager-schedule ${ENV}-cost-tracker-schedule \
       ${ENV}-premium-manager-schedule ${ENV}-premium-cleanup-schedule"
for R in $RULES; do
  aws events disable-rule --name "$R" && echo "disabled $R"
done
aws events describe-rule --name "${ENV}-premium-manager-schedule" --query State
# Expected: DISABLED. Confirm the whole list, not just this one. Check the live rule
# names against the scheduler Lambda's environment variables - this list is the
# expected spelling, and a rule missed here is a service that scales back up mid-restore.

for S in "${ENV}-optinist-cloud-service" "${ENV}-premium-optinist-cloud-service" \
         "${ENV}-background-optinist-cloud-service" "${ENV}-public-optinist-cloud-service"; do
  aws ecs update-service --cluster "${ENV}-optinist-cloud-cluster" --service "$S" \
    --desired-count 0 --query 'service.{name:serviceName,desired:desiredCount}'
done
# Expected: desired 0 for all four, and still 0 a few minutes later. If one has come
# back, a rule is still enabled.

# Read the restore parameters off the instance while it still exists
read -r CLASS STORAGE SUBNET SG <<<"$(aws rds describe-db-instances \
  --db-instance-identifier "$DB" --output text \
  --query 'DBInstances[0].[DBInstanceClass,StorageType,DBSubnetGroup.DBSubnetGroupName,
           VpcSecurityGroups[0].VpcSecurityGroupId]')"
echo "$CLASS $STORAGE $SUBNET $SG"
# Expected: four non-empty values. They are needed in step 2 and unavailable afterwards.
```

**Step 2. Free the identifier, then restore under it.** Restoring onto the original identifier is what
keeps Terraform convergent and leaves the ARN and endpoint unchanged, so nothing else in the stack needs
touching. The alternative - restore to a temporary identifier, verify, then rename with
`modify-db-instance --new-db-instance-identifier` - keeps the broken instance available for diagnosis at
the cost of an extra rename and reboot.

```bash
date -u
# The final snapshot here IS the diagnostic copy of the broken instance. Keep the
# automated backups too: this is a live instance, not a rehearsal clone.
aws rds delete-db-instance --db-instance-identifier "$DB" \
  --final-db-snapshot-identifier "${DB}-broken-$(date +%Y%m%d-%H%M)" \
  --no-delete-automated-backups
aws rds wait db-instance-deleted --db-instance-identifier "$DB"

aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier "$DB" --db-snapshot-identifier "$RESTORE_FROM" \
  --db-instance-class "$CLASS" --storage-type "$STORAGE" --port 3306 \
  --db-subnet-group-name "$SUBNET" --vpc-security-group-ids "$SG" \
  --db-parameter-group-name "$ROLLBACK_PG" \
  --no-publicly-accessible --no-multi-az
aws rds wait db-instance-available --db-instance-identifier "$DB"
date -u

aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{ev:EngineVersion,endpoint:Endpoint.Address,rid:DbiResourceId,
           pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus,
           retention:BackupRetentionPeriod}'
# Expected: EngineVersion back at <FROM>.x, the original endpoint hostname, a NEW
# DbiResourceId, the rollback parameter group in-sync. A changed endpoint means the
# restore did not land on the original identifier - stop and rename before continuing.
```

**Step 3. Re-register the RDS Proxy target.** The restored instance has a new `DbiResourceId` and the
proxy does not pick up a replacement on its own. On development the next scheduler start would do this
through `ensure_rds_proxy_target()`, but a rollback should not wait until morning.

```bash
aws rds register-db-proxy-targets --db-proxy-name "${ENV}-optinist-rds-proxy" \
  --target-group-name default --db-instance-identifiers "$DB"
aws rds describe-db-proxy-targets --db-proxy-name "${ENV}-optinist-rds-proxy" \
  --query 'Targets[].{id:RdsResourceId,state:TargetHealth.State,reason:TargetHealth.Reason}'
# Expected: one target, AVAILABLE. REGISTERING for a minute or two is normal.
```

**Step 4. Revert the Terraform change and re-converge.** Keep `apply_immediately = true` in place for
this apply so the parameter group swap is not deferred.

```bash
# git revert is clean only while the upgrade commit is the newest change to
# infrastructure.tf. Check before using it.
UPGRADE_COMMIT=$(git log -1 --format=%H -- infrastructure/terraform/infrastructure.tf)
git show --stat "$UPGRADE_COMMIT"
# Expected: only the engine version and parameter group family lines. If it carries
# application changes, or if application commits have landed since, do NOT revert -
# reverting them too would ship an unrelated rollback through the same apply (B6).
# Edit the two lines back by hand on a branch off current HEAD instead.
git revert --no-edit "$UPGRADE_COMMIT"

cd infrastructure/terraform
terraform init -backend-config="backends/${ENV}.hcl" -reconfigure
terraform plan -var-file="environments/${ENV}.tfvars" -out=tfplan-rollback
terraform show -json tfplan-rollback | jq -r '
  .resource_changes[] | select(.type=="aws_db_instance")
  | "\(.address) -> \(.change.actions|join(","))"'
# Expected: update, or no change at all. A create or delete here would destroy the
# instance just restored, so anything else aborts.
terraform apply tfplan-rollback
```

**Step 5. Bring traffic back, and close the loop on the scheduler.**

```bash
# Restore the counts recorded in step 1, not blindly 1
for S in "${ENV}-optinist-cloud-service" "${ENV}-premium-optinist-cloud-service" \
         "${ENV}-background-optinist-cloud-service" "${ENV}-public-optinist-cloud-service"; do
  aws ecs update-service --cluster "${ENV}-optinist-cloud-cluster" --service "$S" \
    --desired-count <the count recorded in step 1> --force-new-deployment >/dev/null
done

# Re-enable every rule disabled in step 1. Leaving one disabled silently stops
# autoscaling, premium assignment cleanup or cost metrics, with no alarm for it.
for R in $RULES; do
  aws events enable-rule --name "$R" && echo "enabled $R"
done
for R in $RULES; do
  aws events describe-rule --name "$R" --query '[Name,State]' --output text
done
# Expected: ENABLED for every one

aws ecs describe-services --cluster "${ENV}-optinist-cloud-cluster" \
  --services "${ENV}-optinist-cloud-service" "${ENV}-premium-optinist-cloud-service" \
             "${ENV}-background-optinist-cloud-service" "${ENV}-public-optinist-cloud-service" \
  --query 'services[].{name:serviceName,desired:desiredCount,running:runningCount,
           rollout:deployments[0].rolloutState}'
# Expected: running == desired, COMPLETED, at the recorded counts

# Development only, and the step most easily missed: the scheduler must point back at an
# outgoing-family group, or tonight's restore aims the new family at an old snapshot -
# B3 in reverse, and the environment does not come up in the morning.
PG=$(aws lambda get-function-configuration --function-name "${ENV}-dev-scheduler" \
  --query 'Environment.Variables.RDS_PARAMETER_GROUP_NAME' --output text)
aws rds describe-db-parameter-groups --db-parameter-group-name "$PG" \
  --query 'DBParameterGroups[0].DBParameterGroupFamily'
# Expected: mysql<FROM>. Note that the scheduler's own fixed-name snapshot is still on
# <TO> until the next evening stop regenerates it.
```

Then confirm the application actually reaches the restored database through the proxy, using the SQL
block from the shared helper section with the proxy endpoint as the host. `SELECT VERSION()` must report
`<FROM>`.

### How long the rollback window stays open

A rollback days or weeks after the apply is possible, and the mechanism does not degrade: **a manual
snapshot never expires**, and restoring one always produces an instance on the version the snapshot was
taken at. Nothing about the restore gets harder with time.

What closes the window is not time but housekeeping. Two artefacts have to survive, and phase 5's
cleanup is what removes them:

| Artefact | Needed for | Removed by |
|-------------------------|-----------------------|-----------------------|
| `<ENV>-pre-upgrade-<date>` | The restore source | Phase 5 inventory |
| `<ENV>-optinist-ssl-rollback` | The group the restored instance attaches | Phase 5 inventory |

So **agree a retention duration before phase 1, and do not run phase 5's deletions until it has
elapsed.** Phase 5 lists these rather than deleting them for exactly this reason. Both are close to
free to keep: a snapshot of a small database, and a parameter group, which costs nothing.

Four things do change with elapsed time, none of them blocking:

1. **The data loss grows.** A rollback discards everything written since the snapshot. On development a
   week means a week of everyone's test data, which is a coordination question rather than a technical
   one - announce it rather than discovering it.
2. **`git revert` stops being the clean path.** It only works while the upgrade commit is the newest
   change to `infrastructure.tf`. After a week, application commits will have landed, so revert the two
   lines by hand on a branch off current HEAD instead. The Rollback procedure above says this; it
   becomes the normal case rather than the exception.
3. **Terraform state has moved on.** Other applies may have happened, so gate the rollback plan exactly
   as the forward apply was gated - assert `update` on the instance, and abort on create or delete.
4. **Engine versions are eventually deprecated by AWS.** Restoring a snapshot of a version RDS no longer
   offers can be forced to a newer version or refused outright. This is a months-and-years concern, not
   a weeks one, but it is the reason a retained snapshot is not a permanent rollback guarantee.

On development specifically, a late rollback also has to put the nightly cycle back on the outgoing
version: restore under the real identifier, revert the Terraform change so the Lambda's parameter group
follows, and let the next evening's stop regenerate the scheduler snapshot at the old version. Until
that stop runs, the fixed-name scheduler snapshot is still on the new version.

### Cost of a rollback

- **Time:** the restore duration measured in phase 0A, plus proxy re-registration and the Terraform revert.
- **Data:** everything written after the snapshot. During the upgrade itself the database is offline, so the exposure is near zero if the problem is caught immediately. If it surfaces days into the soak, a rollback discards every write since - acceptable on development, and the real constraint on production.

---

## Edge Case Handling

### 1. A rehearsal clone still references the live parameter group

**Problem:** A clone attached to the live parameter group makes Terraform's destroy of that group fail with `InvalidDBParameterGroupState` during the upgrade apply. Any parameter experiment on the clone also lands on the live instance.

**Solution:**
- Give every clone its own copy of the parameter group, made with `copy-db-parameter-group`.
- Tear all clones down before the apply, and assert that none survive.

### 2. A clone outlives the rehearsal and bills overnight

**Problem:** The scheduler's stop path deletes only the identifier in its own configuration, so a differently named clone is not removed at the scheduled stop and keeps billing through nights and weekends.

**Solution:**
- Delete the clone by hand, the same day it is created.
- Finish before the scheduled stop for a second reason: the stop recreates the nightly snapshot under a fixed identifier, so it must delete the existing one first, and a restore still reading that snapshot can block the deletion and make the stop fail.

### 3. The parameter group destroy fails after a successful upgrade

**Problem:** Terraform destroys the outgoing parameter group after the instance update completes. If RDS still reports it as in use, the destroy fails with `InvalidDBParameterGroupState`.

**Solution:**
- Re-run the apply. Nothing is lost in that state - the instance is already on the new group.
- If it persists, check for a clone or a manually created instance still referencing the group.

### 4. The snapshot chain loses the backup retention period

**Problem:** `BackupRetentionPeriod` is not a restore parameter; it carries over in practice but is not guaranteed. A zero retention means RDS takes no automatic pre-upgrade snapshot, so a rollback silently loses a recovery point.

**Solution:**
- Assert the retention period in every phase 2 cycle check, not just once.
- It does not decay gradually - if the chain loses it, it reads zero at the first cycle, which is why two cycles settle the question.

### 5. The database is slower after the upgrade

**Problem:** A family change can alter InnoDB memory and IO defaults. Where RDS stopped pinning a buffer pool size in favour of a derived value, the pool can shrink, so more reads reach disk.

**Solution:**
- The symptom is slower, not wrong, and it is attributable: it appears as `BufferCacheHitRatio` falling with `ReadIOPS` rising, not as an error.
- Pin the buffer pool size explicitly in the new parameter group if it appears.
- Compare against the phase 3 baseline. A change in any other metric is not explained by the family change and should be triaged as an application question.

### 6. A failure during the soak is hard to attribute

**Problem:** Application bug or upgrade artefact?

**Solution:**
- Ask whether it reproduces locally first. The docker-compose stacks pin the same major version the deployed instances are being moved to, so a failure that reproduces locally is not an upgrade artefact.
- The one place local does not mirror RDS is `sql_mode`: the containers use the upstream default while RDS runs the system default. That divergence exists identically before and after an upgrade, so it does not change attribution.

---

## Monitoring and Metrics

### Log groups

| Log group | Contents |
|-------------------------|-----------------------|
| `/aws/rds/instance/<ENV>-optinist-cloud-rds/error` | RDS error log. Must still receive data after a family change |
| `/aws/rds/proxy/<ENV>-optinist-rds-proxy` | Proxy logs. Watch for authentication failures and refused connections |
| `/aws/lambda/<ENV>-dev-scheduler` | Nightly stop and start. Where `InvalidParameterCombination` appears if B3 bites |
| `/ecs/<ENV>-optinist-cloud-taskdef` | Free-tier application logs |
| `/ecs/<ENV>-background-optinist-cloud-taskdef` | Background job logs |

The `general` and `slowquery` exports produce nothing because both logs are at the engine default of off. Their silence is not a regression.

### Metrics to compare against the baseline

| Metric | Why | Expected after upgrade |
|-------------------------|-----------------------|-----------------------|
| `BufferCacheHitRatio` | The family change's one measurable effect | May fall - see edge case 5 |
| `ReadIOPS` | Corroborates the above | May rise with a smaller buffer pool |
| `CPUUtilization` | Baseline comparison | Unchanged |
| `DatabaseConnections` | Pool health after the rollout | Returns to the pre-upgrade level |
| `FreeableMemory` | Pool sizing sanity | Unchanged |

### Alarms

`<ENV>-optinist-rds-cpu-high`, `<ENV>-optinist-rds-connections-high` and `<ENV>-optinist-rds-storage-low` must return to OK after an apply. `INSUFFICIENT_DATA` immediately afterwards is expected.

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
| `auto_minor_version_upgrade` | `aws_db_instance.main` | Already enabled. Why a major-only version works |
| `backup_retention_period` | `aws_db_instance.main` | Must be greater than zero for automatic pre-upgrade snapshots |

### Scheduler environment variables

| Variable | Purpose |
|-------------------------|-----------------------|
| `RDS_PARAMETER_GROUP_NAME` | The group a nightly restore attaches. Must follow B1's rename |
| `RDS_SNAPSHOT_ID` | Fixed identifier for the nightly snapshot |
| `RDS_SUBNET_GROUP_NAME` | Passed on every restore |
| `RDS_SECURITY_GROUP_IDS` | Passed on every restore |

### Scheduled jobs that touch the database

The longest cadence is 24 hours, which is why two nightly cycles covers every scheduled path.

| Cadence | Jobs |
|-------------------------|-----------------------|
| 5 minutes | `free-manager`, `PublishedExperimentSyncJob` |
| 10 minutes | `common-user-manager` |
| 15 minutes | `premium-manager` |
| 1 hour | `premium-cleanup`, `cost-tracker`, `DataCleanupJob`, `StorageReconciliationJob`, `PremiumExpirationSweep` |
| Daily | `public-cleanup`, `dev-scheduler` |
| 24 hours | `ExpirationDeletion` - the longest |
| Monthly | Image Builder pipeline - builds an AMI, never touches the database |

Intervals for the in-process jobs are in `studio/app/common/core/subscription/constants.py`; the Lambda cadences are the EventBridge rules in `infrastructure/terraform/`.

---

## Testing

**The backend pytest lane does not reach the deployed database.** `make test_backend` runs against a containerised MySQL, as do `make alembic_check`, `make premium_lock_it` and `make workflow_count_it`. They validate the code against the target major version, which CI already does continuously. They are regression cover, not upgrade verification.

Only the deployed e2e lanes reach the upgraded instance, and they reach it through the RDS Proxy - the same path every ECS task uses for `DB_HOST`. That makes them evidence for the proxy authentication chain as well as for the queries.

| Lane | What it covers on a deployed environment |
|-------------------------|-----------------------|
| `17-aws-health` | Read-only. Instance availability, alarms, encrypted connection, and real SQL reads through the proxy. Can be pointed at production |
| `15-premium-aws` | The only hand-written SQL in the codebase - the advisory lock calls |
| `16-storage-aws` | Storage aggregation against the deployed database |
| Browser specs | The application paths, with skips promoted to failures |

Three properties of the suite to know before running it:

- Two lanes are localhost-only by design and cannot reach a deployed environment.
- One subscription lane writes to the shared development database without an opt-in flag. Decide deliberately whether a soak includes it.
- The disruptive lane refuses to run when another account has been active recently, and refuses when it cannot tell. That refusal is usually the correct outcome on a shared environment.

### The engine-version assertion

Nothing in the health lane checked the engine version, the parameter group or `PendingModifiedValues` before this procedure existed, which meant the B2 failure state - instance on the old version with an upgrade queued - was invisible to the suite. The assertion added for it belongs in `frontend/e2e/17-aws-health.spec.ts` beside the other storage and database cases, and checks:

- the engine version matches the target major version
- the parameter group is in sync and on the target family
- `PendingModifiedValues` is empty
- the version the proxy serves agrees with the version the control plane reports, which is the split-brain B2 leaves behind
- `sql_mode` is unchanged

It deliberately does not assert a buffer pool size: that value is derived from instance memory and is expected to change across a family move, so pinning a number would turn a documented consequence into a failing test.

---

## Key Functions Reference

| Function | File | Purpose during an upgrade |
|-------------------------|-----------------------|-----------------------|
| `restore_rds()` | `infrastructure/terraform/dev_scheduler_package/dev_scheduler.py` | The reference for which values a restore must pass explicitly. Passes no engine version, which is B3 |
| `stop_rds()` | Same | Deletes only its configured identifier, which is why clones survive the night |
| `ensure_rds_proxy_target()` | Same | Re-registers the proxy target after a restore. The reason development recovers automatically and production does not |
| `runShellOverSsm()` | `frontend/e2e/helpers.ts` | The in-VPC command mechanism `ssm_sh` reproduces |
| `runSql()` | Same | SQL through the proxy over TLS. Read-only off the local stack |

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

First applied for MySQL 8.0 to 8.4 under issue #877, whose child issues carry the per-phase execution records.
