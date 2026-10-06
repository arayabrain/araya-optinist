# RDS Engine Upgrade: MySQL Major Version Procedure

## Executive Summary

- **This procedure covers an in-place MySQL major version upgrade** of the RDS instances in both environments, using `ModifyDBInstance` rather than a parallel instance or a blue/green deployment.
- **The engine change is two lines of Terraform.** Everything else in this document exists because the surrounding configuration does not tolerate those two lines without preparation.
- **Six constraints (B1 to B6)** must be handled before any apply. They are properties of this stack, not of any particular version pair, so they recur on every major upgrade.
- **The development scheduler's nightly destroy and restore cycle is the dominant constraint.** It makes the upgrade a same-day operation that must not span a night, and it makes the nightly cycle itself a mandatory verification step.
- **Whether a terraform apply is also an application deploy depends on the branch lineage (B6).** On the development lineage every apply rebuilds the image and rolls every ECS service; on the production lineage the apply is the database alone, and the image push and service cycle are separate manual steps.
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
   - **On an environment that is rebuilt on a schedule, the automated retention is not one continuous window.** Each cycle's instance carries its own backup — one `active` row for the current instance, one `retained` row per earlier cycle — so point-in-time recovery reaches any moment *inside* a running day and nothing overnight or on a day the environment did not run. A manual snapshot is what covers the gaps.
   - Deleting the manual snapshot and the retained parameter group is what closes the rollback window, so it is a decision with an agreed duration rather than cleanup.

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

**Two ordering constraints do not follow from the table:**

- **Phase 0's clones must be gone before phase 1**, or the apply cannot destroy the outgoing parameter group.
- **Phase 4 needs two artefacts from earlier phases**: a measured downtime for the announcement — 0B's when it
  ran, otherwise 0A's plus a margin — and phase 3's production baseline metrics, **which cannot be captured
  afterwards.**

---

## Implementation Details

### The six constraints

These are referenced as B1 to B6 from the tracking issues.

#### B1: The parameter group name is fixed, so create_before_destroy collides

**File:** `infrastructure/terraform/infrastructure.tf`

Both `family` and `name` are ForceNew on `aws_db_parameter_group`, so changing the family replaces the resource. With `create_before_destroy = true`, Terraform creates the replacement first, under the same name, and AWS rejects it with `DBParameterGroupAlreadyExists`.

**Fix:** use `name_prefix` in place of `name`, which is durable across future family changes. The `Name` tag is left as it was: nothing reads it, and changing it would add a diff for the gate to account for.

Consumers follow automatically because every reference derives from `aws_db_parameter_group.main.name`. How many there are depends on the environment:

| Consumer | Where | Present when |
|-------------------------|-----------------------|-----------------------|
| The instance's `parameter_group_name` | `infrastructure.tf` | **Always** |
| The scheduler Lambda's `RDS_PARAMETER_GROUP_NAME` | `dev_schedule.tf` | Only with `enable_dev_schedule` |
| The parameter group ARN in the scheduler's IAM policy | `dev_schedule.tf` | Only with `enable_dev_schedule` |

On an environment without the scheduler the instance is the **sole** consumer. Phase 4 has a one-line check
for which case applies.

#### B2: apply_immediately is unset, so the upgrade is deferred and then lost

The provider default is `false`, so `ModifyDBInstance` records the new version in `PendingModifiedValues` and RDS performs the upgrade at the next maintenance window. **Terraform reports a successful update in place either way**, so nothing looks wrong.

On development the maintenance window falls at a time when the scheduler has already deleted the instance. The pending upgrade disappears with it, leaving Terraform state on `<TO>` and the instance on `<FROM>` permanently, while the parameter group has already moved - which then triggers B3 on the next restore.

**Fix:** set `apply_immediately = true` for the upgrade apply. Neither this nor `allow_major_version_upgrade` is read back from the API, so removing them afterwards produces **no diff on `aws_db_instance.main`**. That is the assertion to make — not that the whole plan is empty, which on the development lineage it never is (B6).

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

#### B6: The apply is not an RDS-only change — on one lineage

Whether `null_resource.build_and_deploy` re-runs on an apply depends on its trigger map, which differs by
lineage. **Read it from the plan's `before` triggers (phase 0C), not from the checkout.**

| Lineage | Triggers | An apply from a new commit |
|-------------------------|-----------------------|-----------------------|
| **Development** (`develop-main`) | ALB DNS, ECR repository, `var.git_branch`, **`source_revision`** (the HEAD commit) | **Rebuilds the image and force-deploys every ECS service.** The window must cover the RDS downtime plus a full rollout |
| **Production** (the release lineage) | ALB DNS, ECR repository, `var.git_branch` — a branch name, while the release is a tag | **A no-op.** The apply is the database alone; the image push and the service cycle are separate manual steps |

On the development lineage three things follow. **What gets built is the local working tree, not a branch**:
`ecr_build_push.sh` uses the local checkout as its build context, so the apply ships whatever is in the
working directory — choose a branch cut from what is deployed, carrying only the Terraform change, so a
symptom afterwards has one candidate cause. The script **refuses a dirty worktree**, counting untracked files
at the toplevel, and it runs *after* the RDS modification has been issued. And a `-target` apply scoped to
the RDS resources skips the scheduler Lambda whose parameter group name must follow B1, so it is not a way
out.

**Fix:** confirm `git status --porcelain` is empty at the toplevel, and know what application change rides
along:

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
and is not wanted is pinned here. The values and the count are **version-specific** — six for 8.0 to 8.4 — so
take them from the pair, not from this example (edge case 6).

A major-only `engine_version` resolves to the region default for that major, because
`auto_minor_version_upgrade` is enabled and the provider treats the value as a prefix. **Do not keep
`allow_major_version_upgrade` permanently**: removing it means a future `engine_version` edit cannot perform
a major upgrade silently.

### Resource identity after the upgrade

An in-place upgrade takes the instance offline, upgrades the engine on the same storage, and brings it back.

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

A changed `DbiResourceId` matters in one place: **the RDS Proxy target must be re-registered.** The proxy deregisters its target when the instance is deleted and does not register a replacement on its own. On development `ensure_rds_proxy_target()` does this on the next scheduler start; on production it is a manual `register-db-proxy-targets`. Note that the proxy's `RdsResourceId` is the *instance identifier*, not `DbiResourceId`, so comparing the two proves nothing; the evidence is a target that exists and is `AVAILABLE`.

### Before pasting anything: one line, first

`zsh` does not treat `#` as a comment in interactive input unless `INTERACTIVE_COMMENTS` is set, and it is
off by default. Every block in this document carries comments, so run this before pasting anything else —
the helpers below included:

```bash
setopt interactive_comments 2>/dev/null || true
```

Without it a comment misbehaves in one of three ways, all observed while executing this document:

| Comment | Without the `setopt` |
|-------------------------|-----------------------|
| Trailing, as in `date -u   # t0` | Becomes arguments. `VAR=value   # note` runs `#` as a command and leaves `VAR` unset, silently |
| On its own line, first in a function body | Stored as the function's first command: every call prints `command not found: #` just before its verdict line |
| On its own line inside a `case` | Read as a case pattern — a **hard parse error**. The function does not exist, and the symptom is `command not found: ssm_sh` several steps later |

The third is why `ssm_sh`'s `case` carries no comment. The rest of the session setup — profile, region,
`ENV`, `TF_ENV`, `FROM`, `TO`, `DB` — is under [Procedure](#procedure).

### Shared helper: run a command inside the VPC

Neither RDS instance is publicly accessible and the proxy is TLS-only, so the in-database checks run from an
in-VPC instance over SSM — the mechanism the e2e suite uses in `runShellOverSsm`. The failure branch prints
stdout as well as stderr, because a block that dies partway has already produced its diagnosis.

```bash
# Reads the remote script from stdin and prints its stdout.
ssm_sh() {
  # 'st' rather than 'status': under zsh, status is read-only.
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

# Expected: prints the remote stdout, or the remote stderr and a non-zero status
```

SQL is then run through the `mariadb` client that instance carries, reading credentials from Secrets Manager. The secret id is `${ENV}-optinist/database/config`.

**Every `ssm_sh` heredoc below is unquoted, so the LOCAL shell expands its variables before the script is
sent, and an empty expansion surfaces as a remote error** — with `AWS_REGION` empty, `--region` consumed the
next flag and the instance answered `ParamValidation: argument --region: expected one argument`, which reads
as an SSM or secret fault. Run `need` immediately before any `ssm_sh` heredoc; it names the empty variable
locally, where the fix is:

```bash
need() {
  local v val missing=0
  for v in "$@"; do
    eval "val=\$$v"
    if [ -z "$val" ]; then echo "ABORT: $v is empty" >&2; missing=1
    else printf '%-12s = [%s]\n' "$v" "$val"; fi
  done
  return $missing
}
# Then, before each block, name what that block interpolates. For example:
#   need AWS_REGION ENV PROXY
```

### Shared helper: delete a rehearsal clone safely

The clone and the real instance differ by one variable name in the same shell session - `CLONE` against
`DB` - and in phase 0B that session is pointed at production. A mistyped identifier on `delete-db-instance`
is therefore the single most damaging error available in this procedure, and two API defaults make it worse:
**`--delete-automated-backups` defaults to true**, so the point-in-time window goes with the instance (which
is why `stop_rds()` passes `DeleteAutomatedBackups=False`), and **`--skip-final-snapshot` leaves nothing
behind**, so the fallback is the newest *manual* snapshot, which may be weeks old. Terraform's
`skip_final_snapshot` governs only its own destroy, not a CLI call.

`deletion_protection` is on for production and deliberately off for development, whose scheduler
deletes the instance every weekday. So on development the guards below are the only protection, and on
production a legitimate delete — the rollback — has to turn protection off first.

**Never call `delete-db-instance` directly in this procedure.** Every identifier it may delete carries
`rehearsal` in its name; that one predicate covers clones, their snapshots and their parameter groups alike.

```bash
# Refuses anything that is not a rehearsal-scoped identifier. The name is the only thing
# standing between a typo and a deleted database, so assert on it rather than on care.
assert_rehearsal() {
  case "$1" in
    *rehearsal*) return 0 ;;
    *) echo "REFUSING: '$1' is not a rehearsal-scoped identifier" >&2; return 1 ;;
  esac
}

# Both destructive helpers show the target's real attributes and require a typed
# confirmation: the predicate says "this could be a clone", the preview says which.
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

# Deletes a clone, but keeps a final snapshot and the automated backups: the habit is
# what makes a mistyped identifier survivable rather than terminal.
drop_clone() {
  assert_rehearsal "$1" || return 1
  confirm_target instance "$1" || return 1
  aws rds delete-db-instance --db-instance-identifier "$1" \
    --final-db-snapshot-identifier "$1-final" --no-delete-automated-backups
  aws rds wait db-instance-deleted --db-instance-identifier "$1"
}

# Snapshots need the guard more: there is no final-snapshot fallback for a snapshot, so
# an accepted-but-wrong delete here is unrecoverable.
drop_snapshot() {
  assert_rehearsal "$1" || return 1
  confirm_target snapshot "$1" || return 1
  aws rds delete-db-snapshot --db-snapshot-identifier "$1"
}
```

Neither guard protects against a name you deliberately gave a real asset: do not put `rehearsal` in the name
of anything you intend to keep.

#### Verifying the guards before use

Verify both guards before relying on them, with `ENV` set (see [Procedure](#procedure)). All four checks must
hold. **Never pass a real identifier to `drop_clone` or `drop_snapshot` as a test**: if the predicate is
broken - the thing being checked - the wrapper goes on to describe the real resource and prompt, and a live
database is one keystroke away. So the predicate is tested directly on real names, and the wrappers only on
names that do not exist.

```bash
echo "${ENV:?set ENV before testing the guards}"
DB=${ENV}-optinist-cloud-rds

# 0. These are shell functions: they do not survive a new terminal, and a stale copy is
#    worse than none. Re-paste all four, then read one back.
declare -f drop_clone
# Expected: it calls assert_rehearsal, then confirm_target, and passes
# --no-delete-automated-backups. Anything missing means an older revision is loaded.

# 1. The accept set must be empty of real assets before starting
aws rds describe-db-instances \
  --query 'DBInstances[?contains(DBInstanceIdentifier,`rehearsal`)].DBInstanceIdentifier' --output text
aws rds describe-db-snapshots --snapshot-type manual \
  --query 'DBSnapshots[?contains(DBSnapshotIdentifier,`rehearsal`)].DBSnapshotIdentifier' --output text
# Expected: empty for both. Anything listed here is inside the guards' accept set and
# must be renamed or accounted for before going further.

# 2. The predicate refuses real identifiers. Called directly, so there is no code path
#    to a delete even if it were broken.
assert_rehearsal "$DB"
assert_rehearsal "$(aws rds describe-db-snapshots --db-instance-identifier "$DB" \
  --snapshot-type manual --query 'DBSnapshots[0].DBSnapshotIdentifier' --output text)"
# Expected: REFUSING twice, naming each identifier. The second one is a snapshot that
# really exists, which is the case that matters - it has no final-snapshot fallback.

# 3. The wrappers consult the predicate. The strings deliberately resemble no real
#    identifier, so editing one cannot produce a live name.
drop_clone    "guard-test-instance-must-be-refused"
drop_snapshot "guard-test-snapshot-must-be-refused"
# Expected: REFUSING twice, naming each string. No preview, no prompt, nothing sent to
# AWS. A preview appearing here means the predicate is not wired into the wrapper - abort.

# 4. The accept path works, on the identifier phase 0A will really pass to drop_clone.
#    What protects this call is that the clone does not exist yet.
drop_clone "${ENV}-optinist-rds-upgrade-rehearsal"
# Expected: past assert_rehearsal, then confirm_target fails to describe an instance
# that does not exist yet and returns before prompting. Nothing is deleted.
# If the clone DOES already exist - re-running in a new shell partway through 0A - this
# reaches the prompt instead: answer with anything but the identifier. Still a pass.
```

### Shared helper: sanitise a log before posting it

Execution logs are posted to the tracking issues, which are public. Filter them rather than masking by hand:
**editing a comment does not unpublish anything** - GitHub keeps prior revisions readable, GH Archive records
public issue events permanently, and notification emails have already gone out.

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
    -e 's/db\.[a-z0-9]+\.[a-z]+/<INSTANCE_CLASS>/g' \
    -e 's/ip-[0-9]+-[0-9]+-[0-9]+-[0-9]+/<PRIVATE_DNS>/g' \
    -e 's/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/<UUID>/g'
}

# Expected: the same text with identifiers replaced. Durations, engine versions,
# sql_mode values, precheck findings, metric values and cost figures are left intact -
# acceptance criteria are written against them.
```

**Three rules are anchored on `(^|[^[:alnum:]-])` because BSD `sed -E` has no `\b`** - it does not error on
one, it simply never matches and exits 0, so with `\b` the three mandatory rows (the environment prefix,
`i-…`, `db-…`) passed through while the other five fired and the output looked filtered. The capture group
preserves the consumed character; the `^` alternative keeps a match at the start of a line. **Verify any
change to these rules against a line that exercises all eight**, including an identifier at the very start.

The filter is a convenience, not a guarantee: it does not know about values it has never seen. Check the
result before posting.

**This table is the single source for the substitution policy.** The tracking issues reference it rather
than restating it.

| In the output | Post as | Why |
|-------------------------|-----------------------|-----------------------|
| The environment prefix | `<ENV>` | The convention the repository's earlier execution logs use |
| The AWS account id | `<ACCOUNT_ID>` | Enables cross-account enumeration |
| The proxy endpoint's account token | `proxy-<TOKEN>` | A resolvable hostname |
| An **instance** endpoint's account token - the label between the identifier and the region | `<TOKEN>` | Same reason. Most steps never print an endpoint; step 10 does, because proving which database it reached is the point of it |
| Instance ids and `DbiResourceId` values | `i-A`, `db-A` | Per-resource identifiers |
| Instance class, volume type and size | Generalise | Capacity information: it lets someone size an attack without reconnaissance |
| SSM association ids | `<ASSOC_A>`, `<ASSOC_B>` | Per-resource identifiers, the same class as `i-` and `db-`. They surfaced in phase 4's pre-work |
| A private DNS name or private IP | Generalise | Subnet layout. The in-VPC smoke test prints the instance's own hostname |
| A database username from the tfvars | Generalise | A configuration value, not a resource name |
| **Durations, engine versions, `sql_mode`, precheck findings, metric values, cost figures** | **Keep as measured** | Outcome measurements. Later phases and the acceptance criteria are written against them, so redacting these breaks the work's own definition of done |

Resource *name patterns* are fine with `<ENV>` substituted - `<ENV>-optinist-cloud-rds`,
`/ecs/<ENV>-optinist-cloud-taskdef` and the like. They are names, not capacity, and access to them is
governed by IAM.

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
setopt interactive_comments 2>/dev/null || true
echo "profile=$AWS_PROFILE env=$ENV tf_env=$TF_ENV from=$FROM to=$TO db=$DB"
```

**Then read the live parameter group's name, rather than assuming a literal.** The block above makes no AWS
call; this one does, and it is the first thing every phase that touches the parameter group needs.

```bash
LIVE_PG=$(aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].DBParameterGroups[0].DBParameterGroupName' --output text)
aws rds describe-db-parameter-groups --db-parameter-group-name "$LIVE_PG" \
  --query 'DBParameterGroups[0].{name:DBParameterGroupName,family:DBParameterGroupFamily}'
# Expected: a name, and family mysql<FROM>.
#
# A mysql<TO> family means the apply has ALREADY run, so this group is not a valid copy
# source for a rollback group - stop and re-read what you are doing.
```

> **The name is read, not written.** Before the first upgrade the group is `<ENV>-optinist-ssl`; after it,
> `name_prefix` has replaced that with a generated name (B1), so a literal is correct exactly once.
> **Re-read `LIVE_PG` after any apply that touches the group.** Within a phase it is stable.

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

**Run this block first; the rest of the document assumes it.** `<FROM>`, `<TO>` and `<ENV>` remain in prose
and in expected-output comments as placeholders; every *command* uses `$FROM`, `$TO` and `$ENV`. A handful of
commands still carry an angle-bracket placeholder, each a value only the operator can supply:

| Where | Placeholder |
|-------------------------|-----------------------|
| This block | The profile, the environment, **the backend/tfvars basename**, and the two versions. `ENV` and `TF_ENV` are two separate substitutions and they differ on production |
| Phase 0C's setup | The same, plus the deployment checkout's path |
| **B6**, comparing what is deployed | The deployed ref |
| **Phase 3, criterion 6's baseline** | Production's instance identifier and profile, as `PROD_DB` / `PROD_PROFILE`. Deliberately not `DB`: that step reads production from a shell pointed at development, and reassigning `DB` is how the capture silently becomes the wrong environment's |
| Phase 5, sweeping both environments | The other environment's name |
| Rollback, choosing a restore source | The snapshot id, and the service counts recorded earlier |

Anywhere else, a placeholder left in a command is a defect in this document rather than something to
fill in.

The setup block repeats the `setopt`; see [Before pasting anything](#before-pasting-anything-one-line-first)
for what goes wrong without it.

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

Know what the worst case already costs before any phase runs: the newest manual snapshot is what an accidental
delete falls back to, and it is usually older than assumed. **Run it for the environment the next phase
operates on**, not both — production's floor belongs to phase 4, and reading it earlier puts a production
API call inside a phase that otherwise makes none.

```bash
# Automated backups: the point-in-time window, and whether there is one at all
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{retention:BackupRetentionPeriod,delProt:DeletionProtection}'
# Expected: retention greater than zero. Zero means no automated backups and no PITR.
# DeletionProtection: true on production, false on development.

aws rds describe-db-instance-automated-backups \
  --query "DBInstanceAutomatedBackups[?DBInstanceIdentifier=='${DB}'].{status:Status,
           from:RestoreWindow.EarliestTime,to:RestoreWindow.LatestTime}" --output table
# Expected: read the whole list, not just the active row. On an instance rebuilt on a
# schedule the active row spans only the current instance's lifetime, and each earlier
# cycle is its own `retained` row - see Key Architectural Principles, 5.

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
# SNAP is the scheduler's nightly snapshot.
SNAP=${DB}-dev-scheduler
```

#### The upgrade — steps 1 to 11

Restore a clone, upgrade it, and measure what changed. **Only step 3 carries a deadline** (edge case 2: a
restore still reading the source snapshot when the scheduled stop recreates it); after step 3 nothing in 0A
reads that snapshot again.

##### Step 1 — confirm the source snapshot

```bash
# Confirm the source snapshot is usable and on the outgoing version
aws rds describe-db-snapshots --db-snapshot-identifier "$SNAP" \
  --query 'DBSnapshots[0].{status:Status,ev:EngineVersion,created:SnapshotCreateTime}'
# Expected: available, <FROM>.x. Read the timestamp: the scheduled stop recreates this
# snapshot, so on a Monday it is Friday's. Record which day it is.
```

##### Step 2 — a private parameter group for the clone

```bash
# A private parameter group for the clone, copied from the live one.
#    Do not attach the live group - see edge case 1.
aws rds copy-db-parameter-group \
  --source-db-parameter-group-identifier "$LIVE_PG" \
  --target-db-parameter-group-identifier rehearsal-ssl-from \
  --target-db-parameter-group-description "outgoing-family rehearsal copy"

#    The copy's output does not show the user-set parameters, so check them: a copy that
#    lost require_secure_transport makes step 8's "connection succeeded" meaningless.
aws rds describe-db-parameters --db-parameter-group-name rehearsal-ssl-from --source user \
  --query 'Parameters[].{name:ParameterName,value:ParameterValue}' --output table
# Expected: the same user-set parameters as the live group - compare against
# `describe-db-parameters --db-parameter-group-name "$LIVE_PG" --source user`
```

##### Step 3 — restore the clone

```bash
# Read the restore parameters from the live instance rather than hardcoding them.
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
```

##### Step 4 — confirm what the clone came up as

```bash
# Neither of the two things that matter here is in the restore call's own response.
aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].{status:DBInstanceStatus,retention:BackupRetentionPeriod,
           rid:DbiResourceId,pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus}'
# Expected: available, retention greater than zero, and pg rehearsal-ssl-from / in-sync.
# retention zero: RDS takes no automatic pre-upgrade snapshot and the drill cannot run.
# pg still the DEFAULT group: normal while the restore is creating, but once available
# it means the clone is NOT carrying require_secure_transport - attach the group with
# modify-db-instance before continuing.
# Note DbiResourceId: the drill asserts it changed after its restore.
```

##### Step 5 — resolve the target version and family, and build the target parameter group

```bash
# Resolve the target version and its family from the API, the way the provider resolves
#    `engine_version = "<TO>"`: the region default for that major.
read -r TARGET TO_FAMILY <<<"$(aws rds describe-db-engine-versions --engine mysql \
  --engine-version "$TO" --default-only --output text \
  --query 'DBEngineVersions[0].[EngineVersion,DBParameterGroupFamily]')"
echo "target=$TARGET family=$TO_FAMILY"
# Expected: both non-empty, e.g. a concrete x.y.z and mysql<TO>. Empty means the major
# version has no default in this region - stop.

#    Confirm the resolved version is reachable from where the clone is now.
aws rds describe-db-engine-versions --engine mysql \
  --engine-version "$(aws rds describe-db-instances --db-instance-identifier "$CLONE" \
    --query 'DBInstances[0].EngineVersion' --output text)" \
  --query "DBEngineVersions[0].ValidUpgradeTarget[?EngineVersion=='${TARGET}'].{v:EngineVersion,
           major:IsMajorVersionUpgrade,auto:AutoUpgrade}" --output table
# Expected: one row, IsMajorVersionUpgrade true, AutoUpgrade false. An empty table means
# the resolved default is not reachable from here - stop and pick from the full
# ValidUpgradeTarget list instead.

#    Record TARGET: phase 1 must land on the same version, and the duration measured
#    below is only comparable for the same version.

#    The target-family group is built, with the parameters read from the live group
#    rather than retyped.
aws rds create-db-parameter-group --db-parameter-group-name rehearsal-ssl-to \
  --db-parameter-group-family "$TO_FAMILY" --description "target-family rehearsal"

#    JSON, not the shorthand ParameterName=x,ParameterValue=y form: a value may be an
#    RDS expression such as {DBInstanceClassMemory*3/4}, and the shorthand parser fails
#    on the brace client-side ("Expected: '=', received: '*'"). Shell quoting cannot help.
SRC_JSON=$(aws rds describe-db-parameters --db-parameter-group-name "$LIVE_PG" \
  --source user --query 'Parameters[].[ParameterName,ParameterValue,ApplyType]' --output text \
  | jq -Rn '[inputs | split("\t")
      | {ParameterName: .[0], ParameterValue: .[1],
         ApplyMethod: (if .[2] == "static" then "pending-reboot" else "immediate" end)}]')
printf '%s\n' "$SRC_JSON" | python3 -m json.tool
# Expected: one object per user-set parameter in the live group. An empty list means the
# source group name is wrong - stop, or the TLS check in step 8 passes for the wrong reason.

aws rds modify-db-parameter-group --db-parameter-group-name rehearsal-ssl-to \
  --parameters "$SRC_JSON"

#    Read it back: the modify call reports only the group name.
aws rds describe-db-parameters --db-parameter-group-name rehearsal-ssl-to --source user \
  --query 'Parameters[].{name:ParameterName,value:ParameterValue,applyType:ApplyType}' --output table
# Expected: the same names and values as the live group. A parameter missing here does
# not exist in the new family - a finding for the upgrade itself.
```

##### Step 6 — upgrade, and record the duration

```bash
# Upgrade, and record the duration. TARGET was resolved in step 5.
#    The family change and the version change go in ONE ModifyDBInstance - that ordering
#    is B3, rehearsed here before it matters on a live instance.
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

The same output shows the pre-check's own start and finish, and the automatic pre-upgrade backups either
side of it — the snapshots the drill restores from.

**A waiter timeout is not a failure.** `aws rds wait db-instance-available` gives up after 30 minutes and
exits non-zero while the upgrade carries on. If it errors, do not re-issue the modify — poll instead:

```bash
while :; do
  s=$(aws rds describe-db-instances --db-instance-identifier "$CLONE" \
    --query 'DBInstances[0].[DBInstanceStatus,EngineVersion]' --output text)
  echo "$(date -u +%H:%M:%S) $s"
  case "$s" in available*) break ;; esac
  sleep 60
done
date -u
# Expected: the status walks through upgrading / modifying / configuring-* and settles on
# available with the new version. Then read the duration from the event stream above.
```

##### Step 7 — the precheck log

From the DB log file API, so the clone needs no log exports. **The log does not exist until the upgrade has
run**, so an empty result means the upgrade has not finished, not that the check was clean. **List before
fetching**: filtering on one guessed name silently produces an empty file, which reads like "no problems".

```bash
aws rds describe-db-log-files --db-instance-identifier "$CLONE" \
  --query 'DescribeDBLogFiles[].{name:LogFileName,size:Size,written:LastWritten}' --output table
# Expected: an upgrade-related file - `mysqlUpgrade`, or `PrePatchCompatibility.log` when
# the engine produces one - with a LastWritten from the upgrade just run. If neither is
# newer than the upgrade, wait and list again.

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

# The log ends with its own tally. Read that, not a grep count of "error", which also
# matches check titles such as "Checks for errors in column definitions".
grep -E '^(Errors|Warnings|Database Objects Affected):' /tmp/prepatch.log
awk -F': *' '/^Errors:/{print ($2==0 ? "PASS: Errors=0" : "ABORT: Errors=" $2)}' /tmp/prepatch.log
# Expected: PASS. A non-zero Errors count fails the same way on the real instance and
# aborts the whole plan.

# Warnings do not block, but every one gets read: they are the behaviour that changes.
grep -nE '^[0-9]+\)|^\tNo issues found' /tmp/prepatch.log
# Expected: every check accounted for. Record what each non-"No issues found" detail
# means for this stack in the phase's log.
```

##### Step 8 — the in-database state

```bash
CLONE_HOST=$(aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].Endpoint.Address' --output text)

need AWS_REGION ENV CLONE_HOST

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
SELECT PLUGIN_NAME, PLUGIN_STATUS, LOAD_OPTION FROM information_schema.PLUGINS
 WHERE PLUGIN_NAME LIKE '%native_password%';
SELECT @@innodb_buffer_pool_size, @@innodb_dedicated_server, @@binlog_format, @@log_output;
SELECT @@innodb_io_capacity, @@innodb_io_capacity_max, @@innodb_adaptive_hash_index,
       @@innodb_change_buffering, @@innodb_buffer_pool_instances;
SELECT @@innodb_redo_log_capacity, @@innodb_flush_method, @@innodb_log_writer_threads,
       @@innodb_buffer_pool_chunk_size;
SELECT user, host, plugin FROM mysql.user ORDER BY user;
SQL
SH
```

`mysql_native_password` is a **server option, not a system variable** (`SELECT @@mysql_native_password` fails with `ERROR 1193`), so its configured value is read from the parameter group:

```bash
aws rds describe-db-parameters --db-parameter-group-name rehearsal-ssl-to \
  --query "Parameters[?ParameterName=='mysql_native_password'].[ParameterValue,ApplyType,IsModifiable]" \
  --output text
# Expected: ON  static  False - RDS pins it on. An empty result means the parameter does
# not exist in this family: see the auth row in the table below.
```

**The client aborts on the first SQL error and `set -e` then discards the whole block.** All of these are `SELECT`s, so fix the statement and re-run. A variable that does not exist on one version raises `ERROR 1193`: drop that line for that side.

| Query | Expected | What it settles |
|-------------------------|-----------------------|-----------------------|
| `VERSION()` | `<TO>.x` | The upgrade reached the engine, not just `PendingModifiedValues` |
| `@@sql_mode` | Unchanged from `<FROM>` | Query acceptance behaviour does not change across the upgrade |
| `information_schema.PLUGINS` for `%native_password%` | A row, `ACTIVE` | The precheck warns the plugin is off by default in upstream `<TO>`. This says whether the running server still has it |
| **The connection itself** | Succeeded | The strongest evidence available here, and it costs nothing: if `mysql.user` shows the connecting account on `mysql_native_password`, then a successful login **is** proof the plugin authenticates on `<TO>`. Read the two rows together rather than either alone |
| `@@innodb_buffer_pool_size`, `@@innodb_dedicated_server` | May change together | The buffer pool sizing change - see edge case 6. `@@innodb_buffer_pool_instances` follows it automatically, because the engine forces it to 1 below a 1 GiB pool |
| `@@innodb_redo_log_capacity` | May change | Same class as the buffer pool: a family may pin it where the next leaves `innodb_dedicated_server` to derive it. A smaller redo log checkpoints more often, which is write IO - and it compounds with any `innodb_io_capacity` increase |
| `@@innodb_io_capacity` and the other InnoDB values | **May change, and the RDS family diff does not predict it** | These are parameters RDS pins in neither family, so the *engine* default changes underneath. Comparing family defaults alone misses them entirely, which is why they are read from the running server |
| `@@binlog_format` | May change to `ROW` | Harmless with no replicas and no external consumers |
| `mysql.user` plugins | Existing users keep their plugin | The proxy's client auth type keeps working. The precheck flags these users as using a deprecated method - flagging is not breaking |

The connection succeeding at all is the `require_secure_transport` check: TLS against a group that requires it, on the new family.

##### Step 9 — the same readings on the live `<FROM>` instance

Step 8 says what the new version does; only the pair says what **changed**, and the "before" half exists only
while the live instance is on `<FROM>`. Run it any time before phase 1: **step 8's block with `LIVE_HOST` in
place of `CLONE_HOST`**, dropping the two authentication statements (`PLUGINS` and `mysql.user`), which the
`<FROM>` side cannot inform.

```bash
LIVE_HOST=$(aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].Endpoint.Address' --output text)
need AWS_REGION ENV LIVE_HOST
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
# A formula such as {DBInstanceClassMemory*3/4} on one side and nothing on the other is
# the whole difference. Empty on both sides means the engine derives it, and the measured
# numbers above are the only evidence.
```

Record the two sets side by side. **A difference in `innodb_buffer_pool_size` is the one that changes a decision** - see edge case 6.

##### Step 10 — alembic

The only step that exercises the application's own stack - pydantic settings, SQLAlchemy, pymysql, alembic -
against the new engine, running the same `alembic upgrade head` the deploy runs. On a clone already at head
the migration is a **no-op**; what is proved is that the runner connects, authenticates, negotiates TLS and
executes.

Three things about the block, each a mistake it is written around:

- **A `docker exec` does not inherit the entrypoint's environment.** `DatabaseConfig` reads `MYSQL_*`, which
  `cloud-startup.sh` exports from the task definition's `DB_*` only in its own process tree. Left alone,
  pymysql falls back to `root` and fails with `1045 Access denied` - not an engine problem. So the block
  repeats the mapping from the container's own `DB_*` values, and overrides `MYSQL_SERVER` (not `DB_HOST`,
  which has already been consumed).
- **The target is proved first, and the proof aborts rather than prints.** The deployed configuration points
  `MYSQL_SERVER` at the proxy, which fronts the **live** instance. A successful connection to the wrong
  database is the case `set -e` cannot see, and a version printed for a human would be read after the
  migration had run.
- **`docker exec` needs `-i`.** Without it stdin is not forwarded, the shell reads an empty program, exits 0,
  and SSM reports `Success` with nothing run. The same applies to every `docker exec … sh -s` or `python -`
  in this document.

```bash
need CLONE_HOST TO

ssm_sh <<SH
set -e
C=\$(docker ps --format '{{.Names}}' | grep -m1 -- -background-optinist-cloud-container)

docker exec -i -e MYSQL_SERVER=$CLONE_HOST -w /app "\$C" sh -s <<'INNER'
set -e
# Repeat the entrypoint's DB_* to MYSQL_* mapping, which a docker exec does not inherit.
# MYSQL_SERVER is not mapped here: it comes from the -e above, pointing at the clone.
export MYSQL_USER="\$DB_USER" MYSQL_PASSWORD="\$DB_PASSWORD" MYSQL_DATABASE="\$DB_NAME"
[ -n "\$MYSQL_USER" ] && [ -n "\$MYSQL_DATABASE" ] || {
  echo "ABORT: DB_USER / DB_NAME are absent from this container" >&2; exit 1; }

# 1. Prove the target, through the application's own configuration and connection,
#    and ABORT here if it is not the clone. Same engine construction as the SSL
#    check in cloud-startup.sh.
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

# 2. Only then migrate. set -e and the exit above are what stop this line.
alembic current && alembic heads && alembic upgrade head && alembic current
INNER
SH
# Expected, in order:
#   MYSQL_SERVER is the clone's endpoint, and VERSION() is <TO>.x.
#   current and heads report the same revision, and current is unchanged afterwards.
#
# Failure modes:
#   "ABORT: DB_USER / DB_NAME are absent" - read the container's environment
#     (docker exec "$C" env | grep -E '^(DB_|MYSQL_)') and fix the mapping, not the credentials.
#   "1045 Access denied for user 'root'" - the mapping did not happen. Not an engine problem.
#   "ABORT: expected ... got <FROM>.x" - the override did not take effect and this reached
#     the live instance through the proxy. Nothing was migrated. Re-read CLONE_HOST.
#   An empty MYSQL_SERVER, then a connection error - CLONE_HOST was unset; the client fell
#     back to localhost inside the container. Nothing was migrated.
#   No output at all - the container name did not match, and set -e aborted on C.
#   current BEHIND heads - a migration really applied. Harmless on a clone; investigate
#     why a clone of a live database was not at head before trusting the rest of 0A.
```

**Why this belongs in phase 0 although a failure is fixed in the application.** `cloud-startup.sh` runs
`alembic upgrade head` on every container start and exits non-zero if it fails, so the phase 1 apply would
find the same failure - but ECS's revert to the previous task definition **does not undo the engine
upgrade**, the previous image fails against `<TO>` identically, and recovery is a snapshot rollback or a
hotfix under time pressure. In phase 4 that is a production outage. Here it costs one command against a
throwaway clone, while the decision is still "do not apply yet".

##### Step 11 — rehearse the parameter pins the apply will carry

Where the step 8 / step 9 pair shows a default that moved and is not wanted, the phase 1 parameter group pins
it back (edge case 6). **This step proves the pins produce the intended running state on the clone, before
the apply relies on them** - that a formula such as `{DBInstanceClassMemory*3/4}` evaluates on the new
family, that the values resolve as intended together, and that a `static` parameter reaches the running
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
printf '%s\n' "$PINS_JSON" | python3 -m json.tool
# Expected: valid JSON, one object per pin, each expression intact.

# 1. Prove the group is attached to nothing but the clone: most of these pins are dynamic
#    and would take effect on a live instance using the group. The filter reads every
#    attached group, not just DBParameterGroups[0].
aws rds describe-db-instances \
  --query "DBInstances[?DBParameterGroups[?DBParameterGroupName=='${PG}']].DBInstanceIdentifier" \
  --output text
# Expected: exactly the clone's identifier, and nothing else. Anything else aborts.

# 2. Apply the pins behind both guards, chained with && so a refusal stops the modify.
#    Every pin is pending-reboot, including the dynamic ones, so one reboot applies them
#    together rather than leaving a half-configured server.
assert_rehearsal "$PG" && assert_rehearsal "$CLONE" && \
  aws rds modify-db-parameter-group --db-parameter-group-name "$PG" --parameters "$PINS_JSON"

aws rds describe-db-parameters --db-parameter-group-name "$PG" --source user \
  --query 'Parameters[].[ParameterName,ParameterValue,ApplyMethod]' --output text
# Expected: the pins read back, plus what the group already carried. One bad value fails
# the whole modify, so if a pin is missing, stop - there is nothing waiting to be applied.

aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].DBParameterGroups[0].ParameterApplyStatus' --output text
# Expected: pending-reboot - the "before" half of the reboot check.

# 3. Reboot, which a static parameter needs. reboot-db-instance returns at once and RDS
#    can take minutes to begin the shutdown (about two and a half here), reporting
#    available throughout - so a waiter returns immediately on the pre-reboot state, and
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
# Expected: "restarted at ..." within a few minutes. If the ten-minute loop ends without
# printing, the reboot has not happened and the readings below would be the PRE-reboot
# ones - the <TO> defaults, which reads as the pins having failed.

# The full event sequence, for the record.
aws rds describe-events --source-identifier "$CLONE" --source-type db-instance \
  --duration 15 --query 'Events[].[Date,Message]' --output text

aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].{status:DBInstanceStatus,pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus}'
# Expected: available, the rehearsal group, and pgs now in-sync rather than
# pending-reboot. incompatible-parameters means one of the pins is not viable.
```

Then re-run **step 8's SQL block**: the pinned variables should now report the `<FROM>` figures on a `<TO>` server.

A bad `static` parameter can leave the instance in `incompatible-parameters`, unable to start. On a throwaway clone that costs nothing - finding it here rather than on the live instance is the point - and it does not block the drill, which restores from the clone's pre-upgrade snapshot and deletes an instance in that state without trouble. Record which pin caused it and drop it from the phase 1 group.

#### The rollback drill — steps 12 to 14

**This group is the reason 0A exists**: the only place the rollback is executed rather than described. It
rehearses [the rollback procedure](#the-rollback-procedure)'s steps 0 and 2 — the restore source, and the
delete-and-restore under the same identifier. Steps 1, 3, 4 and 5 (traffic, the proxy target, Terraform, the
scheduler) have nothing to act on with a clone and are exercised only in an actual rollback, which is why the
Rollback section is written to be read on its own.

**0A can be split across days here.** The drill restores from the clone's *own* automatic pre-upgrade
snapshot, so the scheduled stop no longer constrains anything; a reasonable split is steps 1 to 10 on one
day and step 11, the drill and the teardown on the next. Nothing touches the clone overnight — the scheduler
acts on the one identifier in its `RDS_INSTANCE_ID`, not on tags (edge case 3) — but it keeps billing.

If the terminal stayed open, the shell still holds every variable and helper; **credentials expire while the
shell does not**, so expect an `ExpiredToken` on the first call. Verify rather than assume:

```bash
echo "env=$ENV from=$FROM to=$TO db=$DB clone=$CLONE"
for f in ssm_sh assert_rehearsal confirm_target drop_clone drop_snapshot redact; do
  declare -f "$f" >/dev/null 2>&1 && echo "$f: present" || echo "$f: MISSING - re-paste it"
done
aws sts get-caller-identity --query Account --output text
```

To resume **in a new shell**, re-run the session setup from the top of Procedure, re-paste the helpers, then:

```bash
DB=${ENV}-optinist-cloud-rds
CLONE=${ENV}-optinist-rds-upgrade-rehearsal
declare -f drop_clone
# Expected: assert_rehearsal, confirm_target, --no-delete-automated-backups.

# The drill's restore needs these. The values are the same even though the live
# instance is a different one after an overnight cycle.
read -r CLASS STORAGE SUBNET SG <<<"$(aws rds describe-db-instances \
  --db-instance-identifier "$DB" --output text \
  --query 'DBInstances[0].[DBInstanceClass,StorageType,DBSubnetGroup.DBSubnetGroupName,
           VpcSecurityGroups[0].VpcSecurityGroupId]')"

aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].{status:DBInstanceStatus,ev:EngineVersion,
           pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus,
           endpoint:Endpoint.Address}'
# Expected: available, <TO>.x, the target-family rehearsal group, in-sync. The endpoint
# is unchanged by a reboot.
```

Steps 8 to 11 need the in-VPC path, so wait for the environment's morning start before resuming them; the
drill and the teardown use only the RDS API.

##### Steps 12 to 14 — the rollback drill

**This runs the [Rollback](#rollback) procedure's steps 0 and 2 against the clone, not a second copy of
them.** Read that section and substitute:

| Rollback uses | Here |
|-------------------------|-----------------------|
| `$DB` | **`$CLONE`** |
| `$ROLLBACK_PG` | **`rehearsal-ssl-from`** |
| A **manual** pre-upgrade snapshot, with automated ones as the fallback | **An automated one.** The clone has no manual snapshot, so the selection below replaces step 0's listing |
| Clearing `deletion_protection` first | **Not needed.** The clone never had it |

Four things are specific to the drill:

```bash
# 1. SELECT the restore source. --snapshot-type automated returns the daily backups as
#    well as the pre-upgrade ones, so more than one can match the version filter.
aws rds describe-db-snapshots --db-instance-identifier "$CLONE" --snapshot-type automated \
  --query 'reverse(sort_by(DBSnapshots,&SnapshotCreateTime))[].[SnapshotCreateTime,
           EngineVersion,DBSnapshotIdentifier]' --output text
# Expected: the pre-upgrade snapshots on <FROM>, recognisable by "preupgrade" in the
# identifier, plus any daily backup. Anything on <TO> is not a rollback point.

PRE=$(aws rds describe-db-snapshots --db-instance-identifier "$CLONE" \
  --snapshot-type automated \
  --query "reverse(sort_by(DBSnapshots[?starts_with(EngineVersion,'${FROM}')],
           &SnapshotCreateTime))[0].DBSnapshotIdentifier" --output text)
echo "$PRE"
# Expected: the newest <FROM> entry, one of the pre-upgrade ones. "None" means RDS took
# none - check step 4's retention period.

# Everything the restore needs, checked while the clone is still here: the drill deletes
# before it restores, so a blank value is a failed restore with nothing to retry from.
[ -n "$PRE" ] && [ "$PRE" != None ] || echo "ABORT: no ${FROM} snapshot to restore from"
aws rds describe-db-snapshots --db-snapshot-identifier "$PRE" \
  --query 'DBSnapshots[0].{status:Status,ev:EngineVersion,created:SnapshotCreateTime}'
# Expected: available, <FROM>.x. One still "creating" is not a restore source.
aws rds describe-db-parameter-groups --db-parameter-group-name rehearsal-ssl-from \
  --query 'DBParameterGroups[0].[DBParameterGroupName,DBParameterGroupFamily]' --output text
# Expected: the group, on the <FROM> family. Its absence is B3 waiting to happen.
echo "class=$CLASS storage=$STORAGE subnet=$SUBNET sg=$SG"
# Expected: all four non-empty.

# 2. GUARD both calls, chained with && - at a shell's top level `|| return 1` is not a
#    reliable stop. The restore is guarded too: on a mistyped identifier that does not
#    exist, an unguarded restore succeeds and creates an instance nobody wanted.
# 3. TIME both halves. t1-t0 is the delete, t2-t1 the restore.
date -u                                                    # t0
assert_rehearsal "$CLONE" && \
  aws rds delete-db-instance --db-instance-identifier "$CLONE" \
    --final-db-snapshot-identifier "${CLONE}-broken" --no-delete-automated-backups
aws rds wait db-instance-deleted --db-instance-identifier "$CLONE"
date -u                                                    # t1

# 4. CONFIRM the restore source outlived the delete. This is the property the whole
#    rollback depends on, and --no-delete-automated-backups defaults to true.
aws rds describe-db-snapshots --db-snapshot-identifier "$PRE" \
  --query 'DBSnapshots[0].{status:Status,ev:EngineVersion}'
# Expected: still available, still <FROM>.x. If it has gone the drill cannot continue -
# and nothing is lost, because ${CLONE}-broken holds the <TO> state.

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
# Expected: <FROM>.x; the endpoint hostname identical to before the drill; DbiResourceId
# DIFFERENT (why a real rollback re-registers the proxy target); the outgoing-family
# group, in-sync. default.mysql<FROM> here means --db-parameter-group-name did not take
# and the instance is not carrying require_secure_transport.
```

**Record t0, t1 and t2** — the delete and the restore are separate numbers in a rollback decision, and only
the second is unavoidable. Wall clock is right here, unlike step 6: nothing can use the instance until the
restore completes.

**Metadata is not proof.** Run step 8's SQL block once more, re-reading `CLONE_HOST` first. One connection
proves TLS against the outgoing-family group (the only evidence it carried `require_secure_transport`
through the restore), `VERSION()` at `<FROM>.x` from the engine, and the `<FROM>` baseline reproducing itself
from a snapshot. **This is the last opportunity — the teardown removes the instance.**

#### Teardown

As soon as the drill is finished, and in any case before phase 1: a clone still referencing the live
parameter group makes the phase 1 apply fail to destroy it (edge case 1).

```bash
drop_clone "$CLONE"

# drop_clone keeps the final snapshot and the automated backups on purpose; clean them
# up here rather than by weakening the delete. The final snapshot can still be creating
# when the instance delete completes, and delete-db-snapshot refuses it in that state.
aws rds wait db-snapshot-available --db-snapshot-identifier "${CLONE}-final"
drop_snapshot "${CLONE}-final"
drop_snapshot "${CLONE}-broken"

# The retained automated backups: TWO sets, because the clone was deleted twice (the
# drill, then drop_clone), each with --no-delete-automated-backups, so each left a set
# under the same identifier with a different DbiResourceId.
#
# This loop is the one destructive step with no per-item confirmation, and its only
# input is an identifier. Pointed at the live instance it would delete the environment's
# entire retained backup history. Hence the guard, and listing before deleting.
assert_rehearsal "$CLONE" && \
  aws rds describe-db-instance-automated-backups \
    --query "DBInstanceAutomatedBackups[?DBInstanceIdentifier=='${CLONE}'].[DBInstanceAutomatedBackupsArn,
             DbiResourceId,Status]" --output text
# Expected: two rows, with the two DbiResourceId values this clone had. More than two, or
# an unfamiliar resource id, means the filter is matching something else. Stop.

assert_rehearsal "$CLONE" && \
  for ARN in $(aws rds describe-db-instance-automated-backups \
    --query "DBInstanceAutomatedBackups[?DBInstanceIdentifier=='${CLONE}'].DBInstanceAutomatedBackupsArn" \
    --output text); do
    aws rds delete-db-instance-automated-backup --db-instance-automated-backups-arn "$ARN"
  done

aws rds delete-db-parameter-group --db-parameter-group-name rehearsal-ssl-from
aws rds delete-db-parameter-group --db-parameter-group-name rehearsal-ssl-to

# Expected: nothing rehearsal-shaped remains, on any of the four kinds of resource this
# phase created. The first three are the sweep phase 0 ends with ("Whether 0B ran or
# not"), so run that block, plus:
aws rds describe-db-instance-automated-backups \
  --query "DBInstanceAutomatedBackups[?DBInstanceIdentifier=='${CLONE}'].[DBInstanceAutomatedBackupsArn,Status]" \
  --output text
```

### Phase 0B: rehearse on a clone of production - conditional

**The only phase before phase 4 that touches production, and not always necessary.** Decide with the two
gates below. Skipping it keeps the whole of phase 0 inside development, which removes an occasion for a
mistyped identifier against production.

#### Gate 1: does the production schema differ from development's?

The precheck is mostly schema-driven, and the schema is whatever alembic has applied. **If both environments
are at the same revision, 0A's precheck already covered production's schema.** They are not automatically the
same: the environments track different branches.

```bash
# Read-only. One query per environment, through the proxy.
# Run the ssm_sh SQL block from phase 0A with this statement, once per environment.
SELECT version_num FROM alembic_version;
# Expected: the same revision in both. If they differ, production's schema is not what
# 0A tested and 0B should run.
```

Even when the revisions match, the **data-dependent** table-validation checks run over rows development does
not have. Gate 2 prices that residual.

#### Gate 2: how different are the data volumes?

The announced downtime comes from 0A unless production holds materially more data.

```bash
for E in "$ENV" <the other environment>; do
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

**If 0B is skipped, the precheck runs for the first time in phase 4.** It fails safe — the upgrade does not
complete and the instance stays on `<FROM>`, so it costs the window rather than the data — but phase 4 needs a
stated response for that outcome.

#### If 0B runs

0A's sequence with three differences: `ENV` and the SSM target point at production; the source is a fresh
manual snapshot of production, **named with `rehearsal` in it** so the teardown guards accept it; and the
rollback drill is not repeated. **Creating that snapshot is the only operation against production** — the
exposure is the session, in which `DB` is production.

```bash
# Re-run the session setup with ENV set to production, then:
export SSM_NAME=${ENV}-optinist-background
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

Then 0A steps 2 to 8, with `rehearsal-ssl-from` copied from production's group, and 0A's teardown — there is
no `${CLONE}-broken` snapshot and one retained backup set rather than two — plus the source snapshot:

```bash
drop_snapshot "$SNAP"
# $SNAP is a snapshot of PRODUCTION, and the newest manual snapshot is what an accidental
# instance delete falls back to. The guard accepts only rehearsal-scoped names, which is
# why the source snapshot was named that way.
```

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

`terraform plan` is read-only, so the B4 gate can run days ahead of the phase 1 window, with time to fix
what it finds. Three conditions:

- **The Terraform edit must be present in the tree being planned**, committed (phase 1 needs a clean
  worktree, B6). An unmodified checkout produces "no changes" and the gate proves nothing.
- **Run it from the directory Terraform actually applies from** — a separate deployment checkout is a common
  arrangement. Confirm `infrastructure.tf` *there* carries the edit.
- **Expect more in the plan than the RDS changes, and record every resource it contains.** The deploy
  trigger's `before` map is what settles B6 for this lineage, and the checked-out configuration is not
  evidence of it. A trigger key present in `before` and absent in `after` means the configuration being
  applied is *older* than the one in state: the resource is replaced once and later applies skip the build,
  a regression invisible unless the two maps are compared. `aws_ecs_cluster.main` updating its tags from
  `data.external.tf_build_info` is benign.

This usually runs in a different terminal and directory from the AWS CLI work, so it has its own setup. It
needs no delete guards and no `FROM`.

```bash
export AWS_PROFILE=<the profile for this account>
export AWS_REGION=ap-northeast-1
export ENV=<the Terraform var.environment value>
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
# Expected: the development environment's value. Stop if it is anything else: the next
# command attaches this directory to that environment's remote state.
```

```bash
terraform init -backend-config="backends/${TF_ENV}.hcl" -reconfigure

# plan takes a state lock; let it finish. The plan file goes outside the tree - see
# phase 1 step 7 for the two reasons.
PLAN=$(mktemp -t tfplan-dryrun)
terraform plan -var-file="environments/${TF_ENV}.tfvars" -out="$PLAN"

terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.type=="aws_db_instance")
  | "\(.address) -> \(.change.actions|join(","))"'
# Expected: aws_db_instance.main -> update
# Any create, delete or "create,delete" aborts the work

terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.type=="aws_db_parameter_group")
  | "\(.address) -> \(.change.actions|join(","))"'
# Expected: one create and one delete - the create_before_destroy replacement

terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.type=="aws_db_parameter_group"
    and (.change.actions|index("create")))
  | {name: .change.after.name, name_prefix: .change.after.name_prefix,
     family: .change.after.family}'
# Expected: name null (known after apply), name_prefix set, family mysql<TO>.
# A literal name means B1 is unfixed and the apply will fail.

# The pinned parameters. An RDS expression such as {DBInstanceClassMemory*3/4} is the one
# to watch: a bare brace is literal in HCL, a mistyped ${...} is interpolation.
terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.type=="aws_db_parameter_group"
    and (.change.actions|index("create")))
  | .change.after.parameter[]? | "\(.name) = \(.value) [\(.apply_method)]"'
# Expected: one line per pin, values intact, plus the parameters the group already had.
# Missing pins here mean the plan is for a different change than phase 1 will apply.

# The deploy trigger, before and after: whether the apply rebuilds the image (B6).
terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.address=="null_resource.build_and_deploy")
  | {before: .change.before.triggers, after: .change.after.triggers}'
# Expected: the two maps differ in exactly the way the change explains. A key in before
# and absent in after means the configuration is OLDER than the state - see above.

# Every resource the plan touches, attributed. replace_paths names the attribute that
# forces each replacement: "our change did this" against "this was already drifting".
terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.change.actions != ["no-op"])
  | "\(.address) -> \(.change.actions|join(","))  replace_paths=\(.change.replace_paths // [])"' | sort
# Expected from the engine change: the instance updating in place, the parameter group
# replaced on family and name_prefix, and whatever derives the group's name. Everything
# else is drift and rides along with the apply - an AMI data source with most_recent
# replaces its instance whenever the upstream image is rebuilt, and can take network
# egress with it. Triage the list before deciding to apply.

rm -f "$PLAN"
```

0C cannot prove the replacement succeeds - a name collision only surfaces at apply time.

### Phase 1: apply to development

Weekdays, while the environment is up, and finishing before the scheduled stop (B3, B4).

**Fourteen steps in three groups.** The numbering is what phase 4 cites, as **"phase 1 step N"**, rather than
restating them. **Run them in one shell session**: `PRESNAP` (step 4),
`PLAN` (step 7), `NEWPG` and `LIVE_HOST` (step 12) are set in one step and read in a later one.

#### Before the apply — steps 1 to 6

Nothing here changes the database. **Steps 1 and 6 cannot be done afterwards.**

##### Step 1 — the `<FROM>` readings exist, and the pins are decided

```bash
# The pins must already be in the Terraform change. A pin absent here is a tuning change
# that ships silently with the version change, and the family diff will not show it.
grep -oE 'name += +"innodb_[a-z_]+"' infrastructure/terraform/infrastructure.tf | sort
# Expected: one line per pin the phase 0A step 8 / step 9 pair justified. For 8.0 -> 8.4
# that is six: dedicated_server, buffer_pool_size, buffer_pool_instances,
# redo_log_capacity, io_capacity, io_capacity_max.

# The "before" half of the pair. On development it came from 0A step 9; on production
# there is no 0A, so take it here - after the apply the <FROM> instance no longer exists.
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{ev:EngineVersion,endpoint:Endpoint.Address}'
# Expected: <FROM>.x. Then run 0A step 8's SQL block against that endpoint, unless the
# readings are already on file.
```

##### Step 2 — read the stop schedule, and extend it only if needed

```bash
# The deadline: an apply interrupted by the scheduled stop is B4.
aws events describe-rule --name "${ENV}-dev-schedule-stop" \
  --query '[Name,ScheduleExpression,State]' --output text
# Expected: the stop cron, and ENABLED. Convert it to local time; finishing before it is
# the plan, the override below the fallback.
```

The override is capped at twelve hours **from the moment it is set**, so one set in the morning cannot reach
an evening stop. Set it only when an overrun becomes likely, and late enough to clear the deadline.

```bash
aws lambda invoke --function-name "${ENV}-dev-scheduler" \
  --payload '{"action":"override","hours":12}' \
  --cli-binary-format raw-in-base64-out /dev/stdout
# Expected, in the FUNCTION's response rather than the invoke's own status:
#   {"statusCode": 200, "action": "override", "hours": N, "expires_at": "..."}
# A 400 with "OVERRIDE_PARAM_NAME not configured" is the silent failure: the invoke
# reports success while nothing was set. Compare expires_at against the stop time; the
# function has no way to query the override's state, so expires_at is the evidence.
# The next scheduled start clears the override, so it cannot leak into phase 2's cycles.
```

**Production has no scheduler, so phase 4 skips this step** — the one step of the fourteen that does not
carry over.

##### Step 3 — the instance is available

```bash
# Anything but available aborts (B4).
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{status:DBInstanceStatus,ev:EngineVersion}'
# Expected: available, <FROM>.x
```

##### Step 4 — the manual pre-upgrade snapshot

A manual snapshot is the **only** recovery point that survives an instance delete. It is not complete until
it reads `available`.

```bash
# PRESNAP, not SNAP: SNAP elsewhere names a restore *source* that gets deleted (0B ends
# with drop_snapshot "$SNAP"), and two meanings in one session is how a recovery point
# gets deleted by a line copied from another phase.
# THE NAME IS A REQUIREMENT. The Rollback procedure finds this snapshot by searching for
# the literal `pre-upgrade`, because the shell that held $PRESNAP may be gone by then. A
# release-habit name (`<ENV>-optinist-pre-v<version>-<date>`) does not contain it, and
# the rollback then reports no recovery point at all, during an incident.
PRESNAP=${ENV}-optinist-pre-upgrade-$(date +%Y%m%d-%H%M)
aws rds create-db-snapshot --db-instance-identifier "$DB" --db-snapshot-identifier "$PRESNAP"
aws rds wait db-snapshot-available --db-snapshot-identifier "$PRESNAP"
aws rds describe-db-snapshots --db-snapshot-identifier "$PRESNAP" \
  --query 'DBSnapshots[0].{id:DBSnapshotIdentifier,status:Status,ev:EngineVersion,
           created:SnapshotCreateTime}'
# Expected: available, <FROM>.x. This is the rollback point - do not proceed on a
# snapshot still reported as creating.
```

##### Step 5 — retain an outgoing-family parameter group

The apply destroys the managed group, and the outgoing engine cannot use the new family. A rollback needs a
group to attach. **Both verifications are part of the step**: a copy that silently did not happen is
otherwise discovered during the restore, after it has been committed. `copy-db-parameter-group` is not
idempotent, so if the copy was made early, verify it rather than re-run it.

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
# Expected: found == 2 - require_secure_transport = 1 and time_zone = UTC, the live
# group's user-set parameters. `found` is in the query because a `Parameters[]` query over
# a copy that carried nothing prints an empty table and exits 0, and a copy that lost
# require_secure_transport restores an instance that rejects the application's TLS-only
# connections. The same `found`/`length()` pattern guards every count in this document.
```

##### Step 6 — confirm the recovery points are real, before applying

Two of the four recovery points cannot be verified directly - the automatic pre-upgrade snapshots do not
exist until the upgrade takes them - so verify their **precondition**. The table of what each one restores to
is in [What you can roll back to after this apply](#what-you-can-roll-back-to-after-this-apply).

```bash
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{retention:BackupRetentionPeriod,delProt:DeletionProtection,
           window:PreferredBackupWindow}'
# Expected: retention greater than zero. Zero means RDS takes no automatic pre-upgrade
# snapshot at all. Re-read it today: a restore can silently drop it (edge case 5).

aws rds describe-db-instance-automated-backups \
  --query "DBInstanceAutomatedBackups[?DBInstanceIdentifier=='${DB}'].{status:Status,
           rid:DbiResourceId,from:RestoreWindow.EarliestTime,to:RestoreWindow.LatestTime}" \
  --output table
# Expected: one `active` row for the instance running now, whose window runs to a few
# minutes ago - the row that covers "just before this apply". `retained` rows are earlier
# cycles.
```

#### The gate — steps 7 to 9

Steps 7 and 8 are mechanical. **Step 9 is judgement.**

##### Step 7 — clean worktree, then plan to a file outside the tree

```bash
git status --porcelain
# Expected: empty (B6)

# From the repository root: a bare `cd infrastructure/terraform` fails from inside that
# directory, and a failed cd leaves the shell where it was.
cd "$(git rev-parse --show-toplevel)/infrastructure/terraform"
pwd

# PHASE 4 ONLY: the backend was already switched and PROVED at phase 4's own block. Do
# not repeat the init here without repeating the two proofs.
terraform init -backend-config="backends/${TF_ENV}.hcl" -reconfigure

# The plan file goes OUTSIDE the working tree. mktemp gives it 0600, and a plan contains
# every variable value; and on the development lineage an untracked file makes the tree
# dirty, which ecr_build_push.sh refuses - AFTER the RDS modification has been issued.
PLAN=$(mktemp -t tfplan)
terraform plan -var-file="environments/${TF_ENV}.tfvars" -out="$PLAN"
```

##### Step 8 — the two gate assertions

Run the phase 0C assertions against `"$PLAN"`, **including the attribution step**. The gate passes on two
resources - `aws_db_instance.main` showing `update` rather than a replacement, and the parameter group
showing one create and one delete. The plan will contain more, and every entry applies.

**Judge the instance by the SET of changed attributes, never by their count.** How many attributes differ
depends on what the state holds, and on whether the provider renders a diff for each. The criterion: the
action is `update` and not a replacement, and **every** changed attribute is one the configuration intends -
`engine_version`, and whichever of `allow_major_version_upgrade`, `apply_immediately` and
`deletion_protection` the state does not already match. **An attribute outside that set stops the window; a
set smaller than expected does not.** Read the state before the window so the expected set is known.

##### Step 9 — classify the whole plan by root cause

**Before applying.** The extra entries are separate decisions riding along, and the phase log records which
bucket each falls in:

| Bucket | How to recognise it | What to do |
|-------------------------|-----------------------|-----------------------|
| The engine change | The instance, the parameter group, and whatever derives the group's name | Intended |
| The deploy (**B6**) | `null_resource` replacements whose `triggers` carry the git commit | Expected. Its rollout time belongs in the announced window |
| Runtime state versus configuration | A count or an instance the configuration declares statically while something scales it at runtime | Decide deliberately. The apply will overwrite the runtime state |
| Drift | An AMI data source with `most_recent`, a JSON body, a recomputed hash | Usually harmless, but read the ones that replace rather than update |

**A replacement is not an update.** Anything showing `delete,create` is destroyed before its successor exists.
A NAT instance or gateway in that state takes the private subnets with it, and the in-VPC checks in steps 11
to 14 fail during that window: **retry them once the apply settles**, and record the gap so a soak metric can
be attributed to it rather than to the engine.

#### The apply and what it must prove — steps 10 to 14

**Step 10 is the irreversible one.** Steps 11 to 14 are four separate claims.

##### Step 10 — apply

```bash
terraform apply "$PLAN"
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

**Measure this environment's own outage here, from the event stream** — the only moment it can be taken, and
the figure the next run of this procedure quotes. On production it also settles the `app_setup` SSM
association's result (its database wait is five minutes) and corrects the announcement just made.

```bash
# NOT the wall clock - see 0A step 6 for the three intervals to read.
aws rds describe-events --source-identifier "$DB" --source-type db-instance \
  --duration 120 --query 'Events[].{t:Date,msg:Message}' --output table
# --duration is in MINUTES: widen it if the apply started earlier, because an empty table
# reads as "no events" rather than as "the window moved".
```

##### Step 12 — the pins took effect

Skip only if the 0A pair found nothing worth pinning. **A pin that did not apply looks exactly like a clean
apply**: the group shows the value, the instance shows `available`, and the engine runs the `<TO>` default.

```bash
# The group's name is generated by name_prefix (B1), so read it.
NEWPG=$(aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].DBParameterGroups[0].DBParameterGroupName' --output text)

# The configured side, and whether anything is still waiting for a reboot.
aws rds describe-db-parameters --db-parameter-group-name "$NEWPG" --source user \
  --query '{found:length(Parameters),
            params:Parameters[].[ParameterName,ParameterValue,ApplyType,ApplyMethod]}'
# Expected: found == 8 for this upgrade - require_secure_transport, time_zone, and the six
# pins. Count rather than scan: a shorter list reads exactly like a correct one.
# A `static` pin not in effect below needs a reboot; the upgrade's own should have served.

# The running side: 0A step 8's block with LIVE_HOST in place of CLONE_HOST.
LIVE_HOST=$(aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].Endpoint.Address' --output text)
need AWS_REGION ENV LIVE_HOST
# Expected: every pinned variable reports the <FROM> figure from the 0A pair. Derived
# values follow their pin - innodb_buffer_pool_instances comes back with the pool.
```

Record three columns next to 0A's pair - `<FROM>` measured, `<TO>` default, `<TO>` pinned. That is what
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
# Expected: found == 1, AVAILABLE across the apply. `id` is the instance identifier, not
# DbiResourceId (see Resource identity after the upgrade).
```

An in-place upgrade does not move `DbiResourceId`, so no re-registration should be needed - confirm it. A
restore does move it, which is why the rollback has a re-registration step.

#### What you can roll back to after this apply

Reference for step 6, which checks the preconditions of the last two rows.

| Recovery point | Created by | Expires | Restores to |
|-------------------------|-----------------------|-----------------------|-----------------------|
| `<ENV>-optinist-pre-upgrade-<date>` | **Step 4** | **Never** - manual snapshots persist until deleted | `<FROM>`, the state immediately before the apply |
| `<ENV>-optinist-ssl-rollback` | **Step 5** | Never | The parameter group that `<FROM>` instance needs |
| Automatic pre-upgrade snapshots | RDS, up to two, immediately before the upgrade | With the retention period | `<FROM>` |
| Point-in-time recovery | The automated backup | With the retention period | Any point inside a running day on development (Key Architectural Principles, 5); any point on production |

The manual snapshot from step 4 is the primary recovery point regardless of the window shape: its contents
are known, it is found by name, and it survives an instance delete.

**A rollback after a night has passed must also revert the scheduler Lambda's parameter group** — the evening
stop has replaced the fixed-name snapshot with a `<TO>` one, and the Lambda points at the `<TO>`-family group,
so the next nightly restore would aim the new family at an old snapshot (B3 in reverse). The
[Rollback](#rollback) procedure's step 4 covers it.

### Phase 2: the nightly destroy and restore cycle

This proves B3: **an incoming-version snapshot restored against the incoming-family group**, the path every
later restore of the upgraded environment takes. The cycle runs whether anyone watches it or not, so the
choice is between a twenty-minute deliberate check and a broken environment in the morning — edge case 5 and
the proxy re-registration have both failed on this stack. It is not a gate on production's upgrade, which has
no nightly cycle, nor on the rollback, which 0A drills directly.

**Six checks in two sittings**: check 1 in the evening after the stop, checks 2 to 6 after the morning
restore — `stop_rds()` *deletes* the instance, so the morning group run in the evening returns
`DBInstanceNotFound`.

#### The evening, after the stop — check 1

```bash
# 1. The evening snapshot must itself be on the new version
aws rds describe-db-snapshots --db-snapshot-identifier "${DB}-dev-scheduler" \
  --query 'DBSnapshots[0].{ev:EngineVersion,status:Status,created:SnapshotCreateTime}'
# Expected: <TO>.x, available, tonight's timestamp.
# Still <FROM> means the upgrade never completed.
```

The snapshot it reads is the one the morning restore consumes, so a `<FROM>` reading here predicts tomorrow's
failure.

#### The morning, after the restore — checks 2 to 6

One block: `SINCE` is set in check 3 and read in checks 4 and 5b. **Check 6 is the engine's account of the
restore** where the others are the control plane's: reading the pinned values back through the proxy is the
only proof the restore carried them.

```bash
# 2. The morning restore
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{ev:EngineVersion,status:DBInstanceStatus,
           pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus,pending:PendingModifiedValues,
           retention:BackupRetentionPeriod}'
# Expected: <TO>.x, available, in-sync, pending == {}, retention unchanged.
# Retention is the one property the snapshot chain can lose - see edge case 5.

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

# 5. The proxy target exists and is healthy. The proxy auto-deregisters when the
#    instance is deleted and does not auto-register one reappearing under the same
#    identifier - so what this shows is that a target exists at all.
aws rds describe-db-proxy-targets --db-proxy-name "${ENV}-optinist-rds-proxy" \
  --query 'Targets[].{id:RdsResourceId,state:TargetHealth.State,reason:TargetHealth.Reason}'
# Expected: one target, AVAILABLE

# 5b. The re-registration itself - the part that has failed on this stack before -
#     reported under "rds_proxy" in the scheduler's start results.
aws logs filter-log-events --log-group-name "/aws/lambda/${ENV}-dev-scheduler" \
  --start-time "$SINCE" --filter-pattern 'rds_proxy' \
  --query 'events[].message' --output text | tail -5
# Expected: "deferred_still_creating" on the first start pass, then "registered" on the
# verify-start - the mechanism working as designed. "already_registered" means no
# deregistration happened, which is worth understanding. "deferred_still_creating" on
# the VERIFY pass is an error: the restore is stuck.

# 6. The application reaches it through the proxy: 0A step 8's block with this endpoint
#    in place of CLONE_HOST.
aws rds describe-db-proxies --db-proxy-name "${ENV}-optinist-rds-proxy" \
  --query 'DBProxies[0].Endpoint' --output text
# Expected: the pinned values at their <FROM> figures. A failure here but not in check 2
# is a proxy auth problem, not an engine one.
```

**One cycle is the requirement; a second adds no coverage.** `restore_rds()` passes every restore parameter
explicitly — including the parameter group — so the snapshot id is the only input that varies between cycles
and the call reads nothing about the snapshot's provenance. The one property not passed, `BackupRetentionPeriod`
(edge case 5), is what check 2 measures on the restored instance, so the loop closes inside one cycle. A
second cycle is assurance for this environment's own nightly operation, not a gate.

### Phase 3: soak to exit criteria

**One night is the floor and the exit is a checklist, not a date.** Nothing this phase protects against is
found by elapsed time: every scheduled path has a cadence of 24 hours or less (see
[Configuration](#scheduled-jobs-that-touch-the-database)), so one nightly cycle covers all of them, an
application SQL incompatibility is found by exercising code paths, and development carries no load for a
performance regression to show on. **Finish the phase by running the criteria, not by letting time pass.**

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

```bash
# Separate the invocation status from the response payload: with both on stdout they
# interleave, and it is the status that carries FunctionError.
inv() {
  # 'st' rather than 'status': under zsh, status is read-only.
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
```

**The verdict needs both conditions.** A Lambda that raises still returns `StatusCode: 200`, with
`"FunctionError": "Unhandled"` beside it; and a `ResourceNotFoundException` never reaches the handler, so its
error text carries no `FunctionError` at all. The helper requires a 200 **and** no `FunctionError`, and prints
one verdict line per invoke.

```bash
inv free-manager        "{$SCHED,\"detail\":{\"action\":\"monitor\"}}"
inv premium-manager     "{$SCHED,\"detail\":{\"action\":\"monitor\"}}"
inv premium-cleanup     "{$SCHED,\"detail\":{\"action\":\"cleanup\"}}"
inv common-user-manager "{$SCHED,\"detail\":{\"action\":\"manage_users\"}}"
inv cost-tracker   '{}'
inv public-cleanup '{}'
# Expected: ok on every line. public-cleanup is otherwise daily, so this removes a
# full day of waiting.
```

**`free_cleanup` is reached by none of this.** It holds about fifteen raw statements, has no EventBridge schedule and no visible invoker. Record it as an uncovered path rather than invoking it blind: it deletes, and firing a deletion Lambda whose trigger and payload are not established is a worse risk than the gap.

The in-process background jobs have no Lambda, so call them directly:

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
# Expected: FAILED: none on the last line. ExpirationLifecycleJob runs on a 24-hour
# interval, so this is the invoke that saves the most waiting.
```

**Silence is not a pass** — without `-i` the block exits 0 having run nothing (0A step 10). The run is valid
only when all three markers appear: `container: ecs-<ENV>-background-...` (the lookup resolved), **five**
`== <JobName>` lines (the interpreter received its program), and `FAILED: none` on the last line (every job
was attempted, each caught individually so one raising does not end the loop). A correct run takes minutes,
not seconds.

#### Criterion 5 — the log queries

**Choose the window from the apply, not the clock.** On an environment with a nightly stop a 24-hour window
always contains a band of `9501 Timed-out waiting to acquire database connection` from the Lambdas running
while the instance is deleted. That band predates the upgrade. Anchor "is this still happening?" queries
after the apply settled:

```bash
SINCE=$(( ($(date +%s) - 24*3600) * 1000 ))
# And, for anything where the question is "is this still happening?", the moment the
# rollout finished rather than 24 hours ago:
AFTER=$(( $(date -j -f '%Y-%m-%d %H:%M:%S' '<YYYY-MM-DD HH:MM:SS>' +%s) * 1000 ))
echo "AFTER=$AFTER  ($(date -r $((AFTER/1000)) '+%F %T'))"

# CloudWatch reports times as epoch milliseconds, which are unreadable next to the
# apply's own timestamps. 'ts_fmt' rather than 'fmt': fmt(1) is a real command.
# The empty-field skip matters: a logged message ending in a newline arrives as two
# lines, and the blank one would otherwise print as 1970.
ts_fmt() {
  while IFS=$'\t' read -r ts rest; do
    [ -n "$ts" ] || continue
    printf '%s  %s\n' "$(date -r $((ts/1000)) '+%Y-%m-%d %H:%M:%S')" "$rest"
  done
}
```

Production has no nightly stop, so **a `9501` in production's window is a finding**. And **filter on exception
class names, not numeric error codes**: CloudWatch matches a quoted string as a substring, so `1290` matches
`clientConnection=1290121671`.

```bash
aws logs describe-log-streams \
  --log-group-name "/aws/rds/instance/${DB}/error" \
  --order-by LastEventTime --descending --max-items 3 \
  --query 'logStreams[].[lastEventTimestamp,logStreamName]' --output text | ts_fmt
# Expected: a lastEventTimestamp AFTER the apply. This is the one query here where
# silence is the failure: it means the export did not survive the family change.
```

```bash
aws logs filter-log-events --log-group-name "/aws/rds/proxy/${ENV}-optinist-rds-proxy" \
  --start-time "$SINCE" \
  --filter-pattern '?"Access denied" ?"Authentication failed" ?ConnectionRefusedError ?"error 1290"' \
  --query 'events[].message' --output text
# Expected: empty
```

```bash
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
# Expected: every number zero. Several zeros per Lambda is normal - the query is
# applied per page of a paginated call.
```

**Count, do not print, when the question is "any left?"**: `head` shows the *oldest* matches and silently
truncates; `length(events)` cannot mislead that way.

**The startup leader election needs its own query.** It is wrapped in a `try/except` that logs and returns,
so a service at `running == desired` does not prove its `GET_LOCK` succeeded — and that lock is one of the
hand-written SQL sites the lane substitution relies on.

```bash
aws logs filter-log-events --log-group-name "/ecs/${ENV}-public-optinist-cloud-taskdef" \
  --start-time "$SINCE" \
  --filter-pattern '?"Startup sync" ?"Startup sync error"' \
  --query 'events[].message' --output text | tail -20
# Expected: no `Startup sync error`, and at least one line showing the election resolved -
# either this task won and ran the sync, or it deferred to the leader.
#
# An EMPTY result is not a pass: it means nothing was exercised, so the lock was never
# taken and this check has told you nothing.
```

```bash
aws logs filter-log-events --log-group-name "/ecs/${ENV}-background-optinist-cloud-taskdef" \
  --start-time "$SINCE" \
  --filter-pattern '?storage_tracking ?premium_expiration ?StorageReconciliation ?DataCleanup ?ExpirationLifecycle' \
  --query 'events[].[timestamp,message]' --output text | ts_fmt | tail -30
# Expected: NOT empty, with entries after the apply. This is separate evidence from
# criterion 4: it shows the container's own scheduler running these on the new
# version unattended, rather than only responding to a manual invoke.
```

**Read that last one for what each job did.** `ExpirationLifecycleJob` logs `No remote bucket configured,
skipping expiration lifecycle` where none is set, so it counts as invoked, not exercised. The `general` and
`slowquery` exports are off at the engine default and are no signal either way.

#### Criterion 6 — the production baseline

Capture it for **production** before phase 4; it cannot be retaken afterwards. Every command is read-only.
The risks are reading or keeping the *wrong thing*: the only step in phase 3 that reads production, from a
shell whose every variable points at development — so **use `PROD_DB` and `PROD_PROFILE`, pass `--profile`
inline, and never reassign `$DB` or `export AWS_PROFILE`**, which would hand a later `terraform` command a
production context. The capture goes to a file under `$HOME`, not `mktemp`: `$TMPDIR` is swept, and this
reading has to survive until phase 5's comparison weeks later.

```bash
# The only hand-substitution point in this step. PROD_* deliberately.
PROD_DB=<production db instance identifier>
PROD_PROFILE=<production profile name>
OUT=$HOME/baseline-production-$(date +%Y%m%d-%H%M).txt
```

```bash
# Refuse to capture unless this is production and still on the outgoing version.
assert_prod() {
  local ev
  ev=$(aws rds describe-db-instances --profile "$PROD_PROFILE" \
         --db-instance-identifier "$PROD_DB" \
         --query 'DBInstances[0].EngineVersion' --output text) || return 1
  case "$ev" in
    ${FROM}.*) return 0 ;;
    *) echo "ABORT: $PROD_DB reports $ev, expected ${FROM}.x" >&2
       echo "       wrong instance, or already upgraded" >&2
       return 1 ;;
  esac
}
```

```bash
# Confirm the identity before capturing. A development identifier here stops the step.
aws rds describe-db-instances --profile "$PROD_PROFILE" \
  --db-instance-identifier "$PROD_DB" \
  --query 'DBInstances[0].{id:DBInstanceIdentifier,ev:EngineVersion,
           status:DBInstanceStatus,pg:DBParameterGroups[0].DBParameterGroupName}'
# Expected: production's identifier, <FROM>.x, available.
```

```bash
# Guarded with && so a failed gate creates no file at all.
assert_prod && {
  for M in CPUUtilization ReadIOPS WriteIOPS DatabaseConnections \
           BufferCacheHitRatio ReadLatency WriteLatency FreeableMemory; do
    echo "== $M"
    aws cloudwatch get-metric-statistics --profile "$PROD_PROFILE" \
      --namespace AWS/RDS --metric-name "$M" \
      --dimensions "Name=DBInstanceIdentifier,Value=$PROD_DB" \
      --start-time "$(date -u -v-14d +%Y-%m-%dT%H:%M:%SZ)" \
      --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      --period 86400 --statistics Average Maximum \
      --query 'sort_by(Datapoints,&Timestamp)[].{t:Timestamp,avg:Average,max:Maximum}' \
      --output table
  done
} 2>&1 | tee "$OUT"
```

```bash
# The capture is only captured once it is on disk. An empty file is not a reading.
wc -l "$OUT" && grep -c "^== " "$OUT"
# Expected: eight headings, and 14 daily points under each.
```

**`BufferCacheHitRatio` returns no datapoints on standard RDS MySQL** — it is an Aurora metric — so its
section comes back empty, as expected; `ReadIOPS`, `ReadLatency` and `FreeableMemory` carry the buffer-pool
question in its place (see [Metrics to compare](#metrics-to-compare-against-the-baseline)). When posting the
capture, redact the instance identifier and **keep the metric values**.

### Phase 4: apply to production

No scheduler, so the timing is free, but the instance is offline for the whole upgrade.

**On this lineage the apply is the database change alone (B6).** `var.git_branch` is a branch name and the
release is a tag, so no trigger of `null_resource.build_and_deploy` changes; the four launch templates show
no diff and the free tier's ASG does not refresh. **Announce the measured upgrade duration plus a margin** —
0B's figure if it ran, otherwise 0A's — and re-read `var.git_branch` if the release process changes, because
the other lineage's rollout costs tens of minutes on top.

**So the release is two actions**: the apply, and a manual image push and ECS service cycle afterwards.
**Neither this document nor the release procedure states the combined order, and following either alone
drops steps from the other** — write the combined order into the window's tracking issue beforehand. Two
things it has to add to this document: a same-morning `<FROM>` health-lane baseline before the plan, and a
re-read of the ECS and proxy checks after the service cycle.

**If 0B was skipped, the precheck runs here for the first time.** It fails safe — the instance stays on
`<FROM>` and the cost is the window — and the response is decided in advance, not in the window: capture the
log, attempt no in-window fix, finish the rest of the release, and take a second window within days.

**Run phase 1 steps 1 to 14 with `ENV` set to the production value**, with these differences:

| | On production |
|-------------------------|-----------------------|
| **`TF_ENV`, not `ENV`, names the backend and tfvars files** | `$ENV` is `subscr`; the files are `backends/production.hcl` and `environments/production.tfvars`. The two coincide on development, which is why the mistake is invisible there (see [Procedure](#procedure)) |
| **Step 1** — the `<FROM>` readings and the pins | **The only place the "before" half can be taken**: production has no phase 0A, and after the apply the `<FROM>` instance is gone. It changes nothing, so complete it before the window |
| **Step 2** — the stop schedule and the override | **Skipped.** No scheduler, no deadline |
| **Step 13** — the scheduler Lambda's parameter group | **Skipped.** Where the scheduler is absent `aws_db_instance.main` is the sole consumer of the generated name (B1), and the apply covers it |
| **Step 7's `terraform init`** | **Not repeated.** The backend is switched and proved in this phase's own block below |

**Check the scheduler's absence rather than assuming it.** Every resource in `dev_schedule.tf` is gated by
`count = var.enable_dev_schedule ? 1 : 0`, the variable defaults to `false`, and neither tfvars example sets
it, so nothing in the repository settles it:

```bash
grep -n enable_dev_schedule "environments/${TF_ENV}.tfvars"
# Expected: no match, or "= false" - either means the scheduler is absent and steps 2 and
# 13 are correctly skipped. "= true" means they must be RUN.
```

#### The day before the window — rehearse every read-only check

Run every read-only check against production the day before, and spend the window on the three things that
change something: the snapshot, the parameter-group copy and the apply. **A rehearsal proves the command,
not the state** — instance status, service counts and alarm states all move, so a rehearsed check is still
run inside the window. The first two window items are additive and could be done early, but a copy taken a
week before is stale if anyone touched the live group since; re-verify at apply time.

| Rehearse | What the rehearsal settles |
|-------------------------|-----------------------|
| Phase 1 steps 1, 3 and 6 | Every resource name resolves, and the queries return the shape the assertions expect |
| The ECS and alarm checks below | The four service names and the three alarm names are right |
| The proxy reach check below | `ssm_sh` reaches production, and the in-VPC path exists there at all |
| **The health lane** | A complete `e2e/.env.prod`, the case count, and a green baseline on `<FROM>` — so a failure afterwards cannot be mistaken for one the upgrade caused |

```bash
# The application's own path: the proxy endpoint, from inside the VPC. On development
# this was inferred from the ECS services coming up; here it is checked directly.
PROXY=$(aws rds describe-db-proxies --db-proxy-name "${ENV}-optinist-rds-proxy"   --query 'DBProxies[0].Endpoint' --output text)
[ -n "$PROXY" ] && [ "$PROXY" != None ] || { echo "ABORT: proxy endpoint not resolved" >&2; }
need AWS_REGION ENV PROXY
# Then 0A step 8's SQL block with "$PROXY" in place of CLONE_HOST.
# Expected before the window: <FROM>.x through the proxy. After the apply: <TO>.x. A
# failure here while the instance is available is a proxy problem - and on production the
# proxy target is not re-registered automatically.
```

**The health lane reads its target from `frontend/e2e/.env.prod`** (keys in `.env.prod.example`), and
`playwright.config.ts` hard-fails at startup if a required key is missing. The file's values override the
shell, so hand-exported variables are silently ignored.

```bash
( cd frontend && ls -l e2e/.env.prod && \
  for k in BASE_URL API_URL HEALTH_ENV RDS_PROXY_HOST RDS_SECRET_ID \
           RDS_SSM_INSTANCE_NAME STRIPE_SECRET_ENV TEST_USER_EMAIL TEST_USER_PASSWORD; do
    grep -qE "^${k}=." e2e/.env.prod && echo "ok   $k" || echo "MISSING $k"
  done )
# Expected: ok on every line. RDS_SSM_INSTANCE_NAME is a tag:Name value, so it reads
# ${ENV}-optinist-background; HEALTH_ENV is the var.environment value, i.e. the same as $ENV.
```

```bash
# Run from frontend/ in a SUBSHELL so the working directory is not left changed - the
# blocks after this one are relative to infrastructure/terraform.
( cd frontend && E2E_TARGET=prod yarn test:e2e e2e/17-aws-health.spec.ts --retries 0 \
    --grep-invert "@slow|@disruptive|HEALTH-25|HEALTH-26" )
```

Rules for that invocation, each the fix for a mistake an earlier run made:

- **`HEALTH_ENV` is the variable, not `TARGET_ENV`.** `TARGET_ENV` is a TypeScript constant in `helpers.ts`
  and setting it is inert: the lane then grades **development's** compute layer while pointing SQL at
  production, and a green run checks nothing of production.
- **Off development the lane requires `RDS_PROXY_HOST`, `RDS_SSM_INSTANCE_NAME`, `RDS_SECRET_ID` and
  `STRIPE_SECRET_ENV`** as `expect()`s — a missing one fails all 29 cases — and `BASE_URL`, which unset
  defaults to localhost and **skips** all 29.
- **`--retries 0`**: the default retry doubles the AWS reads and HTTP load on production. **Not `--headed`**:
  nothing here drives a browser, and it breaks on a headless host.
- **`--grep-invert` replaces the config's own `grepInvert`**, so `@slow|@disruptive` must be repeated inside
  it. It deselects the two cases that are not read-only, and **whatever is deselected before the apply must
  be deselected after it** or the comparison does not hold:

  | Deselected | Why | What covers it instead |
  |-------------------------|-----------------------|-----------------------|
  | `HEALTH-25` | POSTs a registration to production (an existing address, refused with 400 — but an application bug that accepted it would write first) | The ALB route it uniquely covers cannot change in an engine upgrade; the account's row state is covered by the free-account and instance-assignment cases |
  | `HEALTH-26` | 20 concurrent requests at the public tier — the lane's only timing-dependent assertion, so the likeliest to fail for a reason that is not the upgrade | The serial public-tier case and the proxy-path query; and the release's manual service cycle restarts all four tiers against the new engine |

- **`E2E_FAIL_ON_SKIP=1` is deliberately absent.** On production `HEALTH-27` skips whenever the ALB alarm has
  no retained ALARM transition, and `HEALTH-29` skips with no unpublished experiment. **Read the reporter's
  skip list and account for each entry** instead of trusting the exit code.

**Expect 27 collected cases** with the two deselected (`HEALTH-19` skips on development's plain-HTTP ALB and
executes here). Read the count off the skip-summary reporter's `N executed` line, not Playwright's total.

**Measured on production on 8.0, as the before half of the pair: `26 executed, 1 skipped, 27 mapped`**, in
about two minutes — budget that in the window. The one skip was `HEALTH-27` (production's alarms had not
moved for roughly three months); `HEALTH-15` passed but printed `error rate not decided`, so a failure there
afterwards is a real signal while a pass proves little; `globalSetup` reported `is not a disposable
environment; skipping cleanup`. **Anything other than that shape afterwards is a change** — a `HEALTH-29`
skip included, since an unpublished experiment existed here. The lane writes nothing, but `HEALTH-25`,
`HEALTH-26` and the login are real traffic. **Record the results**: the baseline only works if it is on file.

**Then phase 1 steps 4 and 5 against production**, from the reference, not from a copy — a restated command
is a command that drifts. Step 4's snapshot name is a requirement the Rollback searches for; step 5's two
verifications are the half most easily dropped.

**Then the backend switch, which is production-only and has no phase 1 counterpart:**

```bash
# Forgetting this applies this environment's plan to the other environment's state, and
# a deployment clone used for routine work has been found pointed at the OTHER
# environment's state bucket. From the repository root, because every terraform command
# below depends on the working directory.
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
# Expected: the bucket printed above is the bucket in the .hcl file. Otherwise the init
# did not take and nothing below this line is safe.

# Proof 2 - an attribute that names the environment.
terraform state show aws_db_instance.main | grep -E '^[[:space:]]+(identifier|engine_version)'
# Expected: identifier is $DB, and engine_version is <FROM>.
# `terraform state list | grep aws_db_instance` is NOT a proof: it prints resource
# ADDRESSES, identical in both environments, and the pipe discards terraform's exit
# status, so an unconfigured backend prints nothing rather than failing.
```

Then **phase 1 steps 3 to 12** in order, and after the apply **phase 1 step 14** — the proxy target matters
more here, because production gets no automatic re-registration — plus:

```bash
# Every service rolled cleanly. All four - the public tier is a separate service and
# easy to forget. `found` and `failures` are in the query because a name that does not
# resolve goes to `failures`, which a `services[]` query discards.
aws ecs describe-services --cluster "${ENV}-optinist-cloud-cluster" \
  --services "${ENV}-optinist-cloud-service" "${ENV}-premium-optinist-cloud-service" \
             "${ENV}-background-optinist-cloud-service" "${ENV}-public-optinist-cloud-service" \
  --query '{found:length(services),failures:failures,
            services:services[].{name:serviceName,status:status,desired:desiredCount,
            running:runningCount,rollout:deployments[0].rolloutState}}'
# Expected: found == 4, failures == [], and for each row ACTIVE, COMPLETED,
# running == desired. Desired is NOT uniform: measured on production, free, premium and
# background run 1 each and public runs 2.

# `found` again: describe-alarms answers an unknown name with an empty list and exit 0.
aws cloudwatch describe-alarms \
  --alarm-names "${ENV}-optinist-rds-cpu-high" "${ENV}-optinist-rds-connections-high" \
                "${ENV}-optinist-rds-storage-low" \
  --query '{found:length(MetricAlarms),
            alarms:MetricAlarms[].{name:AlarmName,state:StateValue,
            updated:StateUpdatedTimestamp}}'
# Expected: found == 3, OK for all three. INSUFFICIENT_DATA immediately after the
# upgrade resolves within a couple of evaluation periods. Read `updated` too: a fresh
# timestamp on alarms that had not moved for months is a real transition.
```

A service stuck short of its desired count, or an application connection failing while the instance is healthy, is a connection pool holding a dead connection: force a new deployment on that service. A rollout problem, not a rollback trigger.

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

# Extended Support charge gone, at daily granularity. GROUPED by usage type, not filtered
# to one: a filter on a single usage type that matches nothing returns zero for every
# day, which reads exactly like the charge having stopped. Grouping lists what RDS
# actually billed, so the Extended Support line is OBSERVED absent, not asserted.
aws ce get-cost-and-usage --granularity DAILY \
  --time-period "Start=$(date -u -v-7d +%Y-%m-%d),End=$(date -u +%Y-%m-%d)" \
  --metrics UnblendedCost \
  --filter '{"Dimensions":{"Key":"SERVICE","Values":["Amazon Relational Database Service"]}}' \
  --group-by Type=DIMENSION,Key=USAGE_TYPE \
  --query 'ResultsByTime[].{day:TimePeriod.Start,
           types:Groups[].[Keys[0],Metrics.UnblendedCost.Amount]}'
# Expected, in this order:
#   1. The list is NOT EMPTY on any day - empty means the query is broken, not that RDS
#      cost nothing.
#   2. A usage type containing `ExtendedSupport` appears on the days BEFORE the apply and
#      is absent after it. The apply's own day carries a PARTIAL charge, and Cost
#      Explorer's daily data lags, so the confirming read is two days after the apply.

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

**Deleting the pre-upgrade snapshots and the retained rollback parameter groups closes the rollback window.**
Do it against the retention duration agreed before phase 1, not on the day the upgrade looks finished — see
[How long the rollback window stays open](#how-long-the-rollback-window-stays-open). Both are cheap to keep.

#### The cleanup PR

Three changes, which travel together: splitting them buys no extra proof, since the per-resource assertion
below gives the same evidence within one PR.

| Change | File | Plan effect |
|-------------------------|-----------------------|-----------------------|
| Remove `allow_major_version_upgrade` and `apply_immediately` | `infrastructure/terraform/infrastructure.tf` | **An in-place `update` on `aws_db_instance.main`, carrying exactly those two attributes.** They are write-only against the API but recorded in state |
| Update the MySQL client package, if it has fallen behind | `infrastructure/scripts/app_setup.sh` | Updates `aws_s3_object.app_setup_script`, whose `etag = filemd5(...)` tracks the script |
| Add the snapshot-restore runbook | `infrastructure/documentation/` | None |

The custom AMI installs its client from a different package and needs nothing.

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
#   null_resource.build_and_deploy       -> delete,create   on DEVELOPMENT (source_revision)
#                                        -> absent          on PRODUCTION (B6). Its
#                                           APPEARING there means var.git_branch was
#                                           edited and this apply is now a rollout.
#   aws_s3_object.app_setup_script       -> update          (only if app_setup.sh changed)
#   aws_db_instance.main                 -> update, with exactly two changed attributes:
#                                             allow_major_version_upgrade: true -> null
#                                             apply_immediately:           true -> false
#                                           Judge by the SET, as phase 1 step 8 does; a
#                                           third attribute stops the work.
#
# The guard is restored by the CONFIGURATION, not by this apply: a later engine_version
# edit is refused whether or not this diff has been applied, so any later apply absorbs
# it and none needs scheduling for it alone.
```

---

## Rollback

There is no engine downgrade: returning to the previous version means restoring a snapshot taken before it.
That works because **the engine version is a property of the snapshot** — `RestoreDBInstanceFromDBSnapshot`
has no engine version parameter, the same API fact that causes B3.

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
reproduces the endpoint hostname but **`DbiResourceId` is new**, which is what forces step 3; and each delete
that retains its backups leaves its own set under the same identifier, so a rollback plus its eventual cleanup
means two. **Metadata is not proof**: connect over TLS and read the version from the engine.

### The rollback procedure

**This is the live procedure, and it is self-contained**; phase 0A rehearses it on a clone. Five steps. Do
not skip step 0 - a rollback that starts by deleting the evidence cannot be diagnosed.

```bash
# Run the session setup from the top of Procedure, with ENV set to the environment in
# trouble. It sets FROM and TO, which the checks below compare against.
DB=${ENV}-optinist-cloud-rds
ROLLBACK_PG=${ENV}-optinist-ssl-rollback
```

**Step 0. Choose the restore source, and confirm it.** The manual pre-upgrade snapshot from phase 1 or
phase 4 is the primary one; RDS's automatic pre-upgrade snapshots are the fallback.

```bash
# EVERY manual snapshot, newest first, with the filter as a COLUMN rather than a WHERE
# clause: an empty filtered result and an empty snapshot list mean different things.
aws rds describe-db-snapshots --db-instance-identifier "$DB" --snapshot-type manual \
  --query 'reverse(sort_by(DBSnapshots,&SnapshotCreateTime))[].{
             id:DBSnapshotIdentifier,ev:EngineVersion,status:Status,
             created:SnapshotCreateTime,
             isTarget:contains(DBSnapshotIdentifier,`pre-upgrade`)}' --output table
# Expected: a row with isTarget true - this phase's snapshot, available, on <FROM>. Rows
# with isTarget false are older manual snapshots kept for unrelated reasons: not restore
# sources, and not to be deleted in phase 5. No isTarget-true row but other rows means
# the snapshot was named something else - find it by date. No rows at all means the
# window really was closed.

aws rds describe-db-snapshots --db-instance-identifier "$DB" --snapshot-type automated \
  --query "reverse(sort_by(DBSnapshots[?starts_with(EngineVersion,'${FROM}')],
           &SnapshotCreateTime))[:3].{id:DBSnapshotIdentifier,ev:EngineVersion,
           created:SnapshotCreateTime}" --output table
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

**Step 1. Stop application traffic, and preserve the evidence.** Scaling to zero does not hold on its own:
the manager Lambdas re-scale on their own schedules, so disable their EventBridge rules first, as the
development scheduler's own stop path does.

> **`for R in $(echo "$RULES")`, never `for R in $RULES`.** zsh does not word-split an unquoted parameter
> expansion, so the bare form iterates once over the whole string — loudly for the rule loops, silently for
> the service loop in step 5, which would set the *first* service to the *last* service's count. zsh does
> split command substitution, which is what the wrapper relies on.

```bash
# Record the current state as "name=count" pairs that step 5 can replay. The four counts
# are NOT all 1.
COUNTS=$(aws ecs describe-services --cluster "${ENV}-optinist-cloud-cluster" \
  --services "${ENV}-optinist-cloud-service" "${ENV}-premium-optinist-cloud-service" \
             "${ENV}-background-optinist-cloud-service" "${ENV}-public-optinist-cloud-service" \
  --query 'services[].[serviceName,desiredCount]' --output text | awk '{print $1"="$2}' | tr '\n' ' ')
echo "COUNTS=$COUNTS"
# Expected: four name=count pairs. WRITE THIS LINE DOWN alongside RULES - a rollback can
# outlive the shell it started in.

# DERIVE the rule list from the scheduler Lambda's SCHEDULE_RULE_NAMES and
# DELAYED_RULE_NAMES rather than transcribing it: a hand-copied list drifts, and the
# rule most easily missed, free-manager-asg-events, is triggered BY scaling ECS to zero.
RULES=$(aws lambda get-function-configuration --function-name "${ENV}-dev-scheduler" \
  --query 'Environment.Variables.[SCHEDULE_RULE_NAMES,DELAYED_RULE_NAMES]' --output text \
  | tr '\t' '\n' | python3 -c 'import json,sys;print(" ".join(n for l in sys.stdin for n in json.loads(l)))')

# On an environment with no scheduler - production - there is no Lambda to read, so fall
# back to the rules that exist. This lists them rather than assuming a spelling.
[ -n "$RULES" ] || RULES=$(aws events list-rules --name-prefix "${ENV}-" \
  --query "Rules[?contains(Name,'manager')||contains(Name,'cleanup')||contains(Name,'tracker')].Name" \
  --output text | tr '\t' ' ')

echo "RULES=$RULES"
# Expected: five names on development - free-manager-schedule, free-manager-asg-events,
# cost-tracker-schedule, premium-manager-schedule, premium-cleanup-schedule.
# WRITE THIS LINE DOWN: step 5 re-enables exactly this set.

for R in $(echo "$RULES"); do
  aws events disable-rule --name "$R" && echo "disabled $R"
done

# Verify EVERY one: a rule missed here is a service that scales back up mid-restore.
for R in $(echo "$RULES"); do
  aws events describe-rule --name "$R" --query '[Name,State]' --output text
done
# Expected: DISABLED for every line.

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

**Step 2. Free the identifier, then restore under it.** Restoring onto the original identifier keeps
Terraform convergent and leaves the ARN and endpoint unchanged. The alternative — restore to a temporary
identifier, verify, then rename — keeps the broken instance for diagnosis at the cost of a rename and reboot.

> **Deletion protection blocks the delete, and production has it on.** Turning it off is a `modify`
> that takes seconds — but as a deliberate step, not something discovered from an error during an incident.
> Step 4's apply puts it back, since `deletion_protection` is declared; if the rollback is abandoned before
> step 4, re-enable it by hand.

```bash
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].DeletionProtection'
# Expected: true on production, false on development.

# Only when the line above said true.
aws rds modify-db-instance --db-instance-identifier "$DB" \
  --no-deletion-protection --apply-immediately \
  --query 'DBInstance.{id:DBInstanceIdentifier,pending:PendingModifiedValues}'
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].DeletionProtection'
# Expected: false. Confirm it: the delete fails on this and nothing else explains why.
```

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
# infrastructure.tf - after phase 5's cleanup PR it is not. Check before using it.
NEWEST_TF_COMMIT=$(git log -1 --format=%H -- infrastructure/terraform/infrastructure.tf)
git show --stat "$NEWEST_TF_COMMIT"
# Expected: only the engine version and parameter group family lines. If it carries
# application changes, or if application commits have landed since, do NOT revert -
# reverting them too would ship an unrelated rollback through the same apply (B6).
# Edit the two lines back by hand on a branch off current HEAD instead.
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

**Step 5. Bring traffic back, and close the loop on the scheduler.**

```bash
# If the shell from step 1 is gone, redeclare both from the lines written down there.
# An empty value here makes the loops below run zero times and print nothing, which is
# indistinguishable from success - so refuse rather than proceed.
# COUNTS='svc=1 svc=2 ...'   RULES='rule rule ...'
[ -n "$COUNTS" ] && [ -n "$RULES" ] || {
  echo "ABORT: COUNTS and RULES must be set - redeclare them from step 1's output" >&2; }

# Restore each service to ITS OWN recorded count, not blindly 1
for P in $(echo "$COUNTS"); do
  aws ecs update-service --cluster "${ENV}-optinist-cloud-cluster" \
    --service "${P%%=*}" --desired-count "${P##*=}" --force-new-deployment >/dev/null \
    && echo "restored ${P%%=*} to ${P##*=}"
done

# Re-enable every rule disabled in step 1. Leaving one disabled silently stops
# autoscaling, premium assignment cleanup or cost metrics, with no alarm for it.
for R in $(echo "$RULES"); do
  aws events enable-rule --name "$R" && echo "enabled $R"
done
for R in $(echo "$RULES"); do
  aws events describe-rule --name "$R" --query '[Name,State]' --output text
done
# Expected: ENABLED for every one, and the count must match what step 1 disabled -
# five on development.

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

The mechanism does not degrade with time: a manual snapshot never expires. What closes the window is
housekeeping — phase 5's deletion of the two artefacts below — so **agree a retention duration before phase 1
and do not run those deletions until it has elapsed.** Both are close to free to keep.

| Artefact | Needed for | Removed by |
|-------------------------|-----------------------|-----------------------|
| `<ENV>-optinist-pre-upgrade-<date>` | The restore source | Phase 5 inventory |
| `<ENV>-optinist-ssl-rollback` | The group the restored instance attaches | Phase 5 inventory |

Four things do change with elapsed time, none blocking:

1. **The data loss grows**, and it starts inside the apply window: nothing in the forward path stops traffic,
   so apart from the minutes the engine is offline the service is up and writable, and **the maintenance
   announcement is the mitigation, not a courtesy** — say in it that *a rollback loses data created during
   the window*. Keep the gap between the manual snapshot and the verification short. On development a
   late rollback is a week of everyone's test data: announce it.
2. **`git revert` stops being the clean path** once application commits have landed; revert the two lines
   by hand on a branch off current HEAD, as step 4 says.
3. **Terraform state has moved on**, so gate the rollback plan as the forward apply was gated.
4. **Engine versions are eventually deprecated by AWS**, and a snapshot of a version RDS no longer offers
   may be forced to a newer version or refused. A months-and-years concern, but the reason a retained
   snapshot is not a permanent guarantee.

On development a late rollback must also put the nightly cycle back: step 4 reverts the Lambda's parameter
group, and the next evening's stop regenerates the scheduler snapshot at the old version.

### Cost of a rollback

- **Time:** the restore duration measured in phase 0A, plus proxy re-registration and the Terraform revert.
- **Data:** everything written after the snapshot — near zero if caught during the window, every write since if it surfaces days into the soak. Acceptable on development, the real constraint on production.

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

### 3. The clone claims to be the live instance

**Problem:** RDS copies the source instance's tags through the snapshot onto the clone, so it arrives with
the live instance's `Name` and `ManagedBy = terraform` while in no state file — misleading a cost report
grouped by `Name` and a drift audit.

**Solution:** retag it as soon as the restore is available. Nothing keys off these tags functionally.

```bash
aws rds add-tags-to-resource --resource-name "$(aws rds describe-db-instances \
  --db-instance-identifier "$CLONE" --query 'DBInstances[0].DBInstanceArn' --output text)" \
  --tags Key=Name,Value="$CLONE" Key=ManagedBy,Value=manual Key=Purpose,Value=rehearsal
aws rds list-tags-for-resource --resource-name "$(aws rds describe-db-instances \
  --db-instance-identifier "$CLONE" --query 'DBInstances[0].DBInstanceArn' --output text)" \
  --query 'TagList[].{k:Key,v:Value}' --output table
# Expected: Name is the clone's own identifier, ManagedBy is manual
```

### 4. The parameter group destroy fails after a successful upgrade

**Problem:** Terraform destroys the outgoing parameter group after the instance update completes. If RDS still reports it as in use, the destroy fails with `InvalidDBParameterGroupState`.

**Solution:**
- Re-run the apply. Nothing is lost in that state - the instance is already on the new group.
- If it persists, check for a clone or a manually created instance still referencing the group.

### 5. The snapshot chain loses the backup retention period

**Problem:** `BackupRetentionPeriod` is not a restore parameter; it carries over in practice but is not guaranteed. A zero retention means RDS takes no automatic pre-upgrade snapshot, so a rollback silently loses a recovery point.

**Solution:**
- Assert the retention period in phase 2's check 2, on the restored instance.
- It does not decay gradually - if the chain loses it, it reads zero at the first cycle, which is why one cycle settles the question.

### 6. The database is slower after the upgrade

**Problem:** A major version changes InnoDB memory and IO defaults, and **a family diff does not show most of them.** Two mechanisms produce the same symptom: RDS stops pinning a value and the engine derives it (`innodb_buffer_pool_size`, visible in a family diff), or RDS pins it in neither family and the engine default changed underneath (the larger group — between 8.0 and 8.4 `innodb_io_capacity`, `innodb_io_capacity_max`, `innodb_adaptive_hash_index`, `innodb_change_buffering` and `innodb_buffer_pool_instances`), which a family diff cannot show. **A value that looks derived from another may not be**: `innodb_buffer_pool_instances` did not come back when the pool was pinned back, because its own default had changed. Re-read every moved value after the pins are applied (0A step 11).

**Solution:**
- The symptom is slower, not wrong: `ReadIOPS` or `WriteIOPS` rising, latency up. Not an error.
- **Attribute by the step 8 / step 9 pair** — the same running server, before and after — not by diffing family defaults.
- `innodb_io_capacity` deserves a decision: a large increase tells the page cleaner it has IO headroom the volume may not have. Pin it and `innodb_io_capacity_max` to the outgoing values, and tune deliberately afterwards as separate work.
- Pin `innodb_buffer_pool_size` if the pair shows the pool shrinking, and `innodb_buffer_pool_instances` alongside it. The engine requires the pool to be a multiple of `innodb_buffer_pool_chunk_size` times the instance count.
- A metric that moved against the phase 3 baseline with **no** corresponding difference in the pair is an application question.

### 7. A failure during the soak is hard to attribute

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

**These are the eight the criterion 6 baseline captures**, so the two lists stay in step.

| Metric | Why | Expected after upgrade |
|-------------------------|-----------------------|-----------------------|
| `BufferCacheHitRatio` | Buffer pool sizing | **Returns no datapoints on standard RDS MySQL** - an Aurora metric. The three rows below carry the question in its place |
| `ReadIOPS` | The substitute for the above: on a cache-served workload it is flat enough that a sizing change stands out | May rise with a smaller buffer pool |
| `FreeableMemory` | The other side of pool sizing | Unchanged |
| `ReadLatency` | What a user notices about a smaller pool | May rise with `ReadIOPS` |
| `WriteIOPS` | Background flushing, if `innodb_io_capacity` rose - edge case 6, mechanism 2 | May rise independently of any application change |
| `WriteLatency` | The write-side pair to the above. `ReadLatency` alone shows only half of edge case 6 | May rise with `WriteIOPS` |
| `CPUUtilization` | Baseline comparison | Unchanged |
| `DatabaseConnections` | Pool health after the rollout | Returns to the pre-upgrade level |

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
| `auto_minor_version_upgrade` | `aws_db_instance.main` | **Not declared** - it takes the provider default of `true`, which is why a major-only version works. Setting it to `false` would break that, so check before assuming it is set |
| `backup_retention_period` | `aws_db_instance.main` | Must be greater than zero for automatic pre-upgrade snapshots |

### Scheduler environment variables

| Variable | Purpose |
|-------------------------|-----------------------|
| `RDS_PARAMETER_GROUP_NAME` | The group a nightly restore attaches. Must follow B1's rename |
| `RDS_SNAPSHOT_ID` | Fixed identifier for the nightly snapshot |
| `RDS_SUBNET_GROUP_NAME` | Passed on every restore |
| `RDS_SECURITY_GROUP_IDS` | Passed on every restore |

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

Two lanes are localhost-only and cannot reach a deployed environment; one subscription lane writes to the shared development database without an opt-in flag; the disruptive lane refuses to run when another account has been active recently, which on a shared environment is usually the correct outcome.

### Choosing the lanes

**The two AWS-facing lanes beyond the health lane take well over an hour together, and criterion 4 reaches
more hand-written SQL than they do** — roughly 120 raw `execute()` statements in the Lambda packages, which
nothing else executes. **So the environment's own release e2e set plus criterion 4 substitutes for them, conditional on
criterion 4 running in full.** Run the health lane regardless, and record the substitution in the phase log.
The residual risk, `GROUP BY` strictness, is closed by measurement: step 8 records `sql_mode`, and a value
without `ONLY_FULL_GROUP_BY` cannot start rejecting a query the outgoing version accepted.

### The engine-version assertion

**Deferred to after phase 4, and tracked in its own issue (see References).** It closes the B2 failure state — instance on the old
version with an upgrade queued — which nothing in the health lane checked. Every assertion in it is also a
manual step in phase 4, so it is regression cover for the next change, not verification of this one. One
rule from it: an assertion against a hard-coded engine version ships in the same commit as the
`engine_version` edit, so it cannot fail during the upgrade it claims to guard.

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

First applied for MySQL 8.0 to 8.4 under issue #877, whose child issues carry the per-phase execution records, the combined release order used in phase 4, and the deferred engine-version assertion (#902).
