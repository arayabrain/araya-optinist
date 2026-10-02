# RDS Engine Upgrade: MySQL Major Version Procedure

## Executive Summary

- **In-place `ModifyDBInstance` upgrade** of the RDS instance in each environment. No parallel instance, no blue/green.
- **The engine change is a few lines of Terraform.** The procedure exists because the surrounding stack does not tolerate those lines without preparation: six constraints, B1 to B6, recur on every major upgrade.
- **Development is destroyed and restored nightly**, so its upgrade is a same-day operation and the nightly cycle is a mandatory check.
- **Rollback is a snapshot restore.** There is no engine downgrade.
- **Rehearse first.** Phase 0 upgrades and rolls back a throwaway clone before any live instance is touched.

| Phase | Target | Live impact | When |
|---|---|---|---|
| 0A | Throwaway clone of development | None | Any time; step 3 before the scheduled stop |
| 0B | Throwaway clone of production (conditional) | Creates one snapshot of production | Only if its gate says run |
| 0C | Development Terraform state | None, read-only | Days ahead of phase 1 |
| 1 | Development instance | Offline for the upgrade; on the `develop-main` lineage also an ECS rollout | A weekday, finishing before the scheduled stop |
| 2 | Development instance | None beyond the nightly cycle | The night after phase 1 |
| 3 | Development instance, plus a read of production metrics | Test and job traffic | Until the exit criteria are met |
| 4 | Production instance | Offline for the upgrade | The release window |
| 5 | Both | None | After the agreed rollback retention |

Placeholders: `<FROM>` is the current major version, `<TO>` the target, `<ENV>` the Terraform `var.environment` value. First applied for MySQL 8.0 to 8.4 under issue #877; the per-phase execution records are on its child issues #896 (phase 0), #897 (phases 1 to 3) and #898 (phases 4 and 5).

---

## The six constraints

| | Constraint | Handled by |
|---|---|---|
| **B1** | `aws_db_parameter_group` has `family` and `name` both ForceNew. With `create_before_destroy` and a fixed `name`, the replacement collides with the original (`DBParameterGroupAlreadyExists`) | `name_prefix` instead of `name`. Every consumer derives the name from `aws_db_parameter_group.main.name`: the instance always, and on environments with the scheduler also the Lambda's `RDS_PARAMETER_GROUP_NAME` and its IAM policy (`dev_schedule.tf`, under `count = var.enable_dev_schedule ? 1 : 0`) |
| **B2** | `apply_immediately` defaults to false, so the upgrade is queued in `PendingModifiedValues` for the maintenance window, while Terraform reports success. On development the scheduler deletes the instance before that window, and the upgrade is lost | `apply_immediately = true` for the upgrade apply. Phase 1 step 11 asserts `PendingModifiedValues` is empty |
| **B3** | The scheduler's `restore_rds()` passes a parameter group but no engine version, so a `<FROM>` snapshot restored against a `<TO>`-family group fails with `InvalidParameterCombination` | Ordering: the group swap and the engine upgrade land in one `ModifyDBInstance`, and the instance is `available` on `<TO>` before that day's scheduled stop |
| **B4** | Applying while the scheduler has deleted the instance recreates it empty, behind the same endpoint | Assert `available` immediately before the apply, and assert from the plan JSON that `aws_db_instance.main` is `update`, never `create` or `delete` |
| **B5** | `engine_lifecycle_support` is only sent on create and restore paths, so declaring it produces a permanent diff | Leave it undeclared. The attribute still reads the Extended Support value afterwards; it is an attribute, not a charge |
| **B6** | The apply may also be an application deploy. **Whether it is depends on the lineage**: on `develop-main`, `deployment.tf` triggers `null_resource.build_and_deploy` on `source_revision` (the HEAD commit), so every apply after a commit rebuilds the image and rolls all four ECS services. The `develop/v1.1.11` lineage has no such trigger, so its apply is the database change alone | Read the trigger from the plan, not from memory (phase 0C). Apply from a clean worktree: `ecr_build_push.sh` refuses a dirty tree and fails *after* the RDS modification is issued. Where the apply does deploy, the window covers the rollout and the branch should carry no unrelated application change |

A `-target` apply avoids the rebuild but skips the scheduler consumers of B1. Do not use it.

### The Terraform change

```diff
 resource "aws_db_parameter_group" "main" {
-  family = "mysql<FROM>"
-  name   = "${local.env_prefix}-ssl"
+  family      = "mysql<TO>"
+  name_prefix = "${local.env_prefix}-ssl-"
   ...
+  # Pinned to what <FROM> was measured to run. Neither family sets these, so a family
+  # diff does not show them. NOT upgrade scaffolding: these stay after the cleanup.
+  parameter { name = "innodb_dedicated_server"      value = "0" }
+  parameter { name = "innodb_buffer_pool_size"      value = "{DBInstanceClassMemory*3/4}" }
+  parameter { name = "innodb_buffer_pool_instances" value = "8" }
+  parameter { name = "innodb_redo_log_capacity"     value = "2147483648" }
+  parameter { name = "innodb_io_capacity"           value = "200" }
+  parameter { name = "innodb_io_capacity_max"       value = "2000" }
   lifecycle { create_before_destroy = true }
 }

 resource "aws_db_instance" "main" {
-  engine_version              = "<FROM>"
+  engine_version              = "<TO>"
+  allow_major_version_upgrade = true   # remove once both environments are upgraded
+  apply_immediately           = true   # remove once both environments are upgraded
 }
```

The pins are version-specific. Derive them from phase 0A steps 8 and 9 (the before-and-after readings), not from this example: pin every value that moved and is not wanted. For 8.0 to 8.4 it was six; the reasoning is on #897.

A major-only `engine_version` resolves to the region default for that major, because `auto_minor_version_upgrade` is at its default of true and the provider treats the value as a prefix. Remove `allow_major_version_upgrade` afterwards so a future `engine_version` edit cannot perform a major upgrade silently.

### What changes identity

| Attribute | After the upgrade | After a rollback restore |
|---|---|---|
| ARN, identifier, endpoint, port | Unchanged | Unchanged (identifier-derived) |
| `DbiResourceId` | Unchanged | **Changes.** The RDS Proxy target must be re-registered: automatic on development's next scheduler start, manual on production |
| Parameter group name | **Changes** (generated by `name_prefix`) | Set explicitly on the restore |
| `BackupRetentionPeriod` | Unchanged | Preserved in practice, not guaranteed (edge case 5) |

---

## Session setup

Run this first, in every shell. `ENV` names resources; `TF_ENV` names the `backends/` and `environments/` files. They coincide on development and **differ on production** (`subscr` against `production`), which is why `backends/${ENV}.hcl` works on development and fails at the first terraform command on production.

```bash
setopt interactive_comments 2>/dev/null || true
export AWS_PROFILE=<the profile for this account>
export AWS_REGION=ap-northeast-1
export ENV=<the Terraform var.environment value>
export TF_ENV=<the backends/ and environments/ file basename>
export FROM=<the current major version, e.g. 8.0>
export TO=<the target major version, e.g. 8.4>
DB=${ENV}-optinist-cloud-rds
# The live parameter group, read rather than typed: after the first upgrade its name is
# generated, so a literal breaks on the next one. Re-read after any apply.
LIVE_PG=$(aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].DBParameterGroups[0].DBParameterGroupName' --output text)
echo "profile=$AWS_PROFILE env=$ENV tf_env=$TF_ENV from=$FROM to=$TO db=$DB pg=$LIVE_PG"
```

Everything below uses `$FROM`, `$TO`, `$ENV`, `$DB` and `$LIVE_PG`. The remaining hand-substitutions are marked `<...>` where they occur: the 0C checkout path, the other environment's name in 0B's gate 2, the apply's settle time in criterion 5, the production identifiers in criterion 6, the snapshot to restore in a rollback, and the `COUNTS` and `RULES` values redeclared in rollback step 5.

**zsh and pasted comments.** The `setopt` line is first because zsh does not treat `#` as a comment in interactive input by default: a trailing comment becomes arguments (`VAR=x  # note` leaves `VAR` unset), and a comment inside a `case` is a parse error that silently abandons the function definition. Keep comments on their own line, and keep them out of function bodies.

### Helpers

Shell functions do not survive a new terminal. Paste all of them again after one, then check with `declare -f drop_clone`.

**`ssm_sh`: run a script inside the VPC.** Neither instance is publicly reachable and the proxy is TLS-only, so database checks run on the background EC2 instance over SSM. The body deliberately carries no comments.

```bash
ssm_sh() {
  local iid cid st tmp
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
    st=$(aws ssm get-command-invocation --command-id "$cid" --instance-id "$iid" \
      --query Status --output text)
    case "$st" in
      Success) break ;;
      Pending|InProgress|Delayed) sleep 3 ;;
      *) aws ssm get-command-invocation --command-id "$cid" --instance-id "$iid" \
           --query StandardOutputContent --output text
         aws ssm get-command-invocation --command-id "$cid" --instance-id "$iid" \
           --query StandardErrorContent --output text >&2; return 1 ;;
    esac
  done
  aws ssm get-command-invocation --command-id "$cid" --instance-id "$iid" \
    --query StandardOutputContent --output text
}
```

**`sql_run <host>`: run SQL from stdin against a host, with the application's credentials.** `sql_readings <host>` is the fixed set of readings used on the clone, the live instance, through the proxy, and after a rollback. The client aborts on the first SQL error and `set -e` discards the block, so if a variable does not exist on one version remove that line from the function, re-paste it, and re-run; every statement is a `SELECT`.

```bash
sql_run() {
  local sql
  [ -n "$1" ] || { echo "sql_run: no host given" >&2; return 1; }
  sql=$(cat)
  ssm_sh <<SH
set -e
CFG=\$(aws secretsmanager get-secret-value --region ${AWS_REGION} \
  --secret-id ${ENV}-optinist/database/config --query SecretString --output text)
export MYSQL_PWD=\$(printf '%s' "\$CFG" | python3 -c 'import json,sys;print(json.load(sys.stdin)["password"])')
DBU=\$(printf '%s' "\$CFG" | python3 -c 'import json,sys;print(json.load(sys.stdin)["username"])')
DBN=\$(printf '%s' "\$CFG" | python3 -c 'import json,sys;print(json.load(sys.stdin)["database"])')
mariadb --ssl -h $1 -u "\$DBU" --connect-timeout=10 "\$DBN" -t <<'SQL'
$sql
SQL
SH
}

sql_readings() {
  sql_run "$1" <<'SQL'
SELECT VERSION();
SELECT @@sql_mode;
SELECT @@innodb_buffer_pool_size, @@innodb_dedicated_server, @@binlog_format, @@log_output;
SELECT @@innodb_io_capacity, @@innodb_io_capacity_max, @@innodb_adaptive_hash_index,
       @@innodb_change_buffering, @@innodb_buffer_pool_instances;
SELECT @@innodb_redo_log_capacity, @@innodb_flush_method, @@innodb_log_writer_threads,
       @@innodb_buffer_pool_chunk_size;
SELECT PLUGIN_NAME, PLUGIN_STATUS FROM information_schema.PLUGINS
 WHERE PLUGIN_NAME LIKE '%native_password%';
SELECT user, host, plugin FROM mysql.user ORDER BY user;
SQL
}
```

| Reading | What it settles |
|---|---|
| `VERSION()` | The upgrade reached the engine, not just `PendingModifiedValues` |
| The connection itself | TLS against a group that requires `require_secure_transport`, and, with `mysql.user` showing the account on `mysql_native_password`, that the plugin still authenticates on `<TO>` |
| `@@sql_mode` | Unchanged across the upgrade |
| The InnoDB values | What the engine default changed underneath. The RDS family diff does not predict most of them (edge case 6) |
| `@@binlog_format` | May change to `ROW`. Harmless with no replicas |

**Delete guards.** The clone and the real instance differ by one variable name in the same shell, and in phase 0B that shell is pointed at production. Two API defaults make a typo worse than it looks: `--delete-automated-backups` defaults to true, and `--skip-final-snapshot` leaves nothing. So nothing in this procedure calls `delete-db-instance` or `delete-db-snapshot` directly except the live rollback. Every identifier the procedure may delete carries `rehearsal` in its name.

```bash
assert_rehearsal() {
  case "$1" in
    *rehearsal*) return 0 ;;
    *) echo "REFUSING: '$1' is not a rehearsal-scoped identifier" >&2; return 1 ;;
  esac
}

confirm_target() {
  local kind="$1" id="$2" reply
  echo "--- about to delete this $kind:"
  case "$kind" in
    instance) aws rds describe-db-instances --db-instance-identifier "$id" \
        --query 'DBInstances[0].{id:DBInstanceIdentifier,ev:EngineVersion,
                 created:InstanceCreateTime,class:DBInstanceClass,
                 pg:DBParameterGroups[0].DBParameterGroupName}' || return 1 ;;
    snapshot) aws rds describe-db-snapshots --db-snapshot-identifier "$id" \
        --query 'DBSnapshots[0].{id:DBSnapshotIdentifier,type:SnapshotType,
                 ev:EngineVersion,created:SnapshotCreateTime,src:DBInstanceIdentifier}' || return 1 ;;
  esac
  printf 'type the identifier to confirm: '
  read -r reply
  [ "$reply" = "$id" ] || { echo "ABORTED: input did not match" >&2; return 1; }
}

drop_clone() {
  assert_rehearsal "$1" || return 1
  confirm_target instance "$1" || return 1
  aws rds delete-db-instance --db-instance-identifier "$1" \
    --final-db-snapshot-identifier "$1-final" --no-delete-automated-backups
  aws rds wait db-instance-deleted --db-instance-identifier "$1"
}

drop_snapshot() {
  assert_rehearsal "$1" || return 1
  confirm_target snapshot "$1" || return 1
  aws rds delete-db-snapshot --db-snapshot-identifier "$1"
}
```

Verify the guards once per session, before phase 0. Never pass a real identifier to `drop_clone` or `drop_snapshot` as a test: if the predicate were broken, the wrapper would go on to describe the real resource and prompt.

```bash
# 1. Nothing real is inside the accept set
aws rds describe-db-instances \
  --query 'DBInstances[?contains(DBInstanceIdentifier,`rehearsal`)].DBInstanceIdentifier' --output text
aws rds describe-db-snapshots --snapshot-type manual \
  --query 'DBSnapshots[?contains(DBSnapshotIdentifier,`rehearsal`)].DBSnapshotIdentifier' --output text
# Expected: empty for both

# 2. The predicate refuses real identifiers
assert_rehearsal "$DB"
assert_rehearsal "$(aws rds describe-db-snapshots --db-instance-identifier "$DB" \
  --snapshot-type manual --query 'DBSnapshots[0].DBSnapshotIdentifier' --output text)"
# Expected: REFUSING twice

# 3. The wrappers consult the predicate: no preview, no prompt, nothing sent to AWS
drop_clone    "guard-test-instance-must-be-refused"
drop_snapshot "guard-test-snapshot-must-be-refused"
# Expected: REFUSING twice. A preview appearing here means the guard is not wired in: abort

# 4. The accept path works. The clone does not exist yet, so confirm_target fails to
#    describe it and returns before prompting.
drop_clone "${ENV}-optinist-rds-upgrade-rehearsal"
# Expected: past assert_rehearsal, then a describe error, no prompt. If the clone
# already exists (a new shell partway through 0A) this reaches the prompt instead:
# answer with anything but the identifier. That is still a pass.
```

**`redact`: sanitise a log before posting it.** The tracking issues are public and editing a comment does not unpublish anything. Anchors use `(^|[^[:alnum:]-])` rather than `\b` because BSD `sed` has no `\b` and silently never matches.

```bash
# Usage: <command> 2>&1 | redact
redact() {
  local acct
  acct=$(aws sts get-caller-identity --query Account --output text)
  sed -E \
    -e "s/${acct}/<ACCOUNT_ID>/g" \
    -e 's/(^|[^[:alnum:]-])(development|subscr)-optinist/\1<ENV>-optinist/g' \
    -e 's/\/ecs\/(development|subscr)-/\/ecs\/<ENV>-/g' \
    -e 's/proxy-[a-z0-9]{8,}/proxy-<TOKEN>/g' \
    -e 's/\.[a-z0-9]{8,}\.([a-z0-9-]+)\.rds\.amazonaws\.com/.<TOKEN>.\1.rds.amazonaws.com/g' \
    -e 's/(^|[^[:alnum:]-])i-[0-9a-f]{8,}/\1i-A/g' \
    -e 's/(^|[^[:alnum:]-])db-[A-Z0-9]{10,}/\1db-A/g' \
    -e 's/db\.[a-z0-9]+\.[a-z]+/<INSTANCE_CLASS>/g'
}
```

| Redact | Keep as measured |
|---|---|
| Environment prefix, account id, endpoint account tokens, instance ids, `DbiResourceId`, instance class and storage sizes | Durations, engine versions, `sql_mode`, precheck findings, metric values, cost figures. The acceptance criteria are written against them |

### Prerequisites

| Requirement | Without it |
|---|---|
| AWS profile for the account | Nothing runs |
| `environments/<TF_ENV>.tfvars` with real values (gitignored; obtain it) | No plan, so no gate |
| `jq` | The plan gate degrades to reading by eye, which B4 exists to prevent |
| Clean toplevel worktree for the applies | The apply fails after the RDS modification is issued (B6) |
| SSM access to the background instance | The in-database checks become manual |

Before starting, read the recovery floor for the environment the next phase operates on. Production's floor is read in phase 4, not earlier: phase 0 touches production only in 0B's gate (one read-only query and one metric) and, if the gate says run, 0B's snapshot.

```bash
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{retention:BackupRetentionPeriod,delProt:DeletionProtection}'
# Expected: retention greater than zero. Zero means no automated backups and no PITR.

aws rds describe-db-instance-automated-backups \
  --query "DBInstanceAutomatedBackups[?DBInstanceIdentifier=='${DB}'].{status:Status,
           from:RestoreWindow.EarliestTime,to:RestoreWindow.LatestTime}" --output table
# Expected: on development, one active row spanning only today's instance (hours, not the
# retention period) plus one retained row per earlier cycle. PITR exists inside those
# windows and nowhere between them. Production has one continuous window.

aws rds describe-db-snapshots --db-instance-identifier "$DB" --snapshot-type manual \
  --query 'reverse(sort_by(DBSnapshots,&SnapshotCreateTime))[].{id:DBSnapshotIdentifier,
           ev:EngineVersion,created:SnapshotCreateTime}' --output table
# Expected: read the newest date. Manual snapshots survive an instance delete, so this is
# what an accidental delete falls back to. If it is old, take one before starting.
```

### Abort conditions

| Condition | Why |
|---|---|
| The plan shows `create`, `delete` or a replacement for `aws_db_instance.main` | B4 |
| The instance is anything but `available` immediately before the apply | B4 |
| Development work will run past the scheduled stop, or it is a night, weekend or holiday | B3, B4 |
| `PrePatchCompatibility.log` reports `Errors` greater than zero | The same failure occurs on the real instance |
| The manual pre-upgrade snapshot has not reached `available` | Not yet a recovery point |
| `git status --porcelain` is non-empty at the toplevel | B6 |
| A rehearsal clone still exists or still references the live parameter group | Edge case 1: the apply cannot destroy the outgoing group |
| The phase 0A drill did not complete | The rollback has no proven recovery point |
| `drop_clone` does not refuse a real identifier when tested | The guard against the most damaging typo is not working |

---

## Phase 0A: rehearse the upgrade and the rollback on a clone of development

Nearly free: the scheduler's nightly snapshot already exists. Nothing live is touched and nothing enters Terraform state. Steps 1 to 11 upgrade the clone and measure it; steps 12 to 14 are the rollback drill, which is the only place the rollback is executed rather than described.

```bash
CLONE=${ENV}-optinist-rds-upgrade-rehearsal
SNAP=${DB}-dev-scheduler
```

Only step 3 reads the nightly snapshot, so only step 3 has to finish before the scheduled stop recreates it. 0A can be split across days after that; the clone keeps billing, and the scheduler does not touch it (it acts on one configured identifier, not on tags). On resuming in a new shell, re-run the session setup and the two lines above, re-paste every helper, and re-read `CLASS STORAGE SUBNET SG` from step 3 and `CLONE_HOST` from step 8. Credentials expire overnight; the shell does not.

#### Step 1: confirm the source snapshot

```bash
aws rds describe-db-snapshots --db-snapshot-identifier "$SNAP" \
  --query 'DBSnapshots[0].{status:Status,ev:EngineVersion,created:SnapshotCreateTime}'
# Expected: available, <FROM>.x. Read the date: on a Monday this is Friday's snapshot.
```

#### Step 2: a private parameter group for the clone

Never attach the live group to a clone (edge case 1).

```bash
aws rds copy-db-parameter-group \
  --source-db-parameter-group-identifier "$LIVE_PG" \
  --target-db-parameter-group-identifier rehearsal-ssl-from \
  --target-db-parameter-group-description "outgoing-family rehearsal copy"

aws rds describe-db-parameters --db-parameter-group-name rehearsal-ssl-from --source user \
  --query 'Parameters[].{name:ParameterName,value:ParameterValue}' --output table
# Expected: the same user-set parameters as the live group. A copy that lost
# require_secure_transport makes every TLS check below pass for the wrong reason.
```

#### Step 3: restore the clone

```bash
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

#### Step 4: confirm what the clone came up as

```bash
aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].{status:DBInstanceStatus,retention:BackupRetentionPeriod,
           rid:DbiResourceId,pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus}'
# Expected: available, retention greater than zero, pg rehearsal-ssl-from, in-sync.
# Retention zero means RDS takes no pre-upgrade snapshot and the drill cannot run.
# While the restore is still creating this reports the DEFAULT group as "applying";
# re-read once available. If it is still the default, attach the group with
# modify-db-instance before continuing. Note rid: step 14 asserts it changed.
```

Retag the clone so cost reports and drift audits do not attribute it to the live instance (edge case 3).

#### Step 5: resolve the target version and build the target parameter group

```bash
read -r TARGET_VERSION TO_FAMILY <<<"$(aws rds describe-db-engine-versions --engine mysql \
  --engine-version "$TO" --default-only --output text \
  --query 'DBEngineVersions[0].[EngineVersion,DBParameterGroupFamily]')"
echo "target=$TARGET_VERSION family=$TO_FAMILY"
# Expected: a concrete x.y.z and mysql<TO>. This is how the provider resolves
# engine_version = "<TO>", so phase 1 lands on the same version. Empty means stop.

aws rds describe-db-engine-versions --engine mysql \
  --engine-version "$(aws rds describe-db-instances --db-instance-identifier "$CLONE" \
    --query 'DBInstances[0].EngineVersion' --output text)" \
  --query "DBEngineVersions[0].ValidUpgradeTarget[?EngineVersion=='${TARGET_VERSION}'].{v:EngineVersion,
           major:IsMajorVersionUpgrade,auto:AutoUpgrade}" --output table
# Expected: one row, IsMajorVersionUpgrade true. Empty means the default is not
# reachable from here: pick from the full ValidUpgradeTarget list instead.

aws rds create-db-parameter-group --db-parameter-group-name rehearsal-ssl-to \
  --db-parameter-group-family "$TO_FAMILY" --description "target-family rehearsal"

# JSON, not the shorthand form: a value such as {DBInstanceClassMemory*3/4} cannot be
# carried by the CLI's shorthand parser.
SRC_JSON=$(aws rds describe-db-parameters --db-parameter-group-name "$LIVE_PG" \
  --source user --query 'Parameters[].[ParameterName,ParameterValue,ApplyType]' --output text \
  | jq -Rn '[inputs | split("\t")
      | {ParameterName: .[0], ParameterValue: .[1],
         ApplyMethod: (if .[2] == "static" then "pending-reboot" else "immediate" end)}]')
printf '%s\n' "$SRC_JSON" | python3 -m json.tool
# Expected: one object per user-set parameter in the live group. Empty means stop.

aws rds modify-db-parameter-group --db-parameter-group-name rehearsal-ssl-to \
  --parameters "$SRC_JSON"
aws rds describe-db-parameters --db-parameter-group-name rehearsal-ssl-to --source user \
  --query 'Parameters[].{name:ParameterName,value:ParameterValue}' --output table
# Expected: the same names and values. A parameter missing here does not exist in the
# new family, which is a finding for the upgrade itself.
```

#### Step 6: upgrade, and record the duration

The family change and the version change go in one `ModifyDBInstance` (B3, rehearsed here).

```bash
date -u
aws rds modify-db-instance --db-instance-identifier "$CLONE" \
  --engine-version "$TARGET_VERSION" --db-parameter-group-name rehearsal-ssl-to \
  --allow-major-version-upgrade --apply-immediately
aws rds wait db-instance-available --db-instance-identifier "$CLONE"
date -u
```

A waiter timeout (30 minutes) is not a failure: do not re-issue the modify, poll `describe-db-instances` until `available` on the new version. Do not take the duration from the wall clock either; RDS reports `available` for a while after `--apply-immediately`, and the waiter lags. Read the event stream:

```bash
aws rds describe-events --source-identifier "$CLONE" --source-type db-instance \
  --duration 120 --query 'Events[].{t:Date,msg:Message}' --output table
```

| From | To | Used for |
|---|---|---|
| `The downtime started` | `DB instance restarted` | The outage users experience: the announcement figure |
| `The downtime started` | `engine major version upgrade complete` | Upper bound on the above |
| `The pre-check started` | the last `Finished DB Instance backup` | The window the operator holds |

#### Step 7: the precheck log

The log only exists after the upgrade has run. List before fetching: filtering on a guessed name produces an empty file that reads like "no problems".

```bash
aws rds describe-db-log-files --db-instance-identifier "$CLONE" \
  --query 'DescribeDBLogFiles[].{name:LogFileName,size:Size,written:LastWritten}' --output table
# Expected: an upgrade-related file (PrePatchCompatibility.log or mysqlUpgrade) with a
# LastWritten after the upgrade. If not, wait and list again.

for f in $(aws rds describe-db-log-files --db-instance-identifier "$CLONE" --output text \
  --query "DescribeDBLogFiles[?contains(LogFileName,'Upgrade')
           || contains(LogFileName,'upgrade')
           || contains(LogFileName,'PrePatch')].LogFileName"); do
  echo "===== $f"
  aws rds download-db-log-file-portion --db-instance-identifier "$CLONE" \
    --log-file-name "$f" --starting-token 0 --output text
done | tee /tmp/prepatch.log
wc -l /tmp/prepatch.log
# Expected: non-zero

grep -E '^(Errors|Warnings|Database Objects Affected):' /tmp/prepatch.log
awk -F': *' '/^Errors:/{print ($2==0 ? "PASS: Errors=0" : "ABORT: Errors=" $2)}' /tmp/prepatch.log
# Expected: PASS. Read the tally, not a grep count of "error": check titles contain it.

grep -nE '^[0-9]+\)|^\tNo issues found' /tmp/prepatch.log
# Expected: every check accounted for. Read the detail under each that is not
# "No issues found" and record what it means for this stack.
```

#### Step 8: the in-database state on the clone

```bash
CLONE_HOST=$(aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].Endpoint.Address' --output text)
sql_readings "$CLONE_HOST"
# Expected: VERSION() <TO>.x, the connection succeeds, sql_mode unchanged. Record every
# InnoDB value; step 9 is the other half of the pair.

aws rds describe-db-parameters --db-parameter-group-name rehearsal-ssl-to \
  --query "Parameters[?ParameterName=='mysql_native_password'].[ParameterValue,ApplyType,IsModifiable]" \
  --output text
# Expected: ON static False. mysql_native_password is a server option, not a system
# variable, so it is read from the group. Empty means the option does not exist in
# this family: a serious finding for the auth path.
```

#### Step 9: the same readings on the live `<FROM>` instance

Only the pair says what changed, and the `<FROM>` half exists only until phase 1. Run any time before it.

```bash
LIVE_HOST=$(aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].Endpoint.Address' --output text)
sql_readings "$LIVE_HOST"
# Expected: VERSION() <FROM>.x. If it is <TO>.x you are pointed at the clone.

for G in "$LIVE_PG" rehearsal-ssl-to; do
  echo "== $G"
  aws rds describe-db-parameters --db-parameter-group-name "$G" \
    --query "Parameters[?ParameterName=='innodb_buffer_pool_size' ||
             ParameterName=='innodb_dedicated_server' ||
             ParameterName=='innodb_io_capacity'].[ParameterName,ParameterValue,Source]" \
    --output text
done
# A formula on one side and nothing on the other is the whole family diff. Empty on
# both sides means the engine derives it, and the readings above are the only evidence.
```

Record the two sets side by side. Every value that moved and is not wanted becomes a pin in the Terraform change (edge case 6).

#### Step 10: alembic through the application's own stack

Every check so far used the `mariadb` client. This runs the application's driver stack (pydantic settings, SQLAlchemy, pymysql, alembic) against the clone, the same `alembic upgrade head` the deploy runs. On a clone at head it is a no-op; what is proved is that the runner connects, authenticates, negotiates TLS and executes.

A `docker exec` sees the container's `DB_*` variables, not the `MYSQL_*` ones the entrypoint exports, so the mapping is repeated inside. The deployed `MYSQL_SERVER` points at the proxy, which fronts the live instance, so the block proves it reached the clone and aborts otherwise before migrating.

```bash
ssm_sh <<SH
set -e
C=\$(docker ps --format '{{.Names}}' | grep -m1 -- -background-optinist-cloud-container)

docker exec -i -e MYSQL_SERVER=$CLONE_HOST -w /app "\$C" sh -s <<'INNER'
set -e
export MYSQL_USER="\$DB_USER" MYSQL_PASSWORD="\$DB_PASSWORD" MYSQL_DATABASE="\$DB_NAME"
[ -n "\$MYSQL_USER" ] && [ -n "\$MYSQL_DATABASE" ] || {
  echo "ABORT: DB_USER / DB_NAME are absent from this container" >&2; exit 1; }

python3 -c "
import sys
from studio.app.common.db.config import DATABASE_CONFIG, get_ssl_creator
from sqlalchemy import create_engine, text
print('MYSQL_SERVER =', DATABASE_CONFIG.MYSQL_SERVER)
creator = get_ssl_creator()
kwargs = {'creator': creator} if creator else {}
engine = create_engine(DATABASE_CONFIG.DATABASE_URL, **kwargs)
with engine.connect() as c:
    v = c.execute(text('SELECT VERSION()')).scalar()
engine.dispose()
print('VERSION()    =', v)
if not v.startswith('${TO}.'):
    sys.exit('ABORT: expected ${TO}.x, got ' + v + ' - this is not the clone')
"

alembic current && alembic heads && alembic upgrade head && alembic current
INNER
SH
# Expected: MYSQL_SERVER is the clone's endpoint, VERSION() is <TO>.x, current and heads
# report the same revision before and after.
# "1045 Access denied for user 'root'": the DB_* mapping did not happen; not an engine problem.
# "ABORT: expected ... got <FROM>.x": the override did not take and this reached the
#   live instance through the proxy. Nothing was migrated.
# No output at all: the container name did not match.
```

If this fails, the fix is an application change in its own blocking issue. Finding it here costs one command; finding it in phase 1 costs a rollback, because ECS reverting to the previous image does not undo the engine upgrade.

#### Step 11: rehearse the parameter pins

Run before the drill: afterwards there is no `<TO>` instance to test against. This proves a formula is accepted on the new family, that the values resolve together, and that `static` pins reach the running server rather than sitting at `pending-reboot`.

```bash
PG=rehearsal-ssl-to
# Derive this list from the step 8 / step 9 pair. It is version-specific.
PINS_JSON='[
  {"ParameterName":"innodb_dedicated_server","ParameterValue":"0","ApplyMethod":"pending-reboot"},
  {"ParameterName":"innodb_buffer_pool_size","ParameterValue":"{DBInstanceClassMemory*3/4}","ApplyMethod":"pending-reboot"},
  {"ParameterName":"innodb_buffer_pool_instances","ParameterValue":"8","ApplyMethod":"pending-reboot"},
  {"ParameterName":"innodb_redo_log_capacity","ParameterValue":"2147483648","ApplyMethod":"pending-reboot"},
  {"ParameterName":"innodb_io_capacity","ParameterValue":"200","ApplyMethod":"pending-reboot"},
  {"ParameterName":"innodb_io_capacity_max","ParameterValue":"2000","ApplyMethod":"pending-reboot"}
]'
printf '%s\n' "$PINS_JSON" | python3 -m json.tool

# The group must be attached to nothing but the clone; most of these are dynamic.
aws rds describe-db-instances \
  --query "DBInstances[?DBParameterGroups[?DBParameterGroupName=='${PG}']].DBInstanceIdentifier" \
  --output text
# Expected: exactly the clone's identifier

assert_rehearsal "$PG" && assert_rehearsal "$CLONE" && \
  aws rds modify-db-parameter-group --db-parameter-group-name "$PG" --parameters "$PINS_JSON"
aws rds describe-db-parameters --db-parameter-group-name "$PG" --source user \
  --query 'Parameters[].[ParameterName,ParameterValue,ApplyMethod]' --output text
aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].DBParameterGroups[0].ParameterApplyStatus' --output text
# Expected: every pin read back, and pending-reboot. One bad value fails the whole
# modify; if a pin is missing, stop rather than reboot.

# Reboot. RDS can take minutes to begin the shutdown while still reporting available,
# so a waiter issued straight away returns at once. Poll the events instead.
SINCE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
assert_rehearsal "$CLONE" && \
  aws rds reboot-db-instance --db-instance-identifier "$CLONE" >/dev/null
for i in $(seq 1 60); do
  EV=$(aws rds describe-events --source-identifier "$CLONE" --source-type db-instance \
    --start-time "$SINCE" --query "Events[?contains(Message,'restarted')].Date" --output text)
  [ -n "$EV" ] && { echo "restarted at $EV"; break; }
  sleep 10
done
aws rds wait db-instance-available --db-instance-identifier "$CLONE"
# Expected: "restarted at ..." within a few minutes. If the loop ends without it, the
# readings below are the pre-reboot ones and would wrongly show the pins as failed.

aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].{status:DBInstanceStatus,pgs:DBParameterGroups[0].ParameterApplyStatus}'
# Expected: available, in-sync. incompatible-parameters means a pin is not viable:
# record which, drop it from the Terraform change. It does not block the drill.

sql_readings "$CLONE_HOST"
# Expected: the pinned variables now report the <FROM> figures on a <TO> server.
```

#### Steps 12 to 14: the rollback drill

The drill runs the [Rollback](#rollback) procedure's steps 0 and 2 against the clone, restoring from the clone's own automatic pre-upgrade snapshot. Steps 1, 3, 4 and 5 of the rollback have no clone counterpart (traffic, proxy, Terraform) and are only ever exercised in a real rollback.

**Step 12: locate the automatic pre-upgrade snapshot.**

```bash
aws rds describe-db-snapshots --db-instance-identifier "$CLONE" --snapshot-type automated \
  --query 'reverse(sort_by(DBSnapshots,&SnapshotCreateTime))[].[SnapshotCreateTime,
           EngineVersion,DBSnapshotIdentifier]' --output text
# Expected: pre-upgrade snapshots on <FROM> ("preupgrade" in the identifier) plus any
# daily backup. Anything on <TO> is not a rollback point.

PRE=$(aws rds describe-db-snapshots --db-instance-identifier "$CLONE" --snapshot-type automated \
  --query "reverse(sort_by(DBSnapshots[?starts_with(EngineVersion,'${FROM}')],
           &SnapshotCreateTime))[0].DBSnapshotIdentifier" --output text)
echo "$PRE"
[ -n "$PRE" ] && [ "$PRE" != None ] || echo "ABORT: no ${FROM} snapshot to restore from" >&2
# Expected: the newest <FROM> entry. "None" means RDS took none: check step 4's retention.

RB_ID=$CLONE RB_SNAP=$PRE RB_PG=rehearsal-ssl-from
assert_rehearsal "$RB_ID" || echo "ABORT: the drill must target the clone" >&2
```

Step 12 ends with Rollback **step 0's confirmation block** (not its "choose" block, which lists the live instance's snapshots) run with those three variables. Step 2 deletes `$RB_ID` before it restores, so a stale value becomes a failed restore with no clone to retry from: the confirmation block's `echo` must show seven non-empty values and `RB_SNAP` must not be `None`.

**Step 13** is Rollback **step 2**, run once. Its three `date -u` stamps give two intervals: the delete (final snapshot included) and the restore. They are separate numbers in a rollback decision, and only the restore is unavoidable.

**Step 14: confirm what came back.**

```bash
aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].{ev:EngineVersion,endpoint:Endpoint.Address,rid:DbiResourceId,
           pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus}'
# Expected: <FROM>.x, the same endpoint hostname as before the drill, a DIFFERENT rid
# (why a real rollback re-registers the proxy target), rehearsal-ssl-from in-sync.

CLONE_HOST=$(aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].Endpoint.Address' --output text)
sql_readings "$CLONE_HOST"
# Expected: the connection succeeds over TLS, VERSION() <FROM>.x, and the InnoDB values
# match step 9 exactly. Metadata is not proof the database serves; this is.
```

#### Teardown

Before phase 1 at the latest: a clone still referencing the live group makes the apply fail to destroy it (edge case 1). The clone was deleted twice with `--no-delete-automated-backups`, so there are two retained backup sets to remove.

```bash
drop_clone "$CLONE"
aws rds wait db-snapshot-available --db-snapshot-identifier "${CLONE}-final"
for S in $(aws rds describe-db-snapshots --snapshot-type manual \
  --query "DBSnapshots[?starts_with(DBSnapshotIdentifier,'${CLONE}-')].DBSnapshotIdentifier" --output text); do
  drop_snapshot "$S"
done
# Expected: two snapshots, ${CLONE}-final and ${CLONE}-broken-<date>, each previewed and confirmed

# List before deleting: pointed at the live instance this loop would remove the
# environment's whole point-in-time history.
assert_rehearsal "$CLONE" && \
  aws rds describe-db-instance-automated-backups \
    --query "DBInstanceAutomatedBackups[?DBInstanceIdentifier=='${CLONE}'].[DBInstanceAutomatedBackupsArn,
             DbiResourceId,Status]" --output text
# Expected: two rows, the clone's two DbiResourceId values. Anything else: stop.
assert_rehearsal "$CLONE" && \
  for ARN in $(aws rds describe-db-instance-automated-backups \
    --query "DBInstanceAutomatedBackups[?DBInstanceIdentifier=='${CLONE}'].DBInstanceAutomatedBackupsArn" \
    --output text); do
    aws rds delete-db-instance-automated-backup --db-instance-automated-backups-arn "$ARN"
  done

aws rds delete-db-parameter-group --db-parameter-group-name rehearsal-ssl-from
aws rds delete-db-parameter-group --db-parameter-group-name rehearsal-ssl-to

aws rds describe-db-instances \
  --query 'DBInstances[?contains(DBInstanceIdentifier,`rehearsal`)].DBInstanceIdentifier' --output text
aws rds describe-db-snapshots --snapshot-type manual \
  --query 'DBSnapshots[?contains(DBSnapshotIdentifier,`rehearsal`)].DBSnapshotIdentifier' --output text
aws rds describe-db-parameter-groups \
  --query 'DBParameterGroups[?contains(DBParameterGroupName,`rehearsal`)].DBParameterGroupName' --output text
aws rds describe-db-instance-automated-backups \
  --query "DBInstanceAutomatedBackups[?DBInstanceIdentifier=='${CLONE}'].Status" --output text
# Expected: empty for all four
```

## Phase 0B: rehearse on a clone of production (conditional)

The only phase before phase 4 that touches production, and only to create a snapshot. Decide with the gate; skipping it is a legitimate outcome that keeps all of phase 0 inside development.

```bash
# Gate 1: schema. Once per environment, in a session pointed at that environment.
HOST=$(aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].Endpoint.Address' --output text)
sql_run "$HOST" <<<'SELECT version_num FROM alembic_version;'
# Expected: the same revision in both. Both run alembic upgrade head at startup, so the
# same revision means 0A's precheck already covered production's schema.

# Gate 2: data volume. Both instances are in the one account, so one session reads both.
for E in "$ENV" <the other environment>; do
  echo "== $E"
  aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name FreeStorageSpace \
    --dimensions "Name=DBInstanceIdentifier,Value=${E}-optinist-cloud-rds" \
    --start-time "$(date -u -v-2H +%Y-%m-%dT%H:%M:%SZ)" --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --period 300 --statistics Average \
    --query 'sort_by(Datapoints,&Timestamp)[-1].Average' --output text
done
# Subtract from AllocatedStorage to get used space.
```

| Alembic revisions | Used data | Decision |
|---|---|---|
| Same | Comparable | **Skip 0B.** Announce 0A's duration plus a margin |
| Same | Production materially larger | Run 0B for the timing only |
| Differ | Either | **Run 0B.** 0A did not test production's schema |

If 0B is skipped, the precheck runs for the first time in phase 4. It fails safe (the upgrade does not complete; the instance stays on `<FROM>`), so decide the response to that outcome before the window, not inside it.

If 0B runs: re-run the session setup with `ENV` set to production, then 0A steps 2 to 8 with these differences. The source is a fresh manual snapshot, named with `rehearsal` so the guards accept it. Step 9 (the live instance's readings) and the drill are not repeated. The only operation against production itself is the snapshot; the exposure is the shell, in which `DB` is production.

```bash
CLONE=${ENV}-optinist-rds-upgrade-rehearsal
SNAP=${ENV}-optinist-rehearsal-source-$(date +%Y%m%d-%H%M)
aws rds create-db-snapshot --db-instance-identifier "$DB" --db-snapshot-identifier "$SNAP"
aws rds wait db-snapshot-available --db-snapshot-identifier "$SNAP"
# ... 0A steps 2 to 8, then 0A's teardown block (one retained backup set, not two), then:
drop_snapshot "$SNAP"
```

`drop_snapshot "$SNAP"` is the call that matters: the newest manual snapshot of production is what an accidental instance delete falls back to, and the guard accepts only `rehearsal`-scoped names.

Whether 0B ran or not, run 0A's final four listing commands and confirm nothing `rehearsal`-shaped survives into phase 1.

## Phase 0C: dry-run the plan gate

`terraform plan` is read-only, so the B4 gate runs days ahead of the apply. It needs the Terraform edit present in the tree being planned, and it must run from the directory Terraform actually applies from (a separate deployment checkout is common).

```bash
export AWS_PROFILE=<the profile for this account>
export AWS_REGION=ap-northeast-1
export ENV=<the Terraform var.environment value>
export TF_ENV=<the backends/ and environments/ file basename>
cd <the deployment checkout>/infrastructure/terraform

git rev-parse --abbrev-ref HEAD; git log -1 --oneline; git status --porcelain
grep -nE 'family|name_prefix|engine_version|allow_major_version_upgrade' infrastructure.tf
echo "ENV=$ENV TF_ENV=$TF_ENV"
# Expected: the phase 1 branch, the INCOMING family and version in the grep, and the
# development values. Stop on anything else: the next command attaches this directory
# to that environment's remote state.

terraform init -backend-config="backends/${TF_ENV}.hcl" -reconfigure
# plan takes a state lock; let it finish. The plan file goes outside the worktree: it
# holds every variable value, and an untracked file trips the clean-tree guard.
PLAN=$(mktemp -t tfplan-dryrun)
terraform plan -var-file="environments/${TF_ENV}.tfvars" -out="$PLAN"
```

The gate assertions. Phase 1 step 8 and phase 4 run exactly these against their own plan files.

```bash
terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.type=="aws_db_instance")
  | "\(.address) -> \(.change.actions|join(","))"'
# Expected: aws_db_instance.main -> update. Any create or delete aborts (B4).

terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.type=="aws_db_parameter_group")
  | "\(.address) -> \(.change.actions|join(","))"'
# Expected: one create and one delete (the create_before_destroy replacement)

terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.type=="aws_db_parameter_group"
    and (.change.actions|index("create")))
  | {name: .change.after.name, name_prefix: .change.after.name_prefix, family: .change.after.family}'
# Expected: name null, name_prefix set, family mysql<TO>. A literal name means B1 is unfixed.

terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.type=="aws_db_parameter_group"
    and (.change.actions|index("create")))
  | .change.after.parameter[]? | "\(.name) = \(.value) [\(.apply_method)]"'
# Expected: one line per pin, values intact. A bare brace is literal in HCL; a mistyped
# ${...} would have been read as interpolation.

terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.address=="null_resource.build_and_deploy")
  | {before: .change.before.triggers, after: .change.after.triggers}'
# Expected: this is B6 settled from evidence. A source_revision key that changes means
# the apply rebuilds the image and rolls all four ECS services, and the window must
# cover it. No such key, or an unchanged map, means the apply is the database alone.
# A key present in before and absent in after means the configuration being applied is
# OLDER than the one in state: stop. On production, this query sizes the window.

terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.change.actions != ["no-op"])
  | "\(.address) -> \(.change.actions|join(","))  replace_paths=\(.change.replace_paths // [])"' | sort
# Expected from the engine change: the instance updating in place, the parameter group
# replaced on family/name_prefix, and whatever derives the group's name (on development
# the scheduler Lambda's environment and its IAM policy). Everything else is drift or the
# deploy, and it all applies: classify each entry (phase 1 step 9) before deciding.
```

0C only; phases 1 and 4 apply their plan file in step 10:

```bash
rm -f "$PLAN"
```

0C cannot prove the replacement succeeds; a name collision only surfaces at apply time.

## Phase 1: apply to development

Weekdays, while the environment is up, finishing before the scheduled stop (B3, B4). Fourteen steps in one shell session: `PRESNAP` (step 4) and `PLAN` (step 7) are set in one step and read in a later one. Phase 4 runs this same sequence against production and cites the steps by number.

#### Step 1: the `<FROM>` readings exist, and the pins are decided

```bash
grep -oE 'name += +"innodb_[a-z_]+"' infrastructure/terraform/infrastructure.tf | sort
# Expected: one line per pin the 0A step 8 / step 9 pair justified.

aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{ev:EngineVersion,endpoint:Endpoint.Address}'
# Expected: <FROM>.x. If the "before" readings are not on file, run
# sql_readings against this endpoint NOW; after the apply there is no later opportunity.
```

#### Step 2: read the stop schedule, and extend it only if needed

```bash
aws events describe-rule --name "${ENV}-dev-schedule-stop" \
  --query '[Name,ScheduleExpression,State]' --output text
# Expected: the stop cron, ENABLED. Convert to local time: that is the deadline.

# Fallback only. Capped at twelve hours from the moment it is set, so set it late enough
# to clear the deadline. The next scheduled start clears it.
aws lambda invoke --function-name "${ENV}-dev-scheduler" \
  --payload '{"action":"override","hours":12}' \
  --cli-binary-format raw-in-base64-out /dev/stdout
# Expected in the FUNCTION response: {"statusCode": 200, "action": "override", "expires_at": ...}
# and expires_at later than the stop. A 400 "OVERRIDE_PARAM_NAME not configured" means
# nothing was set, although the invoke itself reports success.
```

#### Step 3: the instance is available

```bash
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{status:DBInstanceStatus,ev:EngineVersion}'
# Expected: available, <FROM>.x. Anything else aborts (B4).
```

#### Step 4: the manual pre-upgrade snapshot

The only recovery point that survives an instance delete. `PRESNAP`, not `SNAP`: `SNAP` names restore sources that get deleted elsewhere in this document.

```bash
PRESNAP=${ENV}-pre-upgrade-$(date +%Y%m%d-%H%M)
aws rds create-db-snapshot --db-instance-identifier "$DB" --db-snapshot-identifier "$PRESNAP"
aws rds wait db-snapshot-available --db-snapshot-identifier "$PRESNAP"
aws rds describe-db-snapshots --db-snapshot-identifier "$PRESNAP" \
  --query 'DBSnapshots[0].{id:DBSnapshotIdentifier,status:Status,ev:EngineVersion,created:SnapshotCreateTime}'
# Expected: available, <FROM>.x. Do not proceed on "creating".
```

#### Step 5: retain an outgoing-family parameter group

The apply destroys the managed group, and a rollback needs an outgoing-family group to attach. `copy-db-parameter-group` is not idempotent: if the copy already exists, verify it rather than re-running.

```bash
aws rds copy-db-parameter-group \
  --source-db-parameter-group-identifier "$LIVE_PG" \
  --target-db-parameter-group-identifier "${ENV}-optinist-ssl-rollback" \
  --target-db-parameter-group-description "outgoing-family rollback target"

aws rds describe-db-parameter-groups --db-parameter-group-name "${ENV}-optinist-ssl-rollback" \
  --query 'DBParameterGroups[0].{name:DBParameterGroupName,family:DBParameterGroupFamily}'
aws rds describe-db-parameters --db-parameter-group-name "${ENV}-optinist-ssl-rollback" \
  --source user --query 'Parameters[].{name:ParameterName,value:ParameterValue}' --output table
# Expected: family mysql<FROM>, and the same user-set parameters as the live group. A
# copy that lost require_secure_transport restores an instance that refuses the
# application's TLS-only connections, and that is discovered after the restore.
```

#### Step 6: confirm the recovery points are real

The automatic pre-upgrade snapshots do not exist until the upgrade takes them, so check their precondition.

```bash
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{retention:BackupRetentionPeriod,delProt:DeletionProtection}'
# Expected: retention greater than zero. Zero means RDS takes no pre-upgrade snapshot.

aws rds describe-db-instance-automated-backups \
  --query "DBInstanceAutomatedBackups[?DBInstanceIdentifier=='${DB}'].{status:Status,
           from:RestoreWindow.EarliestTime,to:RestoreWindow.LatestTime}" --output table
# Expected: one active row whose window runs to a few minutes ago.
```

| Recovery point | Created by | Expires | Restores to |
|---|---|---|---|
| `<ENV>-pre-upgrade-<date>` | Step 4 | Never | `<FROM>`, immediately before the apply |
| `<ENV>-optinist-ssl-rollback` | Step 5 | Never | The group a `<FROM>` instance needs |
| Automatic pre-upgrade snapshots | RDS, during the upgrade | With the retention period | `<FROM>` |
| Point-in-time recovery | The automated backup | With the retention period | Any moment inside a window. On development that is one window per running day, nothing overnight |

#### Step 7: clean worktree, then plan to a file outside the tree

```bash
git status --porcelain
# Expected: empty (B6)
cd infrastructure/terraform
terraform init -backend-config="backends/${TF_ENV}.hcl" -reconfigure
PLAN=$(mktemp -t tfplan)
terraform plan -var-file="environments/${TF_ENV}.tfvars" -out="$PLAN"
```

#### Step 8: the gate assertions

Run every phase 0C assertion against `"$PLAN"`, including the trigger and attribution queries. The gate passes on two resources; the plan contains more, and all of it applies.

#### Step 9: classify the whole plan by root cause

| Bucket | How to recognise it | What to do |
|---|---|---|
| The engine change | The instance, the parameter group, whatever derives the group's name | Intended |
| The deploy (B6) | `null_resource` replacements whose triggers carry the git commit | Expected on the `develop-main` lineage. Its rollout time belongs in the window |
| Runtime state against configuration | A count or instance declared statically while something scales it at runtime | Decide deliberately: the apply overwrites the runtime state |
| Drift | An AMI data source with `most_recent`, a JSON body, a recomputed hash | Usually harmless, but read the ones that replace rather than update |

A `delete,create` replacement is down between the two actions. A NAT instance or gateway in that state takes private-subnet egress with it, and without an SSM interface endpoint the in-VPC checks in steps 11 to 14 fail during the gap. Retry them once the apply settles, and record the gap so a soak metric can be attributed.

#### Step 10: apply

```bash
terraform apply "$PLAN"
```

#### Step 11: the upgrade happened rather than being queued

```bash
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{ev:EngineVersion,status:DBInstanceStatus,
           pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus,pending:PendingModifiedValues}'
# Expected: <TO>.x, available, a generated group name, in-sync, pending == {} (B2)
```

#### Step 12: the pins took effect

A pin that did not apply looks exactly like a clean apply: the group shows the value, the instance is `available`, the engine runs the `<TO>` default.

```bash
NEWPG=$(aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].DBParameterGroups[0].DBParameterGroupName' --output text)
aws rds describe-db-parameters --db-parameter-group-name "$NEWPG" --source user \
  --query 'Parameters[].[ParameterName,ParameterValue,ApplyType,ApplyMethod]' --output text
# Expected: every pin present

LIVE_HOST=$(aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].Endpoint.Address' --output text)
sql_readings "$LIVE_HOST"
# Expected: every pinned variable reports the <FROM> figure, not the <TO> default. If a
# static pin has not taken effect, reboot-db-instance and re-read.
```

Record three columns: `<FROM>` measured, `<TO>` default measured, `<TO>` pinned measured. That is what makes the soak's metrics attributable.

#### Step 13: the scheduler Lambda follows the renamed group

```bash
aws lambda get-function-configuration --function-name "${ENV}-dev-scheduler" \
  --query 'Environment.Variables.RDS_PARAMETER_GROUP_NAME'
# Expected: the generated name from step 11, not the old literal (B1)
```

#### Step 14: the proxy target is healthy

```bash
aws rds describe-db-proxy-targets --db-proxy-name "${ENV}-optinist-rds-proxy" \
  --query 'Targets[].{id:RdsResourceId,state:TargetHealth.State,reason:TargetHealth.Reason}'
# Expected: one target, AVAILABLE. An in-place upgrade keeps DbiResourceId, so no
# re-registration should be needed; confirm rather than assume.
```

A rollback after a night has passed needs one more thing: the evening stop replaced the scheduler's fixed-name snapshot with a `<TO>` one, and the Lambda points at the `<TO>`-family group. Rollback step 4 covers it.

## Phase 2: one nightly destroy and restore cycle

Proves B3 in the direction phase 0A cannot: a `<TO>` snapshot restored against the `<TO>`-family group, which is the path every later restore of this environment takes. One cycle settles it. `restore_rds()` passes the same explicit parameters every cycle and reads nothing about the snapshot's provenance, so a second cycle exercises the identical path. Whatever it does not pass is inherited from the snapshot; the one inherited property that matters, `BackupRetentionPeriod`, is read in check 2. Later cycles are the environment's normal operation, not a gate.

Check 1 runs in the evening after the stop. Checks 2 to 6 need the morning restore: `destroy_rds()` deletes the instance, so in the evening `describe-db-instances` returns `DBInstanceNotFound`.

```bash
# 1. Evening: the snapshot the morning restore consumes is on the new version
aws rds describe-db-snapshots --db-snapshot-identifier "${DB}-dev-scheduler" \
  --query 'DBSnapshots[0].{ev:EngineVersion,status:Status,created:SnapshotCreateTime}'
# Expected: <TO>.x, available, tonight's timestamp. <FROM> means the upgrade never completed.
```

```bash
# 2. Morning: the restore
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{ev:EngineVersion,status:DBInstanceStatus,
           pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus,pending:PendingModifiedValues,
           retention:BackupRetentionPeriod}'
# Expected: <TO>.x, available, in-sync, pending == {}, retention unchanged (edge case 5)

# 3. No InvalidParameterCombination, the failure B3 predicts
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

# 5. The proxy target exists and is healthy. The proxy deregisters on delete and does
#    not re-register on its own; ensure_rds_proxy_target() does it on start.
aws rds describe-db-proxy-targets --db-proxy-name "${ENV}-optinist-rds-proxy" \
  --query 'Targets[].{id:RdsResourceId,state:TargetHealth.State,reason:TargetHealth.Reason}'
# Expected: one target, AVAILABLE
aws logs filter-log-events --log-group-name "/aws/lambda/${ENV}-dev-scheduler" \
  --start-time "$SINCE" --filter-pattern 'rds_proxy' \
  --query 'events[].message' --output text | tail -5
# Expected: "deferred_still_creating" on the first start pass, then "registered" on the
# verify pass. "error: RDS still not available at verify-start" means the restore is stuck.

# 6. The pins survived the restore, read through the proxy. The only check that is the
#    engine's account rather than the control plane's.
PROXY=$(aws rds describe-db-proxies --db-proxy-name "${ENV}-optinist-rds-proxy" \
  --query 'DBProxies[0].Endpoint' --output text)
sql_readings "$PROXY"
# Expected: <TO>.x and the pinned values. A failure here but not in check 2 is a proxy
# problem, not an engine one.
```

## Phase 3: soak to exit criteria

The exit is a checklist, not a date. Every scheduled database path has a cadence of 24 hours or less, so one night covers them all; elapsed time finds almost nothing that running the criteria does not.

| # | Exit criterion | Skippable? |
|---|---|---|
| 1 | Phase 2's six checks pass on one cycle, including the retention period | No |
| 2 | The deployed e2e lanes are green with `E2E_FAIL_ON_SKIP=1`. The release set may stand in for the two expensive lanes if criterion 4 runs in full (see [Testing](#testing)) | No |
| 3 | The health-lane case #902 adds (an engine change is fully applied) is green | Yes: regression cover, can follow phase 4 |
| 4 | Every scheduled job has run once on the new version with no SQL error, by invoking it | No. It is the only thing that executes the Lambda packages' hand-written SQL |
| 5 | The log queries are clean | Can be shortened |
| 6 | Production baseline metrics captured | **Never.** Impossible after phase 4, and it is one command |

#### Criterion 4: invoke every scheduled job

Each payload is the literal `input` the EventBridge target sends. This writes and deletes: the cleanup jobs remove data, and `ExpirationLifecycleJob` performs a day's expiry at once. Announce it on a shared environment.

> **Do not invoke `<ENV>-dev-scheduler`.** It sits in the same naming scheme and it deletes the instance you are soaking.

```bash
# A Lambda that raises still returns StatusCode 200 with FunctionError beside it, and a
# ResourceNotFound or AccessDenied never carries FunctionError at all. The verdict needs both.
inv() {
  local resp st
  resp=$(mktemp -t lambdaresp)
  st=$(aws lambda invoke --function-name "${ENV}-$1" --payload "$2" \
         --cli-binary-format raw-in-base64-out "$resp" 2>&1)
  if ! printf '%s' "$st" | grep -qE '"StatusCode":[[:space:]]*200'; then
    echo "FAIL $1 -- the invoke itself did not return 200"
  elif printf '%s' "$st" | grep -q FunctionError; then
    echo "FAIL $1 -- FunctionError"
  else
    echo "ok   $1"
  fi
  printf '  status: %s\n' "$(printf '%s' "$st" | tr -d '\n')"
  printf '  payload: '; head -c 600 "$resp"; echo
  rm -f "$resp"
}
SCHED='"source":"aws.events","detail-type":"Scheduled Event"'

inv free-manager        "{$SCHED,\"detail\":{\"action\":\"monitor\"}}"
inv premium-manager     "{$SCHED,\"detail\":{\"action\":\"monitor\"}}"
inv premium-cleanup     "{$SCHED,\"detail\":{\"action\":\"cleanup\"}}"
inv common-user-manager "{$SCHED,\"detail\":{\"action\":\"manage_users\"}}"
inv cost-tracker   '{}'
inv public-cleanup '{}'
# Expected: ok on every line
```

`free_cleanup` has no EventBridge schedule and no known invoker; record it as an uncovered path rather than firing a deletion Lambda blind.

The in-process background jobs have no Lambda. `docker exec` needs `-i`: without it `python -` reads an empty program, exits 0, and SSM reports `Success` in two seconds with nothing run.

```bash
ssm_sh <<'SH'
set -e
C=$(docker ps --format '{{.Names}}' | grep -m1 -- -background-optinist-cloud-container)
echo "container: $C"
docker exec -i -w /app "$C" python - <<'PY'
import traceback
from studio.app.common.core.background.expiration_lifecycle_job import ExpirationLifecycleJob
from studio.app.common.core.background.premium_expiration_sweep_job import PremiumExpirationSweepJob
from studio.app.common.core.background.storage_reconciliation_job import StorageReconciliationJob
from studio.app.common.core.background.cleanup_job import DataCleanupJob
from studio.app.common.core.background.sync_job import PublishedExperimentSyncJob

failed = []
for job in (ExpirationLifecycleJob, PremiumExpirationSweepJob,
            StorageReconciliationJob, DataCleanupJob, PublishedExperimentSyncJob):
    print(f"== {job.__name__}", flush=True)
    try:
        job.run()
        print(f"   ok {job.__name__}", flush=True)
    except Exception:
        failed.append(job.__name__)
        traceback.print_exc()
        print(f"   FAIL {job.__name__}", flush=True)
print("FAILED:", failed or "none", flush=True)
PY
SH
# Expected, all three: a non-empty "container:" line, five "== JobName" lines, and
# "FAILED: none" last. Anything less means the block did not run. A real run takes
# minutes. ExpirationLifecycleJob logs "No remote bucket configured" where none is set,
# which counts as invoked, not exercised.
```

#### Criterion 5: the log queries

Two windows: `SINCE` (24 hours) for the queries whose expected result is empty regardless, and `AFTER` (the apply's settle time, local time) for the Lambda counts, because on development the nightly stop produces a band of `9501 Timed-out waiting to acquire database connection` while the instance is gone, and that predates the upgrade. On production the same line is a finding. Filter on exception class names, never on numeric error codes: CloudWatch matches substrings, and `1290` matches connection ids and ports.

```bash
SINCE=$(( ($(date +%s) - 24*3600) * 1000 ))
AFTER=$(( $(date -j -f '%Y-%m-%d %H:%M:%S' '<YYYY-MM-DD HH:MM:SS>' +%s) * 1000 ))
ts_fmt() {
  while IFS=$'\t' read -r ts rest; do
    [ -n "$ts" ] || continue
    printf '%s  %s\n' "$(date -r $((ts/1000)) '+%Y-%m-%d %H:%M:%S')" "$rest"
  done
}

aws logs describe-log-streams --log-group-name "/aws/rds/instance/${DB}/error" \
  --order-by LastEventTime --descending --max-items 3 \
  --query 'logStreams[].[lastEventTimestamp,logStreamName]' --output text | ts_fmt
# Expected: a timestamp AFTER the apply. Silence means the export did not survive the
# family change.

aws logs filter-log-events --log-group-name "/aws/rds/proxy/${ENV}-optinist-rds-proxy" \
  --start-time "$SINCE" \
  --filter-pattern '?"Access denied" ?"Authentication failed" ?ConnectionRefusedError ?"error 1290"' \
  --query 'events[].message' --output text
# Expected: empty

for LG in "/ecs/${ENV}-optinist-cloud-taskdef" "/ecs/${ENV}-background-optinist-cloud-taskdef" \
          "/ecs/${ENV}-premium-optinist-cloud-taskdef" "/ecs/${ENV}-public-optinist-cloud-taskdef"; do
  echo "== $LG"
  aws logs filter-log-events --log-group-name "$LG" --start-time "$SINCE" \
    --filter-pattern '?OperationalError ?ProgrammingError ?InternalError ?IntegrityError' \
    --query 'events[].message' --output text | head -c 1500; echo
done
# Expected: empty

# A handler can catch a SQL exception, log it, and still return 200, so criterion 4's
# verdict does not cover this. Count rather than print: head shows the OLDEST matches.
for L in free-manager premium-manager premium-cleanup common-user-manager cost-tracker \
         public-cleanup free-cleanup; do
  N=$(aws logs filter-log-events --log-group-name "/aws/lambda/${ENV}-$L" --start-time "$AFTER" \
        --filter-pattern '?"9501" ?OperationalError ?ProgrammingError ?InternalError ?Traceback' \
        --query 'length(events)' --output text)
  echo "$L: $N"
done
# Expected: every number zero (several zeros per Lambda is normal: one per page)

aws logs filter-log-events --log-group-name "/ecs/${ENV}-background-optinist-cloud-taskdef" \
  --start-time "$SINCE" \
  --filter-pattern '?storage_tracking ?premium_expiration ?StorageReconciliation ?DataCleanup ?ExpirationLifecycle' \
  --query 'events[].[timestamp,message]' --output text | ts_fmt | tail -30
# Expected: NOT empty, with entries after the apply: the container's own scheduler
# running the jobs unattended, separate evidence from the manual invokes.
```

The `general` and `slowquery` exports are at the engine default of off and produce nothing either way.

#### Criterion 6: the production baseline

The only step in phase 3 that reads production, from a shell whose every variable points at development. Use `PROD_*` names, pass `--profile` inline, never `export AWS_PROFILE`, and write to a file: the capture cannot be retaken, and `$TMPDIR` is cleared on reboot.

```bash
PROD_DB=<production db instance identifier>
PROD_PROFILE=<production profile name>
OUT=$HOME/baseline-production-$(date +%Y%m%d-%H%M).txt

assert_prod() {
  local ev
  ev=$(aws rds describe-db-instances --profile "$PROD_PROFILE" \
         --db-instance-identifier "$PROD_DB" --query 'DBInstances[0].EngineVersion' --output text) || return 1
  case "$ev" in
    ${FROM}.*) return 0 ;;
    *) echo "ABORT: $PROD_DB reports $ev, expected ${FROM}.x (wrong instance, or already upgraded)" >&2
       return 1 ;;
  esac
}

assert_prod && {
  for M in CPUUtilization ReadIOPS WriteIOPS DatabaseConnections \
           BufferCacheHitRatio ReadLatency WriteLatency FreeableMemory; do
    echo "== $M"
    aws cloudwatch get-metric-statistics --profile "$PROD_PROFILE" \
      --namespace AWS/RDS --metric-name "$M" \
      --dimensions "Name=DBInstanceIdentifier,Value=$PROD_DB" \
      --start-time "$(date -u -v-14d +%Y-%m-%dT%H:%M:%SZ)" --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      --period 86400 --statistics Average Maximum \
      --query 'sort_by(Datapoints,&Timestamp)[].{t:Timestamp,avg:Average,max:Maximum}' --output table
  done
} 2>&1 | tee "$OUT"
wc -l "$OUT" && grep -c "^== " "$OUT"
# Expected: eight headings, 14 daily points under each except BufferCacheHitRatio, which
# is an Aurora metric and returns nothing on RDS MySQL. ReadIOPS, ReadLatency and
# FreeableMemory carry the buffer pool question in its place.
```

## Phase 4: apply to production

**Run phase 1 steps 1 to 14 with the session setup pointed at production**, with these differences:

| | On production |
|---|---|
| `TF_ENV`, not `ENV`, names the backend and tfvars files | `backends/production.hcl` and `environments/production.tfvars` while `$ENV` is `subscr`. Using `$ENV` fails at the first terraform command of the window |
| Step 1 | Carries more weight: there is no phase 0A, so this is the only `<FROM>` reading. Complete it before the window |
| Step 2 | Skipped: no scheduler, so the timing is free |
| Step 13 | Skipped, and the blast radius is smaller: with no scheduler, the instance is the sole consumer of the group name |
| The window | Measured upgrade duration (0B's if it ran, else 0A's plus a margin), **plus the ECS rollout only if the production plan's trigger query shows the apply rebuilds** (rehearsed the day before, below). 0C read development's state, which can differ. On the `develop/v1.1.11` lineage it does not rebuild; do not carry development's apply time over |

Steps 2 and 13 are skipped on a variable, not on the environment. Check it:

```bash
# From infrastructure/terraform in the deployment checkout
grep -n enable_dev_schedule "environments/${TF_ENV}.tfvars"
# Expected: no match, or "= false". A "= true" means production has a scheduler and
# steps 2 and 13 must run.
```

If 0B was skipped, the precheck runs here for the first time. It fails safe (the instance stays on `<FROM>`; the cost is the window), so the response is decided beforehand: capture the log, do not attempt a fix in the window, and take a second window after a reviewed fix.

#### The day before: rehearse every read-only check

A rehearsal proves the command and the resource names, not the state, so each rehearsed check still runs inside the window. Three things cannot be rehearsed and are the window: the snapshot (step 4), the parameter-group copy (step 5) and the apply (step 10). The first two may be done early, then re-verified at apply time.

| Rehearse | Settles |
|---|---|
| Phase 1 steps 1, 3 and 6 | Every resource name resolves on an environment nobody has queried in this work |
| Phase 1 steps 7 and 8, plan only, no apply | Production's own `build_and_deploy` trigger map, which decides whether the window covers an ECS rollout. Include the backend check below. `rm -f "$PLAN"` afterwards |
| The ECS and alarm checks below | The four service names and three alarm names |
| The proxy reach check below | `ssm_sh` reaches production and the database answers through the proxy |
| The health lane below | `frontend/e2e/.env.prod` exists and is complete, and the lane is green on `<FROM>`, so a failure afterwards is new or it is not |

```bash
PROXY=$(aws rds describe-db-proxies --db-proxy-name "${ENV}-optinist-rds-proxy" \
  --query 'DBProxies[0].Endpoint' --output text)
sql_readings "$PROXY"
# Expected before the window: <FROM>.x through the proxy. After the apply: <TO>.x.
# A failure here while the instance is available is a proxy problem, and production's
# target is not re-registered automatically.
```

The health lane reads its environment from `frontend/e2e/.env.prod` (keys in `.env.prod.example`); `playwright.config.ts` hard-fails on an incomplete file and its values override the shell. `HEALTH_ENV` is the variable (`TARGET_ENV` is a TypeScript constant and setting it is inert). Off development the lane requires `BASE_URL`, `RDS_PROXY_HOST`, `RDS_SSM_INSTANCE_NAME`, `RDS_SECRET_ID` and `STRIPE_SECRET_ENV` as `expect`s. `E2E_FAIL_ON_SKIP=1` is deliberately absent on production: some cases skip on state a healthy production legitimately has. Read the reporter's skip list instead.

```bash
# From the repository root
( cd frontend && ls -l e2e/.env.prod && \
  for k in BASE_URL API_URL HEALTH_ENV RDS_PROXY_HOST RDS_SECRET_ID \
           RDS_SSM_INSTANCE_NAME STRIPE_SECRET_ENV TEST_USER_EMAIL TEST_USER_PASSWORD; do
    grep -qE "^${k}=." e2e/.env.prod && echo "ok   $k" || echo "MISSING $k"
  done )
# Expected: ok on every line

( cd frontend && E2E_TARGET=prod yarn test:e2e e2e/17-aws-health.spec.ts --retries 0 \
    --grep-invert "HEALTH-25|HEALTH-26" )
# Expected: green. Record the reporter's "N executed, N skipped" line and each skip's
# reason: that shape is the baseline, and the post-apply run uses this identical
# invocation. HEALTH-25 and HEALTH-26 are deselected because they generate write-shaped
# and concurrent traffic against production. --retries 0 so a retry does not double the
# load; no --headed, nothing here drives a browser.
```

#### Inside the window

Phase 1 steps 3 to 14, skipping 13 unless the `enable_dev_schedule` check said otherwise. In step 7, after its `terraform init` and before its plan, prove which state is attached:

```bash
# From infrastructure/terraform, after step 7's terraform init
terraform state list | grep aws_db_instance
# Expected: exactly one line, and it is production's instance. grep, not grep -c: a
# count cannot show WHICH instance. An empty result is a backend problem, not an empty state.
```

After step 14:

```bash
aws ecs describe-services --cluster "${ENV}-optinist-cloud-cluster" \
  --services "${ENV}-optinist-cloud-service" "${ENV}-premium-optinist-cloud-service" \
             "${ENV}-background-optinist-cloud-service" "${ENV}-public-optinist-cloud-service" \
  --query 'services[].{name:serviceName,status:status,desired:desiredCount,
           running:runningCount,rollout:deployments[0].rolloutState}'
# Expected: ACTIVE, running == desired, COMPLETED for all four

aws cloudwatch describe-alarms \
  --alarm-names "${ENV}-optinist-rds-cpu-high" "${ENV}-optinist-rds-connections-high" \
                "${ENV}-optinist-rds-storage-low" \
  --query 'MetricAlarms[].{name:AlarmName,state:StateValue,updated:StateUpdatedTimestamp}'
# Expected: OK for all three. INSUFFICIENT_DATA immediately after the upgrade resolves
# within a couple of evaluation periods.
```

Then `sql_readings "$PROXY"` (expected `<TO>.x`) and the health lane again, compared against the day-before baseline. If the release process cycles the ECS services afterwards, re-read the service and proxy checks after the cycle: fresh tasks starting against the upgraded engine are stronger evidence than tasks that were already running.

A service stuck short of its desired count while the instance is healthy is a connection pool holding a dead connection: force a new deployment on that service. A rollout problem, not a rollback trigger.

## Phase 5: verify and clean up

```bash
for E in development subscr; do
  aws rds describe-db-instances --db-instance-identifier "${E}-optinist-cloud-rds" \
    --query 'DBInstances[0].{id:DBInstanceIdentifier,ev:EngineVersion,endpoint:Endpoint.Address,
             status:DBInstanceStatus,pg:DBParameterGroups[0].DBParameterGroupName,
             pgs:DBParameterGroups[0].ParameterApplyStatus,els:EngineLifecycleSupport}'
done
# Expected: <TO>.x, available, in-sync, the original identifier and endpoint.
# EngineLifecycleSupport still naming Extended Support is correct (B5).

aws ce get-cost-and-usage --granularity DAILY \
  --time-period "Start=$(date -u -v-7d +%Y-%m-%d),End=$(date -u +%Y-%m-%d)" \
  --metrics UnblendedCost \
  --filter "{\"Dimensions\":{\"Key\":\"USAGE_TYPE\",\"Values\":[\"APN1-ExtendedSupport:Yr1-Yr2:MySQL${FROM}\"]}}" \
  --query 'ResultsByTime[].{day:TimePeriod.Start,cost:Total.UnblendedCost.Amount}' --output table
# Expected: falling to zero from the day after the production apply

# Inventory the rollback artefacts. Lists, deliberately: deleting them closes the
# rollback window, so do it against the retention duration agreed before phase 1.
aws rds describe-db-snapshots --snapshot-type manual \
  --query 'DBSnapshots[?contains(DBSnapshotIdentifier,`pre-upgrade`)
           || contains(DBSnapshotIdentifier,`rehearsal`)].DBSnapshotIdentifier' --output text
aws rds describe-db-parameter-groups \
  --query 'DBParameterGroups[?contains(DBParameterGroupName,`rehearsal`)
           || contains(DBParameterGroupName,`rollback`)].DBParameterGroupName' --output text

aws rds describe-db-snapshots --snapshot-type manual \
  --query 'DBSnapshots[?contains(DBSnapshotIdentifier,`dev-scheduler`)].DBSnapshotIdentifier' --output text
# Expected: still present. Never delete the scheduler's own snapshot.
```

#### The cleanup PR

| Change | File | Plan effect |
|---|---|---|
| Remove `allow_major_version_upgrade` and `apply_immediately` | `infrastructure/terraform/infrastructure.tf` | **None on `aws_db_instance.main`.** Neither is read back from the API |
| Update the MySQL client package if it has fallen behind | `infrastructure/scripts/app_setup.sh` | Updates `aws_s3_object.app_setup_script` (`etag = filemd5(...)`) |
| Documentation | `infrastructure/documentation/` | None |

The six pinned parameters stay. Do not assert an empty plan: on the `develop-main` lineage `null_resource.build_and_deploy` is always replaced (B6). Assert per resource.

```bash
cd infrastructure/terraform
PLAN=$(mktemp -t tfplan-cleanup)
terraform plan -var-file="environments/${TF_ENV}.tfvars" -out="$PLAN"
terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.change.actions != ["no-op"])
  | "\(.address) -> \(.change.actions|join(","))"'
# Expected: aws_db_instance.main must NOT appear. If it does, stop: the attributes were
# load-bearing or something else changed.
```

---

## Rollback

There is no engine downgrade. The engine version is a property of the snapshot (`RestoreDBInstanceFromDBSnapshot` has no engine version parameter), so restoring a `<FROM>` snapshot is the whole rollback. The same fact causes B3 in the other direction.

| Recovery point | Created by | Version |
|---|---|---|
| Manual pre-upgrade snapshot `<ENV>-pre-upgrade-<date>` | Phase 1 step 4, phase 4 | `<FROM>`. Primary: its contents are known and it never expires |
| Automatic pre-upgrade snapshots | RDS, during the upgrade | `<FROM>`. Fallback. Survive an instance delete only if it passes `--no-delete-automated-backups` |
| Retained group `<ENV>-optinist-ssl-rollback` | Phase 1 step 5, phase 4 | Outgoing family |

The window stays open as long as those artefacts exist; phase 5 lists rather than deletes them for that reason. What changes with time: the data loss grows (everything written since the snapshot), `git revert` stops being clean once other commits touch `infrastructure.tf`, and Terraform state may have moved, so gate the rollback plan like the forward one.

### The rollback procedure

Self-contained; written for a live instance. Phase 0A's drill runs steps 0 and 2 with `RB_ID=$CLONE`.

```bash
# LIVE ROLLBACK ONLY. The 0A drill sets these three in step 12; do not paste this block there.
# Run the session setup with ENV set to the environment in trouble, then:
RB_ID=$DB
RB_PG=${ENV}-optinist-ssl-rollback
```

**Step 0: choose the restore source, and confirm it.** Two blocks: the drill runs only the second.

```bash
# Choose (live rollback only)
aws rds describe-db-snapshots --db-instance-identifier "$DB" --snapshot-type manual \
  --query 'reverse(sort_by(DBSnapshots[?contains(DBSnapshotIdentifier,`pre-upgrade`)],
           &SnapshotCreateTime))[].{id:DBSnapshotIdentifier,ev:EngineVersion,
           status:Status,created:SnapshotCreateTime}' --output table
aws rds describe-db-snapshots --db-instance-identifier "$DB" --snapshot-type automated \
  --query "reverse(sort_by(DBSnapshots[?starts_with(EngineVersion,'${FROM}')],
           &SnapshotCreateTime))[:3].{id:DBSnapshotIdentifier,ev:EngineVersion,
           created:SnapshotCreateTime}" --output table
# Prefer the manual snapshot. Nothing listed means the rollback window was closed.
RB_SNAP=<the chosen snapshot id>
```

```bash
# Confirm (live rollback and the 0A drill)
aws rds describe-db-snapshots --db-snapshot-identifier "$RB_SNAP" \
  --query 'DBSnapshots[0].{status:Status,ev:EngineVersion,created:SnapshotCreateTime}'
# Expected: available, <FROM>.x. "creating" is not a restore source.
aws rds describe-db-parameter-groups --db-parameter-group-name "$RB_PG" \
  --query 'DBParameterGroups[0].[DBParameterGroupName,DBParameterGroupFamily]' --output text
# Expected: family mysql<FROM>. Missing means step 2's restore fails after the delete:
# create it by copying any surviving outgoing-family group before going further.
read -r CLASS STORAGE SUBNET SG <<<"$(aws rds describe-db-instances \
  --db-instance-identifier "$RB_ID" --output text \
  --query 'DBInstances[0].[DBInstanceClass,StorageType,DBSubnetGroup.DBSubnetGroupName,
           VpcSecurityGroups[0].VpcSecurityGroupId]')"
echo "id=$RB_ID snap=$RB_SNAP pg=$RB_PG class=$CLASS storage=$STORAGE subnet=$SUBNET sg=$SG"
# Expected: seven non-empty values, and snap is not "None". They are unavailable once
# the instance is deleted.
```

**Step 1: stop application traffic.** The manager Lambdas re-scale on their own schedules (`free-manager` every five minutes, `premium-manager` every fifteen), so disable their rules before scaling to zero. Use `for R in $(echo "$RULES")`: zsh does not word-split a bare `$RULES`.

```bash
COUNTS=$(aws ecs describe-services --cluster "${ENV}-optinist-cloud-cluster" \
  --services "${ENV}-optinist-cloud-service" "${ENV}-premium-optinist-cloud-service" \
             "${ENV}-background-optinist-cloud-service" "${ENV}-public-optinist-cloud-service" \
  --query 'services[].[serviceName,desiredCount]' --output text | awk '{print $1"="$2}' | tr '\n' ' ')
echo "COUNTS=$COUNTS"
# Expected: four name=count pairs. WRITE THIS DOWN: step 5 replays it, and the counts are not all 1.

# Derive the rule list from the scheduler's own configuration; a hand-copied list drifts.
RULES=$(aws lambda get-function-configuration --function-name "${ENV}-dev-scheduler" \
  --query 'Environment.Variables.[SCHEDULE_RULE_NAMES,DELAYED_RULE_NAMES]' --output text \
  | tr '\t' '\n' | python3 -c 'import json,sys;print(" ".join(n for l in sys.stdin for n in json.loads(l)))')
# No scheduler (production): list the rules that exist instead
[ -n "$RULES" ] || RULES=$(aws events list-rules --name-prefix "${ENV}-" \
  --query "Rules[?contains(Name,'manager')||contains(Name,'cleanup')||contains(Name,'tracker')].Name" \
  --output text | tr '\t' ' ')
echo "RULES=$RULES"
# Expected on development: five names, including free-manager-asg-events, which is
# triggered BY scaling ECS to zero. WRITE THIS DOWN.

for R in $(echo "$RULES"); do aws events disable-rule --name "$R" && echo "disabled $R"; done
for R in $(echo "$RULES"); do aws events describe-rule --name "$R" --query '[Name,State]' --output text; done
# Expected: DISABLED for every one

for S in "${ENV}-optinist-cloud-service" "${ENV}-premium-optinist-cloud-service" \
         "${ENV}-background-optinist-cloud-service" "${ENV}-public-optinist-cloud-service"; do
  aws ecs update-service --cluster "${ENV}-optinist-cloud-cluster" --service "$S" \
    --desired-count 0 --query 'service.{name:serviceName,desired:desiredCount}'
done
# Expected: desired 0 for all four, and still 0 a few minutes later
```

**Step 2: free the identifier, then restore under it.** Restoring onto the original identifier keeps Terraform convergent and the endpoint unchanged. The final snapshot is the diagnostic copy of the broken instance. This is the one deliberate delete in the procedure, so it goes through `confirm_target`: a preview of the instance and the identifier typed back. The guards are chained with `&&`; a top-level `return` does not reliably stop a pasted block.

```bash
date -u
[ -n "$RB_SNAP" ] && [ "$RB_SNAP" != None ] && confirm_target instance "$RB_ID" && \
  aws rds delete-db-instance --db-instance-identifier "$RB_ID" \
    --final-db-snapshot-identifier "${RB_ID}-broken-$(date +%Y%m%d-%H%M)" \
    --no-delete-automated-backups
aws rds wait db-instance-deleted --db-instance-identifier "$RB_ID"
date -u

aws rds describe-db-snapshots --db-snapshot-identifier "$RB_SNAP" \
  --query 'DBSnapshots[0].{status:Status,ev:EngineVersion}'
# Expected: still available on <FROM>.x. The flag above is what kept an automated
# snapshot alive; confirm before committing to the restore.

[ -n "$RB_SNAP" ] && [ "$RB_SNAP" != None ] && \
  aws rds restore-db-instance-from-db-snapshot \
    --db-instance-identifier "$RB_ID" --db-snapshot-identifier "$RB_SNAP" \
    --db-instance-class "$CLASS" --storage-type "$STORAGE" --port 3306 \
    --db-subnet-group-name "$SUBNET" --vpc-security-group-ids "$SG" \
    --db-parameter-group-name "$RB_PG" \
    --no-publicly-accessible --no-multi-az
aws rds wait db-instance-available --db-instance-identifier "$RB_ID"
date -u

aws rds describe-db-instances --db-instance-identifier "$RB_ID" \
  --query 'DBInstances[0].{ev:EngineVersion,endpoint:Endpoint.Address,rid:DbiResourceId,
           pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus,retention:BackupRetentionPeriod}'
# Expected: <FROM>.x, the original endpoint hostname, a NEW DbiResourceId, the rollback
# group in-sync, retention unchanged. A changed endpoint means the restore did not land
# on the original identifier: stop and rename before continuing. A group still reading
# default.mysql<FROM> means --db-parameter-group-name did not take and TLS is not enforced.
```

**Step 3: re-register the RDS Proxy target.** The restored instance has a new `DbiResourceId`; the proxy does not pick it up on its own.

```bash
aws rds register-db-proxy-targets --db-proxy-name "${ENV}-optinist-rds-proxy" \
  --target-group-name default --db-instance-identifiers "$DB"
aws rds describe-db-proxy-targets --db-proxy-name "${ENV}-optinist-rds-proxy" \
  --query 'Targets[].{id:RdsResourceId,state:TargetHealth.State,reason:TargetHealth.Reason}'
# Expected: one target, AVAILABLE (REGISTERING for a minute or two is normal)
```

**Step 4: revert the Terraform change and re-converge.** The revert removes `apply_immediately = true` along with the rest; add it back before planning so the group swap is not deferred. On development this is also what points the scheduler Lambda back at an outgoing-family group; without it tonight's restore aims the new family at an old snapshot (B3 in reverse).

```bash
NEWEST_TF_COMMIT=$(git log -1 --format=%H -- infrastructure/terraform/infrastructure.tf)
git show --stat "$NEWEST_TF_COMMIT"
# Expected: only the engine change. If it carries other changes, or application commits
# have landed since, edit the lines back by hand on a branch off HEAD instead of reverting.
git revert --no-edit "$NEWEST_TF_COMMIT"

cd infrastructure/terraform
terraform init -backend-config="backends/${TF_ENV}.hcl" -reconfigure
PLAN=$(mktemp -t tfplan-rollback)
terraform plan -var-file="environments/${TF_ENV}.tfvars" -out="$PLAN"
terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.type=="aws_db_instance")
  | "\(.address) -> \(.change.actions|join(","))"'
# Expected: update, or absent. A create or delete would destroy the instance just restored.
terraform apply "$PLAN"
```

**Step 5: bring traffic back.**

```bash
# If the shell from step 1 is gone, redeclare COUNTS and RULES from what was written down.
# Empty values make the loops run zero times and print nothing.
[ -n "$COUNTS" ] && [ -n "$RULES" ] || echo "ABORT: COUNTS and RULES must be set" >&2

for P in $(echo "$COUNTS"); do
  aws ecs update-service --cluster "${ENV}-optinist-cloud-cluster" \
    --service "${P%%=*}" --desired-count "${P##*=}" --force-new-deployment >/dev/null \
    && echo "restored ${P%%=*} to ${P##*=}"
done
for R in $(echo "$RULES"); do aws events enable-rule --name "$R" && echo "enabled $R"; done
for R in $(echo "$RULES"); do aws events describe-rule --name "$R" --query '[Name,State]' --output text; done
# Expected: ENABLED for every rule disabled in step 1

aws ecs describe-services --cluster "${ENV}-optinist-cloud-cluster" \
  --services "${ENV}-optinist-cloud-service" "${ENV}-premium-optinist-cloud-service" \
             "${ENV}-background-optinist-cloud-service" "${ENV}-public-optinist-cloud-service" \
  --query 'services[].{name:serviceName,desired:desiredCount,running:runningCount,
           rollout:deployments[0].rolloutState}'
# Expected: running == desired at the recorded counts, COMPLETED

# Development only
aws rds describe-db-parameter-groups --db-parameter-group-name "$(aws lambda get-function-configuration \
  --function-name "${ENV}-dev-scheduler" --query 'Environment.Variables.RDS_PARAMETER_GROUP_NAME' --output text)" \
  --query 'DBParameterGroups[0].DBParameterGroupFamily'
# Expected: mysql<FROM>. The scheduler's fixed-name snapshot stays on <TO> until the next
# evening stop regenerates it.

PROXY=$(aws rds describe-db-proxies --db-proxy-name "${ENV}-optinist-rds-proxy" \
  --query 'DBProxies[0].Endpoint' --output text)
sql_readings "$PROXY"
# Expected: VERSION() <FROM>.x through the proxy. Metadata is not proof; this is.
```

---

## Edge Case Handling

1. **A rehearsal clone still references the live parameter group.** Terraform's destroy of the group fails with `InvalidDBParameterGroupState`, and any parameter experiment on the clone lands on the live instance. Every clone gets its own copy of the group, and all clones are gone before the apply.
2. **A clone outlives the rehearsal and bills overnight.** The scheduler deletes only its configured identifier. Delete clones by hand, and finish step 3 before the stop: the stop recreates the nightly snapshot under a fixed name, and a restore still reading it can make the stop fail.
3. **The clone claims to be the live instance.** RDS copies tags through the snapshot, so the clone arrives tagged `ManagedBy = terraform` with the live `Name`. Retag it:
   ```bash
   ARN=$(aws rds describe-db-instances --db-instance-identifier "$CLONE" \
     --query 'DBInstances[0].DBInstanceArn' --output text)
   aws rds add-tags-to-resource --resource-name "$ARN" \
     --tags Key=Name,Value="$CLONE" Key=ManagedBy,Value=manual Key=Purpose,Value=rehearsal
   ```
4. **The parameter group destroy fails after a successful upgrade.** RDS still reports the outgoing group in use. Re-run the apply; nothing is lost, the instance is already on the new group. If it persists, something else still references the group (case 1).
5. **The snapshot chain loses the backup retention period.** `BackupRetentionPeriod` is not a restore parameter. Zero means no automatic pre-upgrade snapshot and no PITR. Phase 2 check 2 reads it; it does not decay gradually, so one cycle settles it.
6. **The database is slower after the upgrade.** A major version changes InnoDB defaults, and a family diff shows almost none of them: RDS pins the value in neither family, and the engine default moves underneath (`innodb_io_capacity` went 200 to 10000 between 8.0 and 8.4). Attribute by comparing 0A step 8 against step 9, never by diffing family defaults, and pin what moved. `innodb_buffer_pool_instances` does not follow the pool size and needs its own pin. Compare against the phase 3 baseline: a metric that moved with no corresponding difference in the pair is an application question.
7. **A soak failure is hard to attribute.** Ask whether it reproduces locally first: the docker-compose stacks run the same major version, so a locally reproducible failure is not an upgrade artefact. The one local divergence is `sql_mode`, and it exists identically before and after.

---

## Monitoring and Metrics

| Log group | Contents |
|---|---|
| `/aws/rds/instance/<ENV>-optinist-cloud-rds/error` | RDS error log. Must still receive data after a family change |
| `/aws/rds/proxy/<ENV>-optinist-rds-proxy` | Authentication failures and refused connections |
| `/aws/lambda/<ENV>-dev-scheduler` | Nightly stop and start; where `InvalidParameterCombination` appears if B3 bites |
| `/ecs/<ENV>-optinist-cloud-taskdef`, `/ecs/<ENV>-{background,premium,public}-optinist-cloud-taskdef` | The four application tiers |
| `/aws/lambda/<ENV>-<job>` | The scheduled Lambdas; a caught SQL error is visible only here |

| Metric | Why | Expected after the upgrade |
|---|---|---|
| `ReadIOPS`, `ReadLatency`, `FreeableMemory` | Buffer pool sizing. `BufferCacheHitRatio` is an Aurora metric and returns nothing on RDS MySQL | Unchanged with the pool pinned |
| `WriteIOPS`, `WriteLatency` | Background flushing driven by `innodb_io_capacity` | Unchanged with the capacities pinned |
| `CPUUtilization`, `DatabaseConnections` | Baseline comparison; pool health after a rollout | Unchanged; connections return to the pre-upgrade level |

Alarms `<ENV>-optinist-rds-cpu-high`, `-connections-high` and `-storage-low` must return to OK after an apply.

---

## Configuration

| Terraform attribute | Resource | Role |
|---|---|---|
| `family` | `aws_db_parameter_group.main` | ForceNew; changing it replaces the group |
| `name_prefix` | `aws_db_parameter_group.main` | Replaces `name` so the replacement does not collide (B1) |
| `engine_version` | `aws_db_instance.main` | Major-only value, treated as a prefix |
| `allow_major_version_upgrade`, `apply_immediately` | `aws_db_instance.main` | Temporary (B2). Removed by the cleanup PR |
| `auto_minor_version_upgrade` | `aws_db_instance.main` | Undeclared, so true; a major-only version depends on it |
| `backup_retention_period` | `aws_db_instance.main` | Greater than zero for automatic pre-upgrade snapshots |

Scheduler Lambda environment: `RDS_PARAMETER_GROUP_NAME` (must follow B1's rename), `RDS_SNAPSHOT_ID`, `RDS_SUBNET_GROUP_NAME`, `RDS_SECURITY_GROUP_IDS`, `SCHEDULE_RULE_NAMES`, `DELAYED_RULE_NAMES`.

Scheduled jobs that touch the database. The longest cadence is 24 hours, which is why one nightly cycle covers every scheduled path.

| Cadence | Jobs |
|---|---|
| 5 minutes | `free-manager`, `PublishedExperimentSyncJob` |
| 10 minutes | `common-user-manager` |
| 15 minutes | `premium-manager` |
| 1 hour | `premium-cleanup`, `cost-tracker`, `DataCleanupJob`, `StorageReconciliationJob`, `PremiumExpirationSweepJob` |
| 24 hours | `public-cleanup`, `dev-scheduler`, `ExpirationLifecycleJob` |

In-process intervals are in `studio/app/common/core/subscription/constants.py`; Lambda cadences are the EventBridge rules in `infrastructure/terraform/`.

---

## Testing

The backend pytest lane runs against a containerised MySQL and never reaches the deployed database; it is regression cover, not upgrade verification. Only the deployed e2e lanes reach the upgraded instance, through the RDS Proxy, the same path every ECS task uses.

| Lane | Covers |
|---|---|
| `17-aws-health` | Read-only: instance availability, alarms, encrypted connection, SQL reads through the proxy. Can be pointed at production. #902 adds a case asserting `PendingModifiedValues` is empty, the group is `in-sync`, the version the proxy serves equals the control plane's, and `sql_mode` is unchanged; it names no engine version |
| `15-premium-aws` | Advisory locks through the premium assignment path. Expensive |
| `16-storage-aws` | Storage aggregation against the deployed database. Expensive |
| Browser specs | The application paths, with `E2E_FAIL_ON_SKIP=1` |

The two expensive lanes may be substituted by the environment's standard release set **only if criterion 4 runs in full**: the Lambda packages hold roughly 120 raw `execute()` statements that nothing else executes, and the advisory-lock path also runs on every deploy via `startup_leader.py`. Record the substitution in the phase log. Its one gap is cheap to close: the startup leader election is wrapped in a `try/except`, so a healthy service does not prove its `GET_LOCK` succeeded.

```bash
aws logs filter-log-events --log-group-name "/ecs/${ENV}-public-optinist-cloud-taskdef" \
  --start-time $(( ($(date +%s) - 86400) * 1000 )) \
  --filter-pattern '?"Startup sync" ?"Startup sync error"' \
  --query 'events[].message' --output text | tail -20
# Expected: no "Startup sync error" and at least one line showing the election resolved.
# Empty is not a pass: nothing was exercised.
```

---

## Key Functions Reference

| Function | File | Role |
|---|---|---|
| `restore_rds()` | `infrastructure/terraform/dev_scheduler_package/dev_scheduler.py` | Which values a restore passes explicitly. Passes no engine version (B3) |
| `destroy_rds()` | Same | The nightly delete. Acts only on its configured identifier, with `DeleteAutomatedBackups=False` |
| `ensure_rds_proxy_target()` | Same | Re-registers the proxy target after a restore; why development recovers automatically and production does not |
| `runShellOverSsm()`, `runSql()` | `frontend/e2e/helpers.ts` | The in-VPC mechanism `ssm_sh` reproduces, and SQL through the proxy |

## AWS Resources

| Resource | Name |
|---|---|
| DB instance | `<ENV>-optinist-cloud-rds` |
| DB parameter group | `<ENV>-optinist-ssl-<generated suffix>`; read it from the instance |
| RDS Proxy | `<ENV>-optinist-rds-proxy` |
| Database credentials | Secrets Manager `<ENV>-optinist/database/config` |
| Scheduler Lambda | `<ENV>-dev-scheduler` (development only) |
| In-VPC SSM target | EC2 tagged `<ENV>-optinist-background` |
| ECS cluster and services | `<ENV>-optinist-cloud-cluster`; `<ENV>-optinist-cloud-service`, `<ENV>-premium-...`, `<ENV>-background-...`, `<ENV>-public-...` |

## References

- `DEV_SCHEDULE_GUIDE.md`: the nightly schedule, overrides, manual start and stop
- `INFRA_DEPLOYMENT_PROCEDURE.md`: backend switching, the apply flow, cycling the ECS services
- `MAINTENANCE_PROCEDURES.md`: routine database maintenance
- [Upgrades of the RDS for MySQL DB engine](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_UpgradeDBInstance.MySQL.html): automatic pre-upgrade snapshots; the engine version cannot be reverted
- [Upgrade strategies for Amazon RDS for MySQL 8.0 to 8.4](https://aws.amazon.com/blogs/database/upgrade-strategies-for-amazon-rds-for-mysql-8-0-to-8-4/): the precheck categories and `PrePatchCompatibility.log`
- Issues #877 (parent), #896, #897, #898, #902: the 8.0 to 8.4 execution records, measurements and the reasoning behind the pins
