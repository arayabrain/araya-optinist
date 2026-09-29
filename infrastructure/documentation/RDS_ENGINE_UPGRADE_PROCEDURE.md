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
   - **On an environment that is rebuilt on a schedule, the automated retention is not one continuous window.** The retention period reads the same as production's, but each cycle's instance carries its own backup, so what exists is one window per cycle rather than an unbroken span. Point-in-time recovery is available *within* those windows and nowhere between them. A manual snapshot is what covers the gaps.
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

**Fix:** use `name_prefix` in place of `name`. This is durable across future family changes. A one-off rename embedding the target version also works, at the cost of another rename next time.

The resource's `Name` tag is deliberately left as it was - it is a tag rather than an identifier, nothing reads it, and changing it would add a diff to the plan that the gate would then have to account for.

Consumers follow automatically because every reference derives from `aws_db_parameter_group.main.name`. **How many consumers there are depends on the environment:**

| Consumer | Where | Present when |
|-------------------------|-----------------------|-----------------------|
| The instance's `parameter_group_name` | `infrastructure.tf` | **Always** |
| The scheduler Lambda's `RDS_PARAMETER_GROUP_NAME` | `dev_schedule.tf` | Only with `enable_dev_schedule` |
| The parameter group ARN in the scheduler's IAM policy | `dev_schedule.tf` | Only with `enable_dev_schedule` |

**So the three-consumer blast radius is development's.** Both of the non-instance consumers sit in
`dev_schedule.tf` under `count = var.enable_dev_schedule ? 1 : 0`, so on an environment without the scheduler
the instance is the **sole** consumer. Confirm which case applies before sizing the change - phase 4 has a
one-line check for it.

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

### Before pasting anything: one line, first

**This has to come before the helper definitions below, not with the rest of the setup.** `zsh` does not treat
`#` as a comment in interactive input unless `INTERACTIVE_COMMENTS` is set, and it is off by default — and a
comment in the wrong place does not merely print noise, it can abandon a function definition outright (see
*Conventions* below). The helpers are the first thing an operator pastes, so the `setopt` has to precede them.

```bash
setopt interactive_comments 2>/dev/null || true
```

The rest of the session setup — profile, region, `ENV`, `TF_ENV`, `FROM`, `TO`, `DB` — is under
[Procedure](#procedure), because unlike this line it is needed only once the commands start.

### Shared helper: run a command inside the VPC

Neither RDS instance is publicly accessible and the proxy is TLS-only, so the in-database checks run from an in-VPC instance over SSM. This is the same mechanism the e2e suite uses in `runShellOverSsm`.

**Two things about the function body, both learned the hard way:**

- **The failure branch prints stdout as well as stderr.** A block that dies partway still produced everything
  up to that point, and that output is the diagnosis.
- **The body carries no comments, deliberately.** A comment inside its `case` is not the harmless noise a
  comment elsewhere is: with zsh's `INTERACTIVE_COMMENTS` off, `#` is read as the start of a **case pattern**,
  which is a **hard parse error**. The function then does not exist, and the only symptom is
  `command not found: ssm_sh` several steps later. Verified by pasting it into `zsh -f -i` both ways. Keep
  explanation in prose here, not in the block.

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

# Both destructive helpers show the target's real attributes and require a typed
# confirmation. The name predicate says "this could be a clone"; the preview is what
# tells you it is the clone you meant.
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

# Deletes a clone, but keeps a final snapshot and the automated backups anyway. They
# are cheap on a throwaway, and the habit is what makes a mistyped identifier
# survivable rather than terminal.
drop_clone() {
  assert_rehearsal "$1" || return 1
  confirm_target instance "$1" || return 1
  aws rds delete-db-instance --db-instance-identifier "$1" \
    --final-db-snapshot-identifier "$1-final" --no-delete-automated-backups
  aws rds wait db-instance-deleted --db-instance-identifier "$1"
}

# Snapshots need the same guard, and need it more: there is no final-snapshot fallback
# for a snapshot, so an accepted-but-wrong delete here is unrecoverable. Phase 0B's
# source snapshot is taken from the production instance, and the newest manual snapshot
# is exactly what an accidental instance delete falls back to.
drop_snapshot() {
  assert_rehearsal "$1" || return 1
  confirm_target snapshot "$1" || return 1
  aws rds delete-db-snapshot --db-snapshot-identifier "$1"
}
```

**What the guards do and do not protect.** Worth knowing precisely, because the difference decides how
much attention each step still needs.

| | |
|---|---|
| Refuses any identifier without `rehearsal` in it | So both live instances, and every pre-upgrade snapshot, are outside the accept set. Confirm this before relying on it: `describe-db-instances` and `describe-db-snapshots` filtered on `contains(…, 'rehearsal')` should both return nothing at the start of the work |
| Shows the target's real attributes before acting | Engine version, creation time and parameter group for an instance; type and source for a snapshot. An identifier that is accepted but wrong usually looks wrong here |
| Requires the identifier typed back | No single keystroke deletes anything |
| `drop_clone` keeps a final snapshot and the automated backups | Even an accepted-and-wrong instance delete is recoverable |
| **`drop_snapshot` has no fallback** | A snapshot cannot be snapshotted. The predicate and the confirmation are the only protection, which is why both are there |
| Neither protects against a name you deliberately gave a real asset | Do not put `rehearsal` in the name of anything you intend to keep |

#### Verifying the guards before use

Verify both guards before relying on them, against names that must never be accepted. **Set `ENV`
first** - see the session setup under [Procedure](#procedure). With `ENV` unset the test still refuses,
but it refuses a name that does not exist, which is a weaker test than the one intended.

Four checks. All four must hold; any one failing is an abort condition.

**Never pass a real identifier to `drop_clone` or `drop_snapshot` as a test.** The point of the test is
that the predicate refuses it - but if the predicate is broken, which is the thing being checked, the
wrapper goes on to describe that real resource and prompt for confirmation, and a live database is then
one keystroke away. So the predicate is tested directly, on real names, and the wrappers are tested only
on names that do not exist.

```bash
echo "${ENV:?set ENV before testing the guards}"
DB=${ENV}-optinist-cloud-rds

# 0. These are shell functions: they do not survive a new terminal, and a copy pasted
#    from an earlier revision is worse than none. Re-paste all four, then read one back.
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

# 3. The wrappers consult the predicate. The only property these strings need is that
#    they lack "rehearsal"; they deliberately do not resemble any real identifier, so
#    that editing one cannot accidentally produce a live name.
drop_clone    "guard-test-instance-must-be-refused"
drop_snapshot "guard-test-snapshot-must-be-refused"
# Expected: REFUSING twice, naming each string. No preview, no prompt, nothing sent to
# AWS. A preview appearing here means the predicate is not wired into the wrapper - abort.

# 4. The accept path works - a guard that refuses everything is not a working guard.
#    This uses the identifier phase 0A will really pass to drop_clone, so it checks the
#    operand that matters rather than an arbitrary string containing "rehearsal".
#    Unlike check 3 the name is not the protection here - it has to be accepted. What
#    protects this call is that the clone does not exist yet.
drop_clone "${ENV}-optinist-rds-upgrade-rehearsal"
# Expected: past assert_rehearsal, then confirm_target fails to describe an instance
# that does not exist yet and returns before prompting. The accept path is proven
# without anything being deleted.
#
# If the clone DOES already exist - re-running these checks in a new shell partway
# through phase 0A - this reaches the prompt instead. Answer with anything but the
# identifier, or interrupt. That is still a pass; it just is not the state check 4
# was written for.
```

The dates in the fabricated names are arbitrary and deliberately impossible - `00000000-0000` is not a
timestamp any real snapshot carries. Do not substitute a real date here: check 2 already covers real
snapshot names, and it covers them without going near a delete.

### Shared helper: sanitise a log before posting it

Execution logs are posted to the tracking issues, which are public. Replace environment-specific values with a filter rather than by hand: manual masking fails under time pressure, and **editing a comment does not unpublish anything** - GitHub keeps prior revisions readable, GH Archive is a permanent public record of public issue events, and notification emails have already gone out.

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

# Expected: the same text with identifiers replaced. Durations, engine versions,
# sql_mode values, precheck findings, metric values and cost figures are left intact -
# acceptance criteria are written against them.
```

**Three rules are anchored on `(^|[^[:alnum:]-])` rather than on `\b`, and that is not a style choice.**
BSD `sed -E` - `/usr/bin/sed` on macOS - has no `\b`. It does not error on one; **it simply never matches, and
exits 0.** With `\b` in place, the environment prefix, the `i-…` instance ids and the `db-…` resource ids -
**the three rows this document's own substitution table declares mandatory** - passed through untouched while
the other five rules fired, so the output looked filtered. Measured, then fixed:

```
with \b :  name subscr-optinist-cloud-rds / ssm i-0abc123456789def0 / res db-ABCDEFGHIJKLMNOP
anchored:  name <ENV>-optinist-cloud-rds  / ssm i-A                 / res db-A
```

The capture group is what preserves the character the anchor consumed, and the alternation with `^` is what
keeps a match at the start of a line. **Verify any change to these rules against a line that exercises all
eight**, including one identifier at the very start of a line.

The filter is a convenience, not a guarantee: it does not know about values it has never seen. Check the result before posting.

**This table is the single source for the substitution policy.** The tracking issues reference it rather
than restating it, so it only has to be corrected in one place.

| In the output | Post as | Why |
|-------------------------|-----------------------|-----------------------|
| The environment prefix | `<ENV>` | Matches PR #620, the worked example in this repository |
| The AWS account id | `<ACCOUNT_ID>` | Enables cross-account enumeration |
| The proxy endpoint's account token | `proxy-<TOKEN>` | A resolvable hostname |
| An **instance** endpoint's account token - the label between the identifier and the region | `<TOKEN>` | Same reason. Most steps never print an endpoint; step 10 does, because proving which database it reached is the point of it |
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

> **`ENV` names resources. `TF_ENV` names files. They are not the same value, and on production they differ.**
>
> `ENV` is the Terraform `var.environment` value, which is what every resource name is built from —
> `${ENV}-optinist-cloud-rds`, `${ENV}-optinist-rds-proxy`, and so on. **The files under `backends/` and
> `environments/` are named after the environment in prose, not after that value:**
>
> | | `ENV` | `TF_ENV` |
> |---|---|---|
> | Development | `development` | `development` |
> | **Production** | **`subscr`** | **`production`** |
>
> **The two coincide on development**, so the mistaken `backends/${ENV}.hcl` works there and hides the
> problem. On production it resolves to `backends/subscr.hcl`, which does not exist — and it fails at the
> **first** terraform command of the window, before a plan exists. `TF_ENV` is used **only** for those two
> paths; everywhere else, `ENV`.

**Run this block first; the rest of the document assumes it.** `<FROM>`, `<TO>` and `<ENV>` also appear in
prose and in expected-output comments, where they are placeholders on purpose - but from here on every
*command* uses `$FROM`, `$TO` and `$ENV`, so a version or environment name never has to be typed twice.

A handful of commands do still carry an angle-bracket placeholder, and each one is a value only the
operator can supply:

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

**Set `INTERACTIVE_COMMENTS` before pasting anything, and keep comments on their own line.** `zsh` does not
treat `#` as a comment in interactive input unless `INTERACTIVE_COMMENTS` is set, and it is off by default.
The setup block above turns it on; without it, every commented block in this document misbehaves in one of
two ways.

A *trailing* comment becomes arguments: `date -u   # t0` fails with `illegal time format`, and
`VAR=value   # note` runs `#` as a command and leaves `VAR` unset. The second is the dangerous one because
it fails quietly.

A comment *on its own line inside a pasted function body* behaves in one of two ways, and **the difference
decides whether the function exists at all:**

| Where the comment sits | What happens |
|-------------------------|-----------------------|
| The **first line of the body**, as in `inv` | Stored as the function's first command, so every call begins with `funcname:1: command not found: #`. **Noise, not breakage** - but noise printed immediately before a verdict line is how a real `FAIL` gets overlooked, which is the whole reason the helpers print one verdict per item |
| Inside a **`case`**, as `ssm_sh` used to | `#` is read as the start of a **case pattern**. That is a **hard parse error**: `parse error near 'esac'`, the definition is abandoned, and stray fragments of the body execute on their own. **The function does not exist**, and the only symptom arrives steps later as `command not found: ssm_sh` |

The second was reproduced by pasting `ssm_sh` into `zsh -f -i` with comments off - it reported
`NOT DEFINED`, having first issued a real `aws ssm get-command-invocation` with an empty command id.
`ssm_sh`'s body now carries no comments for that reason.

**So keeping comments on their own line is necessary but not sufficient**, and the `setopt` is what actually
makes this document safe to paste — which is why it is in the very first block, **ahead of every helper
definition.** All three behaviours were observed while executing this document.

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

**Run it for the environment the next phase operates on, not for both.** Phase 0 operates on
development, so it is development's floor that matters there; production's belongs to phase 4, which
establishes it by taking and verifying its own pre-upgrade snapshot. Reading production's floor earlier
answers no question that phase 0 asks, and it puts a production API call inside a phase that otherwise
makes none.

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
# period. On an instance that is deleted and restored on a schedule, the active row spans
# only the current instance's lifetime - hours - and every earlier cycle appears as its
# own `retained` row covering only its own window. Read the whole list, not just the
# active row: together they say which moments are recoverable and which are not.

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
# SNAP is the scheduler's nightly snapshot.
SNAP=${DB}-dev-scheduler
```

#### The upgrade — steps 1 to 11

Restore a clone, upgrade it, and measure what changed. **Only step 3 carries a deadline**: caveat 3 below
is about a restore still reading the source snapshot when the scheduled stop recreates it, and after step 3
nothing in 0A reads that snapshot again.

##### Step 1 — confirm the source snapshot

```bash
# Confirm the source snapshot is usable and on the outgoing version
aws rds describe-db-snapshots --db-snapshot-identifier "$SNAP" \
  --query 'DBSnapshots[0].{status:Status,ev:EngineVersion,created:SnapshotCreateTime}'
# Expected: available, <FROM>.x. Read the timestamp rather than assuming it is last
# night's: the snapshot is recreated by the scheduled stop, so on a day following days
# the environment did not run it is that much older. On a Monday it is Friday's. The
# clone will carry that data, which changes nothing for the precheck or the timing -
# but record which day it actually is.
```

##### Step 2 — a private parameter group for the clone

```bash
# A private parameter group for the clone, copied from the live one.
#    Do not attach the live group - see edge case 1.
aws rds copy-db-parameter-group \
  --source-db-parameter-group-identifier "${ENV}-optinist-ssl" \
  --target-db-parameter-group-identifier rehearsal-ssl-from \
  --target-db-parameter-group-description "outgoing-family rehearsal copy"

#    The copy's output does not show the user-set parameters, so check them. The clone
#    and the drill's restore both attach this group: a copy that lost them produces an
#    instance that rejects the application's TLS-only connections, and it would also
#    make the "connection succeeded" check further down meaningless.
aws rds describe-db-parameters --db-parameter-group-name rehearsal-ssl-from --source user \
  --query 'Parameters[].{name:ParameterName,value:ParameterValue}' --output table
# Expected: the same user-set parameters as the live group - compare against
# `describe-db-parameters --db-parameter-group-name "${ENV}-optinist-ssl" --source user`
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
# Confirm what the clone actually came up as. Two things matter here and neither is
#    visible in the restore call's own response.
aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].{status:DBInstanceStatus,retention:BackupRetentionPeriod,
           rid:DbiResourceId,pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus}'
# Expected: available, retention greater than zero, and pg rehearsal-ssl-from / in-sync.
#
# retention: zero means RDS takes no automatic pre-upgrade snapshot, so step 12 has
# nothing to work with and the drill cannot run.
#
# pg: while the restore is still creating, this reports the DEFAULT group with
# ParameterApplyStatus "applying" even though the restore passed --db-parameter-group-name.
# That is normal mid-flight, but it has to be re-read once the instance is available. If
# it is still the default group then the clone is NOT carrying require_secure_transport,
# and the "connection succeeded" check further down would pass for the wrong reason -
# attach the group with modify-db-instance before continuing.
#
# Note DbiResourceId: step 14 asserts it changed after the drill's restore.
```

##### Step 5 — resolve the target version and family, and build the target parameter group

```bash
# Resolve the target version and its parameter group family. Both come from the API
#    rather than being spelled out: `ModifyDBInstance` needs a concrete version, and the
#    family is not simply "mysql" plus the version - it is "mysql" plus the *major*
#    version, so composing it by hand is a guess. Resolve it the same way the Terraform
#    provider resolves `engine_version = "<TO>"`: the region default for that major.
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

#    Now the parameter group. There is no existing group on the new family to copy, so
#    this one is built - but the parameters are read from the live group rather than
#    retyped, for the same reason step 3 reads the restore parameters off the live
#    instance. A value that differs from expectation should be carried, not silently
#    replaced by what this document assumed.
aws rds create-db-parameter-group --db-parameter-group-name rehearsal-ssl-to \
  --db-parameter-group-family "$TO_FAMILY" --description "target-family rehearsal"

#    Built as JSON rather than the shorthand ParameterName=x,ParameterValue=y form.
#    A user-set value may be an RDS expression such as {DBInstanceClassMemory*3/4},
#    and the CLI's shorthand parser cannot carry a brace - it fails client-side with
#    "Expected: '=', received: '*'". Shell quoting does not help, because the shell is
#    not what is parsing it. This is not hypothetical here: the phase 1 parameter group
#    may pin exactly such an expression, and then the next upgrade reads it back out.
SRC_JSON=$(aws rds describe-db-parameters --db-parameter-group-name "${ENV}-optinist-ssl" \
  --source user --query 'Parameters[].[ParameterName,ParameterValue,ApplyType]' --output text \
  | jq -Rn '[inputs | split("\t")
      | {ParameterName: .[0], ParameterValue: .[1],
         ApplyMethod: (if .[2] == "static" then "pending-reboot" else "immediate" end)}]')
printf '%s\n' "$SRC_JSON" | python3 -m json.tool
# Expected: one object per user-set parameter in the live group. An empty list means the
# source group name is wrong - stop, because the clone would then upgrade with engine
# defaults and the TLS check further down would pass for the wrong reason.

aws rds modify-db-parameter-group --db-parameter-group-name rehearsal-ssl-to \
  --parameters "$SRC_JSON"

#    Read it back. The modify call reports only the group name, not what it stored.
aws rds describe-db-parameters --db-parameter-group-name rehearsal-ssl-to --source user \
  --query 'Parameters[].{name:ParameterName,value:ParameterValue,applyType:ApplyType}' --output table
# Expected: the same names and values as the live group. If a parameter the live group
# sets is missing here, it does not exist in the new family - which is a finding for the
# upgrade itself, not just for the rehearsal.
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


**Do not take the duration from the wall clock.** The `date -u` pair brackets the whole call including
the waiter's polling lag, and RDS also reports `available` for a while after `--apply-immediately` before
the status transitions - so a waiter can even return before the upgrade starts. Measured on this stack,
the wall clock read more than four times the actual outage.

RDS timestamps the real thing in its event stream. Take the numbers from there:

```bash
aws rds describe-events --source-identifier "$CLONE" --source-type db-instance \
  --duration 120 --query 'Events[].{t:Date,msg:Message}' --output table
```

Three different durations come out of that, and they are for different purposes:

| From | To | What it is | Used for |
|-------------------------|-----------------------|-----------------------|-----------------------|
| `The downtime started` | `DB instance restarted` | **The outage users experience** | The number the production announcement quotes |
| `The downtime started` | `engine major version upgrade complete` | The same, upper bound | A safety margin on the above |
| `The pre-check started` | the last `Finished DB Instance backup` | The whole operation | The window the operator has to hold |

Also worth reading off the same output: the pre-check runs automatically as part of the upgrade and
reports its own start and finish, and RDS takes its automatic pre-upgrade backups either side - those are
the snapshots step 12 goes looking for.

**A waiter timeout is not a failure.** `aws rds wait db-instance-available` gives up after 30 minutes
(60 attempts, 30s apart) and exits non-zero, while the upgrade carries on regardless. A major version
upgrade can exceed that. If the waiter errors, do not re-issue the modify and do not touch the instance -
poll instead:

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
# available with the new version. The measured duration is from the first `date -u` to
# here - that is the figure the production announcement is built on, so take it from the
# status transition rather than from whenever the waiter happened to return.
```

##### Step 7 — the precheck log

This comes from the DB log file API rather than CloudWatch, so the clone needs no log exports.

**The precheck log does not exist until the upgrade has run**, because the upgrade is what generates it.
Before step 6 the instance carries only its error logs and a `mysqlUpgrade` file left over from earlier
history; `PrePatchCompatibility.log` appears afterwards. So this step cannot be done early, and an empty
result here means the upgrade has not finished rather than that the check was clean.

**List before fetching.** Filtering on one guessed name silently produces an empty file, which reads
exactly like "no problems found" - the failure mode worth the extra command.

```bash
# List everything first, and read the list.
aws rds describe-db-log-files --db-instance-identifier "$CLONE" \
  --query 'DescribeDBLogFiles[].{name:LogFileName,size:Size,written:LastWritten}' --output table
# Expected: an upgrade-related file with a LastWritten from the upgrade you just ran.
# Candidates seen on this stack: `mysqlUpgrade`, and `PrePatchCompatibility.log` when the
# engine produces one. If neither is newer than the upgrade, the precheck output has not
# landed yet - wait and list again rather than concluding it was clean.

for f in $(aws rds describe-db-log-files --db-instance-identifier "$CLONE" --output text \
  --query "DescribeDBLogFiles[?contains(LogFileName,'Upgrade')
           || contains(LogFileName,'upgrade')
           || contains(LogFileName,'PrePatch')].LogFileName"); do
  echo "===== $f"
  aws rds download-db-log-file-portion --db-instance-identifier "$CLONE" \
    --log-file-name "$f" --starting-token 0 --output text
done | tee /tmp/prepatch.log
wc -l /tmp/prepatch.log
# Expected: non-zero. An empty file means the filter matched nothing - go back to the
# listing above. This is the failure mode that looks like success.

# The precheck log ends with its own tally. Read that rather than grepping for the word
# "error", which also matches check titles like "Checks for errors in column definitions"
# and the tally line itself - a grep count is not the verdict.
grep -E '^(Errors|Warnings|Database Objects Affected):' /tmp/prepatch.log
awk -F': *' '/^Errors:/{print ($2==0 ? "PASS: Errors=0" : "ABORT: Errors=" $2)}' /tmp/prepatch.log
# Expected: PASS. A non-zero Errors count fails the same way on the real instance and is
# an abort condition for the whole plan.

# Warnings do not block, but every one gets read - they are the list of behaviour that
# changes under you. Each numbered check reports either "No issues found" or its detail.
grep -nE '^[0-9]+\)|^\tNo issues found' /tmp/prepatch.log
# Expected: every check accounted for. Then read the detail under any that is not
# "No issues found", and record what each one means for this stack in the phase's log.
```

##### Step 8 — the in-database state

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

`mysql_native_password` is a **server option, not a system variable** - `SELECT @@mysql_native_password` fails with `ERROR 1193 Unknown system variable`. Its configured value is therefore read from the parameter group instead:

```bash
aws rds describe-db-parameters --db-parameter-group-name rehearsal-ssl-to \
  --query "Parameters[?ParameterName=='mysql_native_password'].[ParameterValue,ApplyType,IsModifiable]" \
  --output text
# Expected: ON  static  False - RDS pins it on, so the option cannot be turned off by
# mistake. An empty result means the parameter does not exist in this family, which is
# a different and more serious finding: see the auth row in the table below.
```

**The client aborts on the first SQL error and `set -e` then discards the whole block**, so one bad statement costs every reading. If a statement fails, fix it and re-run rather than reading around it - all of these are `SELECT`s, so re-running is free. A variable that does not exist on one of the two versions raises `ERROR 1193` and aborts the block: drop that line for that side rather than guessing at a substitute.

**Take the same readings on the live instance before it is upgraded**, through the proxy, so there is a
before-and-after pair for the same variables. Without the "before" half these numbers say what the new
version does, not what changed.

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

Step 8 says what the new version does. Only the pair says what **changed**, and the "before" half exists only while the live instance is still on `<FROM>`: after phase 1 it is gone for good. Run this any time before phase 1. Every statement is a `SELECT`, and it reads the instance endpoint rather than the proxy so the access path matches step 8's.

```bash
LIVE_HOST=$(aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].Endpoint.Address' --output text)

ssm_sh <<SH
set -e
CFG=\$(aws secretsmanager get-secret-value --region ${AWS_REGION} \
  --secret-id ${ENV}-optinist/database/config --query SecretString --output text)
export MYSQL_PWD=\$(printf '%s' "\$CFG" | python3 -c 'import json,sys;print(json.load(sys.stdin)["password"])')
DBU=\$(printf '%s' "\$CFG" | python3 -c 'import json,sys;print(json.load(sys.stdin)["username"])')
DBN=\$(printf '%s' "\$CFG" | python3 -c 'import json,sys;print(json.load(sys.stdin)["database"])')
mariadb --ssl -h $LIVE_HOST -u "\$DBU" --connect-timeout=10 "\$DBN" -t <<'SQL'
SELECT VERSION();
SELECT @@sql_mode;
SELECT @@innodb_buffer_pool_size, @@innodb_dedicated_server, @@binlog_format, @@log_output;
SELECT @@innodb_io_capacity, @@innodb_io_capacity_max, @@innodb_adaptive_hash_index,
       @@innodb_change_buffering, @@innodb_buffer_pool_instances;
SELECT @@innodb_redo_log_capacity, @@innodb_flush_method, @@innodb_log_writer_threads,
       @@innodb_buffer_pool_chunk_size;
SQL
SH
# Expected: VERSION() is <FROM>.x. If it is <TO>.x you are pointed at the clone.
```

The plugin and `mysql.user` queries are deliberately left out. They answer "does authentication still work on `<TO>`", which the `<FROM>` side cannot inform.

Then read the **configured** side for both families, because `innodb_buffer_pool_size` is the one value RDS may pin rather than let the engine derive:

```bash
for G in "${ENV}-optinist-ssl" rehearsal-ssl-to; do
  echo "== $G"
  aws rds describe-db-parameters --db-parameter-group-name "$G" \
    --query "Parameters[?ParameterName=='innodb_buffer_pool_size' ||
             ParameterName=='innodb_dedicated_server' ||
             ParameterName=='innodb_io_capacity'].[ParameterName,ParameterValue,Source]" \
    --output text
done
# A formula such as {DBInstanceClassMemory*3/4} on one side and nothing on the other is
# the whole difference. An empty value on both sides means the engine derives it, and
# the measured numbers above are the only evidence that exists.
```

Record the two sets side by side. **A difference in `innodb_buffer_pool_size` is the one that changes a decision** - see edge case 6.

##### Step 10 — alembic

**What this step is for.** Every check so far went through the `mariadb` client, which is a different client library from the one the application uses. This is the only step that exercises the application's own stack - pydantic settings, SQLAlchemy, pymysql, alembic - against the new engine, and it runs the same `alembic upgrade head` the deploy runs. On a clone of a live database the migration is a **no-op**, because the clone is already at head; what is being proved is that the runner works, not that a migration applies.

**What a `docker exec` into this container does and does not inherit.** `DatabaseConfig` in `studio/app/common/db/config.py` reads `MYSQL_SERVER`, `MYSQL_USER`, `MYSQL_PASSWORD` and `MYSQL_DATABASE`. **None of those are container environment variables.** The task definition supplies `DB_HOST`, `DB_USER`, `DB_PASSWORD` and `DB_NAME`, and `cloud-startup.sh` - the entrypoint - re-exports them under the `MYSQL_*` names. Those exports exist only in the entrypoint's own process tree.

A `docker exec` starts from the container's *configured* environment, so it sees `DB_*` and not `MYSQL_*`, and `studio/config/.env` is not in the image either. Left alone, `DatabaseConfig` therefore resolves `MYSQL_USER` and `MYSQL_DATABASE` to `None`, pymysql falls back to `root` with no password, and the connection fails with `1045 Access denied for user 'root'` - which looks like an authentication problem with the new engine and is nothing of the kind.

**So the mapping has to be repeated inside the exec**, from the container's own `DB_*` values. Reading them there rather than passing them in means no credential is written into the SSM command. Overriding `DB_HOST` does nothing, because the mapping that consumes it has already run; `MYSQL_SERVER` is the value to override.

**Why the target is proved first, and why the proof has to abort rather than print.** The deployed `.env` points `MYSQL_SERVER` at the **RDS Proxy**, which fronts the **live** instance. If the override failed to take effect, `alembic upgrade head` would run against live development.

The dangerous case is not a failed connection - `set -e` catches that. It is a **successful connection to the wrong database**, which `set -e` cannot see. Both halves run inside one SSM invocation, so a version printed for a human to read would be read *after* the migration had already run. The version check therefore exits non-zero itself, which is what makes running both halves in one block safe.

```bash
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
# Failure modes, and what each one means:
#   "ABORT: DB_USER / DB_NAME are absent" - the container does not carry them under
#     those names. Read its environment before guessing: docker exec "$C" env | grep
#     -E '^(DB_|MYSQL_)' - and fix the mapping above rather than the credentials.
#   "1045 Access denied for user 'root'" - the mapping above did not happen, so
#     DatabaseConfig resolved the user to None and pymysql fell back to root. Not an
#     engine problem, however much it looks like one.
#   "ABORT: expected ... got <FROM>.x" - the override did not take effect and this
#     reached the live instance through the proxy. Nothing was migrated. Re-read
#     CLONE_HOST before retrying.
#   An empty MYSQL_SERVER, then a connection error - CLONE_HOST was unset. An empty
#     environment variable still wins over any file, so the client fell back to
#     localhost inside the container. Nothing was migrated.
#   No output at all - the container name did not match, and the assignment to C
#     aborted the block under set -e.
#   current BEHIND heads - the migration then really did apply, which on a throwaway
#     clone is harmless and in fact more informative than the no-op. Investigate why
#     a clone of a live database was not at head before trusting the rest of 0A.
```

The `VERSION()` line also proves the application's driver authenticates and negotiates TLS against `<TO>`, which the `mariadb` client in step 8 could not establish on the application's behalf.

**Why an application-layer check belongs in phase 0.** It is a fair objection that a failure here is fixed in the application, not in AWS. The answer is that phase 0 exists to find what should abort the plan, and this is one of those things regardless of which layer ends up owning the fix. Note also what the later phases actually give:

- `cloud-startup.sh` runs `alembic upgrade head` on every container start, and exits non-zero if it fails - so ECS marks the deployment failed and reverts to the previous task definition. The phase 1 apply therefore does exercise this path by itself.
- **But that revert does not undo the engine upgrade.** The previous image carries the same driver versions, so it fails against `<TO>` identically. ECS keeps reverting, the environment stays down, and recovery is either an RDS snapshot rollback - the drill in steps 12 to 14 - or an application fix made under time pressure. In phase 4 the same event is a production outage.

So the later phases detect it; they do not make it cheap. Here it costs one command against a throwaway clone, and the finding arrives while the decision is still "do not apply yet" rather than "restore or hotfix". If it does fail, the fix is an application change and belongs in its own blocking issue - which is exactly the thing worth knowing before the apply rather than during it.

Be clear about what this does **not** prove: the clone is already at head, so no migration is applied and no DDL runs against `<TO>`. What is proved is that the driver stack connects, authenticates, negotiates TLS and that the migration runner executes.

##### Step 11 — rehearse the parameter pins the apply will carry

Steps 8 and 9 measure what the new version does by default. Where that differs from `<FROM>` and the difference is not wanted, the phase 1 parameter group pins the value back - see edge case 6. **This step proves the pins produce the intended running state, on the clone, before the apply relies on them.** Skip it only if the pair showed nothing worth pinning.

**Run it before the drill.** The drill restores the clone from its pre-upgrade snapshot, so afterwards there is no `<TO>` instance to test against.

Three things this settles that the pair cannot:

- Whether a **formula** such as `{DBInstanceClassMemory*3/4}` is accepted and evaluates on the new family, rather than only on the old one where it was observed.
- Whether the values actually resolve as intended once applied together - a derived value and the switch that derives it interact.
- Whether a `static` parameter reaches the running server, or sits at `pending-reboot` while the instance keeps the old value. That failure state looks exactly like a successful apply.

```bash
# The group the clone is on, and the pins to rehearse. Derive this list from the
# step 8 / step 9 pair rather than copying it: it is version-specific.
#
# JSON, not the shorthand ParameterName=x,ParameterValue=y form. RDS accepts an
# expression such as {DBInstanceClassMemory*3/4} as a parameter value, and the CLI's
# shorthand parser cannot carry one: it reads the brace as the start of a nested
# structure and fails client-side with
#   Error parsing parameter '--parameters': Expected: '=', received: '*'
# No amount of shell quoting helps, because the shell is not what is parsing it.
PG=rehearsal-ssl-to
PINS_JSON='[
  {"ParameterName":"innodb_dedicated_server","ParameterValue":"0","ApplyMethod":"pending-reboot"},
  {"ParameterName":"innodb_buffer_pool_size","ParameterValue":"{DBInstanceClassMemory*3/4}","ApplyMethod":"pending-reboot"},
  {"ParameterName":"innodb_redo_log_capacity","ParameterValue":"2147483648","ApplyMethod":"pending-reboot"},
  {"ParameterName":"innodb_io_capacity","ParameterValue":"200","ApplyMethod":"pending-reboot"},
  {"ParameterName":"innodb_io_capacity_max","ParameterValue":"2000","ApplyMethod":"pending-reboot"}
]'
printf '%s\n' "$PINS_JSON" | python3 -m json.tool
# Expected: valid JSON, one object per pin, each expression intact. A parse error here
# is a typo in the list and costs nothing; the same typo inside the modify call below
# would be reported against the whole call.

# 1. Prove the group is attached to nothing but the clone. A pin on a group the live
#    instance is using would take effect there, and most of these are dynamic.
#    The filter looks at every attached group, not just the first: an instance may
#    carry more than one, and a check that reads DBParameterGroups[0] alone would
#    miss the group in any other position.
aws rds describe-db-instances \
  --query "DBInstances[?DBParameterGroups[?DBParameterGroupName=='${PG}']].DBInstanceIdentifier" \
  --output text
# Expected: exactly the clone's identifier, and nothing else. Anything else aborts.

# 2. Apply the pins, behind both guards. They are chained with && rather than run as
#    separate lines, so a refusal stops the modify instead of only printing above it -
#    the same predicate PRE-1 verified, which accepts only rehearsal-scoped names, so
#    the live group and the live instance are both refused.
#    Every pin is pending-reboot, including the dynamic ones, so one reboot applies
#    them together rather than leaving a half-configured server.
assert_rehearsal "$PG" && assert_rehearsal "$CLONE" && \
  aws rds modify-db-parameter-group --db-parameter-group-name "$PG" --parameters "$PINS_JSON"

aws rds describe-db-parameters --db-parameter-group-name "$PG" --source user \
  --query 'Parameters[].[ParameterName,ParameterValue,ApplyMethod]' --output text
# Expected: the pins read back, plus whatever the group already carried. One bad value
# fails the whole modify and changes nothing, so if a pin is missing here, stop - do
# not reboot, because there is nothing waiting to be applied.

aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].DBParameterGroups[0].ParameterApplyStatus' --output text
# Expected: pending-reboot. This is the "before" half of the reboot check: it confirms
# the modify reached the instance and that a reboot is genuinely required.

# 3. Reboot the clone, which is what a static parameter needs. Behind the guard again,
#    chained for the same reason.
#
#    Neither a waiter nor a status poll gates this reliably. reboot-db-instance returns
#    immediately, and RDS can take **minutes** to begin the shutdown - measured on this
#    stack at about two and a half minutes, during which the instance reports available
#    throughout. A wait issued straight afterwards is satisfied by that pre-reboot
#    state and returns at once; a bounded status poll expires before the transition it
#    is watching for. The event stream is the only deterministic signal, which is the
#    same conclusion step 6 reached about measuring the upgrade.
SINCE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
assert_rehearsal "$CLONE" && \
  aws rds reboot-db-instance --db-instance-identifier "$CLONE" >/dev/null

# Poll the events for a restart newer than the call. --start-time is what keeps a
# restart from an earlier attempt out of the match, which matters on a re-run.
for i in $(seq 1 60); do
  EV=$(aws rds describe-events --source-identifier "$CLONE" --source-type db-instance \
    --start-time "$SINCE" --query "Events[?contains(Message,'restarted')].Date" \
    --output text)
  [ -n "$EV" ] && { echo "restarted at $EV"; break; }
  sleep 10
done
aws rds wait db-instance-available --db-instance-identifier "$CLONE"
# Expected: "restarted at ..." within a few minutes. The loop is bounded at ten
# minutes; if it ends without printing, the reboot has not happened and **the readings
# below would be the pre-reboot ones**. Every pin here is pending-reboot, so those
# readings show the <TO> defaults, which reads as the pins having failed when they have
# simply not been applied yet. That mistake costs a working pin its place in phase 1.

# The full event sequence, for the record: the parameter group update, the shutdown and
# the restart, with the outage between the last two.
aws rds describe-events --source-identifier "$CLONE" --source-type db-instance \
  --duration 15 --query 'Events[].[Date,Message]' --output text

aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].{status:DBInstanceStatus,pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus}'
# Expected: available, the rehearsal group, and pgs now in-sync rather than
# pending-reboot. The change from the earlier reading is the point. A status of
# incompatible-parameters means one of the pins is not viable - see below.
```

Then re-run **step 8's SQL block** and read the same variables. They should now report the `<FROM>` figures rather than the `<TO>` defaults, on a `<TO>` server.

**The one real risk, and why it is run here.** A bad `static` parameter can leave the instance in `incompatible-parameters`, unable to start. On a throwaway clone that costs nothing, and finding it here is the entire point - the alternative is finding it on a live instance during the apply. It also **does not block the drill**: the drill restores from the clone's automatic pre-upgrade snapshot, which is independent of the clone's current state and attaches the outgoing-family group, and `delete-db-instance` works on an instance in that state. If it happens, record which pin caused it and drop that pin from the phase 1 group.


#### The rollback drill — steps 12 to 14

**This group is the reason 0A exists.** It is the only place the rollback is executed rather than described.

**0A can be split across days here**, because from this point the scheduled stop no longer constrains
anything: the drill restores from the clone's *own* automatic pre-upgrade snapshot, held by the retention
period the clone inherited in step 4, rather than from the nightly one.

A reasonable split is steps 1 to 10 on one day, then step 11, the drill and the teardown on the next.
Doing the upgrade group's checks on the first day is worth it: the precheck log is an abort condition for
the whole plan, and `SELECT VERSION()` is what proves the upgrade reached the engine rather than being
queued. Learn both before walking away.

**If the terminal stayed open**, the shell process still holds every variable and helper, and there is nothing to rebuild. Verify that rather than assume it, because one thing does expire on its own:

```bash
echo "env=$ENV from=$FROM to=$TO db=$DB clone=$CLONE"
for f in ssm_sh assert_rehearsal confirm_target drop_clone drop_snapshot redact; do
  declare -f "$f" >/dev/null 2>&1 && echo "$f: present" || echo "$f: MISSING - re-paste it"
done
aws sts get-caller-identity --query Account --output text
```

The last line is the one that matters. **Credentials expire while the shell does not**, so an overnight pause typically ends with an `ExpiredToken` on the first call - re-authenticate and carry on. Nothing else goes stale: an instance endpoint is derived from its identifier, so both the clone's and the live instance's survive a reboot and an overnight destroy-and-restore, and `ssm_sh` re-resolves the SSM target on every call rather than caching it.

To resume **in a new shell** instead:

```bash
# The helpers and every variable are shell state and do not survive a new terminal.
# Re-run the session setup from the top of Procedure, which sets FROM and TO as well.
DB=${ENV}-optinist-cloud-rds
CLONE=${ENV}-optinist-rds-upgrade-rehearsal
# Re-paste the four guard helpers, then:
declare -f drop_clone
# Expected: assert_rehearsal, confirm_target, --no-delete-automated-backups.

# The drill's restore in step 13 needs these. Re-read them; the values are the same
# even though the live instance is a different one after an overnight cycle.
read -r CLASS STORAGE SUBNET SG <<<"$(aws rds describe-db-instances \
  --db-instance-identifier "$DB" --output text \
  --query 'DBInstances[0].[DBInstanceClass,StorageType,DBSubnetGroup.DBSubnetGroupName,
           VpcSecurityGroups[0].VpcSecurityGroupId]')"

aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].{status:DBInstanceStatus,ev:EngineVersion,
           pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus,
           endpoint:Endpoint.Address}'
# Expected: available, <TO>.x, the target-family rehearsal group, in-sync - the clone
# is still where the first day left it. The endpoint is unchanged by a reboot, so
# steps 8 and 11 can re-read it the same way.
```

Steps 8 to 11 also need the in-VPC command path, so **wait for the environment to have started** before resuming: `ssm_sh` looks for a *running* instance and fails fast if the morning cycle has not completed. The drill and the teardown use only the RDS API and do not care.

Nothing touches the clone overnight, and this is worth knowing rather than assuming: the scheduler acts on **one explicitly configured identifier** - `RDS_INSTANCE_ID` in its own environment - not on a tag selector, so a clone that inherited the live instance's tags from the snapshot is still out of its reach. See edge case 3 for why those inherited tags are misleading in other ways. The clone does keep billing, which is the cost of the split.

**What this drill is a rehearsal of:** the [Rollback](#procedure-1) procedure, which is the authoritative
version and is written for a live instance. **Its steps are numbered separately from this phase's** - the
mapping is below.

| Rollback step | Rehearsed here? |
|-------------------------|-----------------------|
| 0 — choose and confirm the restore source | Yes, as **step 12** |
| 1 — stop traffic, disable the manager rules | No. A clone serves nothing |
| 2 — free the identifier, restore under it | Yes, as **steps 13 and 14** |
| 3 — re-register the proxy target | No. The clone is deliberately kept off the proxy; Phase 2 covers this for real |
| 4 — revert Terraform and re-converge | No. The clone is outside Terraform state |
| 5 — restore traffic, check the scheduler | No, for the same reason as 1 |

So the drill proves the restore mechanics and measures them. The four steps it cannot reach are only ever
exercised in an actual rollback, which is why the Rollback section is written to be read on its own.

Read the Rollback section for the concepts - why a restore *is* the rollback, which recovery points
exist, and how long the window stays open. They are not repeated here.

##### Step 12 — locate the automatic pre-upgrade snapshot

```bash
# Look before selecting. --snapshot-type automated returns the daily backups as well
# as the pre-upgrade ones, and the clone was briefly on <FROM> before its own upgrade,
# so more than one snapshot can match the version filter. Step 13 deletes the clone on
# the strength of this choice, so make the choice visible.
aws rds describe-db-snapshots --db-instance-identifier "$CLONE" --snapshot-type automated \
  --query 'reverse(sort_by(DBSnapshots,&SnapshotCreateTime))[].[SnapshotCreateTime,
           EngineVersion,DBSnapshotIdentifier]' --output text
# Expected: the pre-upgrade snapshots on <FROM>, recognisable by "preupgrade" in the
# identifier, plus any daily backup. Anything on <TO> is post-upgrade and is not a
# rollback point.

PRE=$(aws rds describe-db-snapshots --db-instance-identifier "$CLONE" \
  --snapshot-type automated \
  --query "reverse(sort_by(DBSnapshots[?starts_with(EngineVersion,'${FROM}')],
           &SnapshotCreateTime))[0].DBSnapshotIdentifier" --output text)
echo "$PRE"
# Expected: the newest <FROM> entry from the list above, and one of the pre-upgrade
# ones. "None" means RDS took none, so check the retention period from step 4 - and
# note that the same absence on a real instance would mean the Rollback procedure has
# lost one of its recovery points.

# Everything step 13 needs, checked now, while the clone is still here to fall back on.
# Step 13 deletes before it restores, so a blank or stale value below becomes a failed
# restore with no clone left to retry from.
[ -n "$PRE" ] && [ "$PRE" != None ] || echo "ABORT: no ${FROM} snapshot to restore from"

aws rds describe-db-snapshots --db-snapshot-identifier "$PRE" \
  --query 'DBSnapshots[0].{status:Status,ev:EngineVersion,created:SnapshotCreateTime}'
# Expected: available, <FROM>.x. One still "creating" is not a restore source.

aws rds describe-db-parameter-groups --db-parameter-group-name rehearsal-ssl-from \
  --query 'DBParameterGroups[0].[DBParameterGroupName,DBParameterGroupFamily]' --output text
# Expected: the group, on the <FROM> family. Its absence is B3 waiting to happen - an
# outgoing-version snapshot cannot restore against an incoming-family group.

echo "class=$CLASS storage=$STORAGE subnet=$SUBNET sg=$SG"
# Expected: all four non-empty.
```

##### Step 13 — free the identifier, then restore under it

```bash
# Free the identifier, then restore under it. This is the path that keeps
#    Terraform convergent. Time both halves.
#
#    The guard is CHAINED with && rather than followed by `|| return 1`. At the top
#    level of a shell, `return` is not a reliable way to stop: bash reports an error and
#    carries on to the next line, which here is the delete. This is the one delete in
#    the procedure that is meant to happen, which makes it the one most likely to be
#    re-run against the wrong name.
# t0
date -u
assert_rehearsal "$CLONE" && \
  aws rds delete-db-instance --db-instance-identifier "$CLONE" \
    --final-db-snapshot-identifier "${CLONE}-broken" --no-delete-automated-backups

aws rds wait db-instance-deleted --db-instance-identifier "$CLONE"
# t1. t1 - t0 is the delete, final snapshot included.
date -u

#    --no-delete-automated-backups above is what keeps $PRE restorable: the flag
#    defaults to true and would take the restore source down with the instance.
#    Confirm it rather than trust it, while there is still a decision to make.
aws rds describe-db-snapshots --db-snapshot-identifier "$PRE" \
  --query 'DBSnapshots[0].{status:Status,ev:EngineVersion}'
# Expected: still available, still <FROM>.x. If it has gone, the drill cannot continue -
# and nothing is lost, because ${CLONE}-broken holds the <TO> state as a manual snapshot.

#    The restore is guarded too, and for a different reason than the delete: on a
#    mistyped identifier that happens not to exist, an unguarded restore does not fail -
#    it succeeds, and creates an instance nobody wanted.
assert_rehearsal "$CLONE" && \
  aws rds restore-db-instance-from-db-snapshot \
    --db-instance-identifier "$CLONE" --db-snapshot-identifier "$PRE" \
    --db-instance-class "$CLASS" --storage-type "$STORAGE" --port 3306 \
    --db-subnet-group-name "$SUBNET" --vpc-security-group-ids "$SG" \
    --db-parameter-group-name rehearsal-ssl-from \
    --no-publicly-accessible --no-multi-az

aws rds wait db-instance-available --db-instance-identifier "$CLONE"
# t2. t2 - t1 is the restore.
date -u
```

**Record t0, t1 and t2.** The delete and the restore are separate numbers in a rollback decision: only
the second is unavoidable, and the first shrinks if the final snapshot is skipped. Wall clock is the right
measure here, unlike the upgrade in step 6 - there the waiter returned long after the database was serving
again, whereas here nothing can use the instance until the restore completes.

##### Step 14 — confirm what came back

```bash
# Confirm what came back
aws rds describe-db-instances --db-instance-identifier "$CLONE" \
  --query 'DBInstances[0].{ev:EngineVersion,endpoint:Endpoint.Address,rid:DbiResourceId,
           pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus}'
# Expected: EngineVersion back at <FROM>.x - the engine version is a property of the
# snapshot; the endpoint hostname identical to before the drill; DbiResourceId
# different, which is why a real rollback must re-register the proxy target; and the
# parameter group back to the outgoing-family rehearsal copy, in-sync. A group still
# reading default.mysql<FROM> once the instance is available means the restore's
# --db-parameter-group-name did not take, and the instance is not carrying
# require_secure_transport.
```

**Metadata is not proof that the rollback worked.** Everything above is the control plane's account of the instance; none of it says the database serves. A rollback that produces correct metadata and an unreachable database is a failed rollback, so **run step 8's SQL block once more against the restored instance** - re-read `CLONE_HOST` first, since the restore issued a new one even though the hostname is unchanged.

That single connection closes three things at once:

- **TLS succeeds against the outgoing-family group**, which is the only evidence that the group carried `require_secure_transport` through the restore.
- **`VERSION()` reports `<FROM>.x` from the engine**, not from the API.
- **The `<FROM>` baseline reproduces itself.** The restored instance is on the outgoing group with no pins, so the InnoDB values should match the step 9 readings exactly. They are the same measurements taken a third time, on an instance built from a snapshot rather than from the live volume, which is a stronger check on the step 8 / step 9 pair than either reading alone.

This is the last opportunity: the teardown below removes the instance.


Record the measured durations. They replace estimates in any rollback decision.

#### Teardown

Tear down as soon as the drill is finished, and in any case before phase 1 - that is the hard deadline,
because a clone still referencing the live parameter group makes the phase 1 apply fail to destroy it
(caveat 1). Same-day is the cost-saving deadline, not the correctness one.

```bash
drop_clone "$CLONE"

# The clone's own snapshots and retained automated backups, now that the instance is
# gone. These exist because drop_clone deliberately keeps them; clean them up here
# rather than by weakening the delete.
#
# drop_clone's final snapshot can still be creating when the instance delete completes,
# and delete-db-snapshot refuses a snapshot in that state. Wait rather than retry.
aws rds wait db-snapshot-available --db-snapshot-identifier "${CLONE}-final"
drop_snapshot "${CLONE}-final"
drop_snapshot "${CLONE}-broken"

# The retained automated backups. There are two sets, not one: the clone was deleted
# twice, by the drill and by drop_clone above, and both passed
# --no-delete-automated-backups, so each left its own set under the same identifier with
# a different DbiResourceId. $PRE lives in the first of them.
#
# **This loop is the one destructive step with no per-item confirmation, and its only
# input is an identifier.** Pointed at the live instance it would delete that
# environment's entire retained backup history - on development that is one set per
# nightly cycle, so tens of them, and it is the whole point-in-time recovery record.
# Hence the guard, and hence listing before deleting.
assert_rehearsal "$CLONE" && \
  aws rds describe-db-instance-automated-backups \
    --query "DBInstanceAutomatedBackups[?DBInstanceIdentifier=='${CLONE}'].[DBInstanceAutomatedBackupsArn,
             DbiResourceId,Status]" --output text
# Expected: two rows, carrying the two DbiResourceId values this clone had - the one
# from before the drill and the one from after it. More than two, or an unfamiliar
# resource id, means the filter is matching something else. Stop.

assert_rehearsal "$CLONE" && \
  for ARN in $(aws rds describe-db-instance-automated-backups \
    --query "DBInstanceAutomatedBackups[?DBInstanceIdentifier=='${CLONE}'].DBInstanceAutomatedBackupsArn" \
    --output text); do
    aws rds delete-db-instance-automated-backup --db-instance-automated-backups-arn "$ARN"
  done

aws rds delete-db-parameter-group --db-parameter-group-name rehearsal-ssl-from
aws rds delete-db-parameter-group --db-parameter-group-name rehearsal-ssl-to

# Expected: nothing rehearsal-shaped remains, on any of the four kinds of resource
# this phase created. The first two are the same queries PRE-1 check a ran before
# anything existed, so an empty result here restores the starting state.
aws rds describe-db-instances \
  --query 'DBInstances[?contains(DBInstanceIdentifier,`rehearsal`)].DBInstanceIdentifier' \
  --output text
aws rds describe-db-snapshots --snapshot-type manual \
  --query 'DBSnapshots[?contains(DBSnapshotIdentifier,`rehearsal`)].DBSnapshotIdentifier' \
  --output text
aws rds describe-db-parameter-groups \
  --query 'DBParameterGroups[?contains(DBParameterGroupName,`rehearsal`)].DBParameterGroupName' \
  --output text
aws rds describe-db-instance-automated-backups \
  --query "DBInstanceAutomatedBackups[?DBInstanceIdentifier=='${CLONE}'].[DBInstanceAutomatedBackupsArn,Status]" \
  --output text
```

**Expect the drill to have produced two retained automated backup sets, not one.** The clone was deleted
twice - once by the drill and once here - and both deletes passed `--no-delete-automated-backups`, so each
left its own set under the same identifier and a different `DbiResourceId`. The loop above iterates over
every match for that reason. A single deletion pass that only handles one of them leaves the other
billing quietly.

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

**This phase needs the Terraform edit present in the tree Terraform plans from.** The gate asserts that
the instance shows `update` and that the parameter group is created under a generated name - both are
properties of the change, so an unmodified checkout produces "no changes" and the gate proves nothing.
Whether the edit is committed makes no difference to the plan, and phase 1 requires a clean worktree
(**B6**), so committing it first is the simpler path.

**Run it from the directory Terraform actually applies from.** That may not be the repository you have been
editing: a separate deployment checkout is a common arrangement, and it is the one whose working tree
`ecr_build_push.sh` builds from and whose backend config Terraform reads. Confirm `infrastructure.tf` there
carries the edit before planning, not the copy you typed it into.

**Expect more in the plan than the RDS changes**, and know which extras are benign:

- **Read the deploy trigger from the state, not from the checkout.** `null_resource.build_and_deploy`
  triggers on the ALB DNS name, the ECR repository URL, `var.git_branch` and - depending on the revision
  of the configuration that was last applied - `source_revision`, the git commit. Whether that last key is
  present decides whether an apply from a new commit rebuilds the image at all, and the checked-out
  configuration is not evidence: only the plan's `before` triggers are.
- `data.external.tf_build_info` reads the real commit and feeds tags on the ECS cluster, so a checkout at
  a different commit than the last apply shows **`aws_ecs_cluster.main` updating its tags** - one
  resource, and benign.
- **A trigger key that disappears is also a change.** Applying a configuration older than the one in state
  removes the key and replaces the resource, which rebuilds the image once and then leaves the trigger set
  constant across commits - so later applies skip the build. That is a regression, not a rollout, and it is
  invisible unless the `before`/`after` trigger maps are compared.

**Record which resources the plan contains, not just the ones the gate asserts.** Whether the image
rebuild and the ECS rollout appear is what decides whether the apply is also an application deploy, and
therefore what the production window in phase 4 has to cover. That is a claim worth settling from a plan
rather than from reading the configuration.

This usually runs in a different terminal from the AWS CLI work, and in a different directory, so it needs
its own setup. It does not need the delete guards or `FROM`, because nothing here deletes anything.

```bash
export AWS_PROFILE=<the profile for this account>
export AWS_REGION=ap-northeast-1
export ENV=<the Terraform var.environment value>
cd <the deployment checkout>/infrastructure/terraform

# Prove which tree is about to be planned. With two checkouts of one repository this is
# the check that matters most, and it costs nothing.
git rev-parse --abbrev-ref HEAD
git log -1 --oneline
git status --porcelain
grep -nE 'family|name_prefix|engine_version|allow_major_version_upgrade' infrastructure.tf
# Expected: the phase 1 branch, a commit carrying the edit, and the INCOMING family and
# engine version in the grep. An outgoing-version value means this is the wrong
# checkout - fix that before planning, not after reading a confusing plan.

echo "ENV=$ENV"
# Expected: the development environment's value. **Stop if it is anything else.** The
# next command attaches this directory to that environment's remote state, and
# -reconfigure replaces whatever it was attached to before.
```

```bash
terraform init -backend-config="backends/${TF_ENV}.hcl" -reconfigure

# plan is read-only against AWS but it takes a state lock. Let it finish: an
# interrupted plan can leave a lock behind that blocks the next deploy.
# Outside the working tree: a plan file holds every variable value, and an untracked
# file inside the repository trips the clean-tree guard in ecr_build_push.sh - harmless
# here, because 0C never applies, but the same command is used where it is not.
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

# The pinned parameters, if the phase 0A pair produced any. Terraform has to accept
# them, and an RDS expression such as {DBInstanceClassMemory*3/4} is the one to watch:
# a bare brace is literal in HCL, but a mistyped ${...} would be read as interpolation.
terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.type=="aws_db_parameter_group"
    and (.change.actions|index("create")))
  | .change.after.parameter[]? | "\(.name) = \(.value) [\(.apply_method)]"'
# Expected: one line per pin, values intact, plus the parameters the group already had.
# Missing pins here mean the plan is for a different change than phase 1 will apply.

# The deploy trigger, before and after. This is what says whether the apply rebuilds
# the image, and why.
terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.address=="null_resource.build_and_deploy")
  | {before: .change.before.triggers, after: .change.after.triggers}'
# Expected: the two maps differ in exactly the way the change explains. A key present
# in before and absent in after means the configuration being applied is OLDER than the
# one in state - see the third note above.

# Every resource the plan touches, so the extras are recorded rather than noticed.
terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.change.actions != ["no-op"])
  | "\(.address) -> \(.change.actions|join(","))"' | sort

# Then attribute them. replace_paths names the attribute that forces each replacement,
# which is what separates "our change did this" from "this was already drifting".
terraform show -json "$PLAN" | jq -r '
  .resource_changes[] | select(.change.actions != ["no-op"])
  | "\(.address)  replace_paths=\(.change.replace_paths // [])"'
# Expected from the engine change itself: the instance updating in place, the parameter
# group replaced on family and name_prefix, and whatever derives the group's name -
# typically a scheduler environment variable and an IAM policy scoped to it.
#
# **Everything else is drift, and it will ride along with the apply.** An AMI data
# source with most_recent = true replaces its instance whenever the upstream image is
# rebuilt; a replacement like that has nothing to do with the engine upgrade and can
# take network egress with it. Triage the list before deciding to apply: the gate above
# passes on the two RDS resources while any number of unrelated resources move.

rm -f "$PLAN"
```

0C cannot prove the replacement succeeds - a name collision only surfaces at apply time.

### Phase 1: apply to development

Weekdays, while the environment is up, and finishing before the scheduled stop (B3, B4).

**Fourteen steps in three groups, numbered one for one with #897's tasks P1-1 to P1-14.** Phase 4 applies
this same sequence against production, so the numbering is what lets it cite the steps instead of restating
them: read **"phase 1 step N"** wherever phase 4 refers to one. Phase 0A's steps are a separate sequence -
always say which phase's step is meant.

**Run the steps in one shell session.** Four variables are established in one step and read in a later one:
`PRESNAP` (step 4), `PLAN` (step 7), `NEWPG` and `LIVE_HOST` (step 12). A step is one paste; a fresh session
loses the lot.

#### Before the apply — steps 1 to 6

Nothing here changes the database. All six are preconditions, and **step 1 and step 6 are the two that
cannot be done afterwards.**

##### Step 1 — the `<FROM>` readings exist, and the pins are decided

Two preconditions that a later step cannot recover. **This step is a read, plus one file check - it changes
nothing.**

```bash
# The pins the apply will carry must already be in the Terraform change. Read them from
# the file rather than from memory: a pin that is absent here is a tuning change that
# ships silently alongside the version change, and the family diff will not show it.
grep -oE 'name += +"innodb_[a-z_]+"' infrastructure/terraform/infrastructure.tf | sort
# Expected: one line per pin the phase 0A step 8 / step 9 pair justified. For 8.0 -> 8.4
# that is six: dedicated_server, buffer_pool_size, buffer_pool_instances,
# redo_log_capacity, io_capacity, io_capacity_max.
```

```bash
# The "before" half of the pair. Confirm it was recorded; if it was not, take it NOW.
# On development it came from phase 0A step 9. On production there is no phase 0A, so
# this is where it happens - and after the apply the <FROM> instance no longer exists,
# so there is no later opportunity.
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{ev:EngineVersion,endpoint:Endpoint.Address}'
# Expected: <FROM>.x. Then run phase 0A step 8's SQL block against that endpoint, unless
# the readings are already on file.
```

##### Step 2 — read the stop schedule, and extend it only if needed

```bash
# The deadline. The scheduler stops this environment on a fixed schedule, and an apply
# interrupted by that stop is B4 territory: the instance is deleted while Terraform is
# mid-flight. Read the schedule rather than remembering it.
aws events describe-rule --name "${ENV}-dev-schedule-stop" \
  --query '[Name,ScheduleExpression,State]' --output text
# Expected: the stop cron, and ENABLED. Convert it to local time - that is this phase's
# deadline, and finishing before it is the plan rather than the fallback.
```

**The override below is the fallback, not the default.** It is capped at twelve hours and expires on a clock
from the moment it is set, so an override set in the morning cannot reach an evening stop even at the cap.
Set it only when an overrun becomes likely, and late enough to clear the deadline.

```bash
aws lambda invoke --function-name "${ENV}-dev-scheduler" \
  --payload '{"action":"override","hours":12}' \
  --cli-binary-format raw-in-base64-out /dev/stdout
# Expected, in the FUNCTION's response rather than the invoke's own status:
#   {"statusCode": 200, "action": "override", "hours": N, "expires_at": "..."}
#
# A statusCode of 400 with "OVERRIDE_PARAM_NAME not configured" is the silent failure
# here: the invoke still reports success while nothing was set.
#
# Compare expires_at against the stop time read above. Falling short means the stop runs
# anyway. There is no second check available - the function exposes start, stop and
# override, and no way to query the override's state - so expires_at is the evidence.
#
# The next scheduled start clears the override unconditionally, so it cannot leak into
# the nightly cycles phase 2 needs. It does mean that skipping a stop leaves the
# environment up overnight, and the following start then runs against an environment
# that is already running.
```

**Production has no scheduler, so phase 4 skips this step entirely** - its timing is free. That is the one
step of the fourteen that does not carry over.

##### Step 3 — the instance is available

```bash
# Anything but available aborts (B4).
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{status:DBInstanceStatus,ev:EngineVersion}'
# Expected: available, <FROM>.x
```

##### Step 4 — the manual pre-upgrade snapshot

A manual snapshot is the **only** recovery point that survives an instance delete, so this is not optional,
and it is not complete until it reads `available`.

```bash
# PRESNAP, not SNAP. This snapshot is a recovery point that must outlive the phase,
# whereas SNAP elsewhere in this document names a restore *source* that gets deleted -
# phase 0B ends with drop_snapshot "$SNAP". Two meanings in one shell session is how a
# recovery point gets deleted by a line copied from another phase.
PRESNAP=${ENV}-pre-upgrade-$(date +%Y%m%d-%H%M)
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
group to attach.

```bash
aws rds copy-db-parameter-group \
  --source-db-parameter-group-identifier "${ENV}-optinist-ssl" \
  --target-db-parameter-group-identifier "${ENV}-optinist-ssl-rollback" \
  --target-db-parameter-group-description "outgoing-family rollback target"
```

```bash
# Verify it. A rollback attaches this group, so a copy that silently did not happen is
# only discovered during the restore - after the restore has been committed.
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
```

##### Step 6 — confirm the recovery points are real, before applying

**Before, not after.** Two of the four recovery points cannot be verified directly - the automatic
pre-upgrade snapshots do not exist until the upgrade takes them - so verify their **precondition** instead.
The full table of what each one restores to is in
[What you can roll back to after this apply](#what-you-can-roll-back-to-after-this-apply) below; this step
is the checking.

```bash
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{retention:BackupRetentionPeriod,delProt:DeletionProtection,
           window:PreferredBackupWindow}'
# Expected: retention greater than zero. **A zero means RDS takes no automatic
# pre-upgrade snapshot at all**, which removes a recovery point without saying so. On an
# environment rebuilt nightly this is worth re-reading today rather than trusting an
# earlier reading - the retention period is one of the things a restore can silently drop
# (edge case 5).

aws rds describe-db-instance-automated-backups \
  --query "DBInstanceAutomatedBackups[?DBInstanceIdentifier=='${DB}'].{status:Status,
           rid:DbiResourceId,from:RestoreWindow.EarliestTime,to:RestoreWindow.LatestTime}" \
  --output table
# Expected: one `active` row for the instance running now, whose window starts when this
# cycle started and runs to a few minutes ago. That row is what covers "just before this
# apply". Any `retained` rows are earlier cycles.
```

#### The gate — steps 7 to 9

Steps 7 and 8 are mechanical. **Step 9 is judgement, and it is the one that cannot be delegated to an
assertion.**

##### Step 7 — clean worktree, then plan to a file outside the tree

```bash
git status --porcelain
# Expected: empty (B6)
cd infrastructure/terraform
terraform init -backend-config="backends/${TF_ENV}.hcl" -reconfigure
```

```bash
# The plan file goes OUTSIDE the working tree. An untracked file inside the repository
# makes the tree dirty, and ecr_build_push.sh - which this apply invokes - refuses to
# build a dirty tree. Writing the plan into the repository therefore breaks the apply it
# was written for. mktemp also gives it 0600, which matters: a plan file contains every
# variable value, including whatever the tfvars carry.
PLAN=$(mktemp -t tfplan)
terraform plan -var-file="environments/${TF_ENV}.tfvars" -out="$PLAN"
```

##### Step 8 — the two gate assertions

Run the phase 0C assertions against `"$PLAN"`, **including the attribution step**. The gate passes on two
resources - `aws_db_instance.main` showing `update` rather than a replacement, and the parameter group
showing one create and one delete. **The plan will contain more than two, and every one of them applies.**

##### Step 9 — classify the whole plan by root cause

**Before applying, not after.** On a stack of any age the extra entries are not noise, they are separate
decisions that happen to be riding along. Each one belongs in one of these buckets, and the phase log
records which:

| Bucket | How to recognise it | What to do |
|-------------------------|-----------------------|-----------------------|
| The engine change | The instance, the parameter group, and whatever derives the group's name | Intended |
| The deploy (**B6**) | `null_resource` replacements whose `triggers` carry the git commit | Expected. Its rollout time belongs in the announced window |
| Runtime state versus configuration | A count or an instance the configuration declares statically while something scales it at runtime | Decide deliberately. The apply will overwrite the runtime state |
| Drift | An AMI data source with `most_recent`, a JSON body, a recomputed hash | Usually harmless, but read the ones that replace rather than update |

**A replacement is not an update.** Anything in the plan showing `delete,create` rather than `create,delete`
is destroyed before its successor exists, so whatever depends on it is down in between. An egress path,
a NAT instance or a gateway in that state takes the private subnets with it for as long as the
replacement takes - and if the in-VPC command path has no interface endpoint of its own, the checks in
steps 11 to 14 fail during that window. **Retry them once the apply settles rather than diagnosing them**,
and record the gap so that a soak metric can be attributed to it instead of to the engine.

#### The apply and what it must prove — steps 10 to 14

**Step 10 is the irreversible one.** Steps 11 to 14 are four separate claims, and passing three of them
proves nothing about the fourth.

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

##### Step 12 — the pins took effect

Skip this only if the phase 0A pair found nothing worth pinning. **A pin that did not apply is the failure
state worth naming, because it looks exactly like a clean apply**: the group shows the value, the instance
shows `available`, and the engine is still running the `<TO>` default.

```bash
# The group's name is generated by name_prefix (B1), so read it rather than assume it.
NEWPG=$(aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].DBParameterGroups[0].DBParameterGroupName' --output text)

# The configured side, and whether anything is still waiting for a reboot.
aws rds describe-db-parameters --db-parameter-group-name "$NEWPG" --source user \
  --query 'Parameters[].[ParameterName,ParameterValue,ApplyType,ApplyMethod]' --output text
# Expected: every pin present. A `static` pin whose value has not taken effect below
# needs a reboot - the upgrade's own reboot should have served, and if it did not,
# reboot-db-instance and re-read.
```

```bash
# The running side. Re-run the InnoDB readings from phase 0A step 8 against this
# instance rather than the clone: same block, with the endpoint below in place of
# CLONE_HOST.
LIVE_HOST=$(aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].Endpoint.Address' --output text)
echo "$LIVE_HOST"
# Expected: every pinned variable reports the `<FROM>` figure from the 0A pair, not
# the `<TO>` default. Derived values follow their pin - a buffer pool back at its
# `<FROM>` size brings `innodb_buffer_pool_instances` back with it, so that one
# corroborates without being pinned itself.
```

Record the result next to 0A's pair. Three columns - `<FROM>` measured, `<TO>` default measured, `<TO>`
pinned measured - is what makes the soak's metrics attributable, and it is also what #898 quotes when it
argues the production apply changes one thing.

##### Step 13 — the scheduler Lambda follows the renamed group

```bash
# B1: name_prefix generated a new name, and every consumer of the old literal must have
# followed. This is the consumer that a plan does not obviously show.
aws lambda get-function-configuration --function-name "${ENV}-dev-scheduler" \
  --query 'Environment.Variables.RDS_PARAMETER_GROUP_NAME'
# Expected: the name_prefix-generated name, not the old literal
```

**Production has no scheduler, so phase 4 has one fewer consumer to check** - the instance and the IAM
policy remain.

##### Step 14 — the proxy target is healthy

```bash
aws rds describe-db-proxy-targets --db-proxy-name "${ENV}-optinist-rds-proxy" \
  --query 'Targets[].{id:RdsResourceId,state:TargetHealth.State,reason:TargetHealth.Reason}'
# Expected: one target, AVAILABLE
```

An in-place upgrade does not move `DbiResourceId`, so no re-registration should be needed - **confirm it
rather than assume it.** A restore does move it, which is why the rollback procedure has a re-registration
step and this one does not.

#### What you can roll back to after this apply

**Reference for step 6, not a step of its own.** Step 6 holds the two commands that check this; what
follows is what each recovery point is, and what the window shapes mean. The rollback procedure is in
[Rollback](#rollback); this is what it has to work with.

| Recovery point | Created by | Expires | Restores to |
|-------------------------|-----------------------|-----------------------|-----------------------|
| `<ENV>-pre-upgrade-<date>` | **Step 4** | **Never** - manual snapshots persist until deleted | `<FROM>`, the state immediately before the apply |
| `<ENV>-optinist-ssl-rollback` | **Step 5** | Never | The parameter group that `<FROM>` instance needs |
| Automatic pre-upgrade snapshots | RDS, up to two, immediately before the upgrade | With the retention period | `<FROM>` |
| Point-in-time recovery | The automated backup | With the retention period | Any point **inside a window** - see below |

The first two rows are created and verified by their own steps. The other two cannot be verified directly -
the automatic snapshots do not exist until the upgrade takes them - which is why **step 6 checks their
precondition instead**: a retention period greater than zero, and an `active` automated-backup row covering
the moment before the apply.

**On development, point-in-time recovery is a set of windows, not one span.** The retention period reads
the same as production's, but the instance is rebuilt every cycle, so each cycle carries its own backup:
one `active` row for the current instance, and one `retained` row per earlier cycle, each covering only
that cycle's own lifetime. Production, which is never deleted, does have a single continuous window.

What that means in practice on development - read the `describe-db-instance-automated-backups` output
rather than assuming, because the shape follows whatever the schedule is:

| Target moment | Recoverable? |
|-------------------------|-----------------------|
| Today, e.g. just before this apply | **Yes** - the `active` window |
| An earlier day, within that day's running hours | **Yes** - from that day's `retained` backup |
| Overnight, between a stop and the next start | **No** - no instance existed |
| A day the environment did not run at all | **No** - same reason |
| Older than the retention period | No |

So point-in-time recovery does cover a same-day rollback of this apply, and it covers a later rollback
at the granularity of a running day. The manual snapshot from step 4 is still the primary recovery
point, for three reasons that do not depend on the window shape: its contents are known, it is
identified by name rather than by reconstructing which window contains a moment, and it survives an
instance delete. Take it and verify it regardless.

**A rollback after a night has passed needs one more thing.** The evening stop replaces the scheduler's
fixed-name snapshot with a `<TO>` one, and the Lambda points at the `<TO>`-family parameter group. So a
rollback must also revert the Lambda's parameter group, or the next nightly restore aims the new family
at an old snapshot - B3 in reverse. The [Rollback](#rollback) procedure's step 4 covers it; it is easy
to miss when the rollback is framed as "undo the apply".

### Phase 2: two nightly destroy and restore cycles

This proves B3. Within each cycle, check 1 runs in the evening after the stop; the rest need the morning restore.

**This phase is not optional in the way the others are, and not because it gates the release.** The cycle
runs whether anyone watches it or not, so the only choice is between a twenty-minute deliberate check and
finding out because the environment is broken in the morning. Edge case 5 and the proxy re-registration are
both failures that have happened on this stack.

**Be precise about what it does and does not gate.** An environment that is never destroyed has no nightly
cycle, so none of this bears on that environment's *upgrade*, and nor does it bear on its *rollback* - a
rollback restores an outgoing-version snapshot against the retained outgoing-family group, which phase 0A
drills directly. What this phase uniquely covers is the other direction: **an incoming-version snapshot
restored against the incoming-family group.** Any later restore of the upgraded environment takes that path,
so the finding is worth having - it is just not a gate on the upgrade itself.

**The six checks are not one sitting.** Check 1 is the evening, after the stop; checks 2 to 6 need the
morning restore, and they are grouped below accordingly. The checks are numbered 1 to 6 throughout, and
#897's sub-checks **a** to **e** map onto them — **c** covers checks 3 and 4 together.

> **Do not run the morning group in the evening.** `stop_rds()` **deletes** the instance rather than
> stopping it, so `describe-db-instances` returns `DBInstanceNotFound` and the proxy has no target. The
> failure is loud rather than misleading, but the single block this used to be invited exactly that mistake.

#### The evening, after the stop — check 1

```bash
# 1. The evening snapshot must itself be on the new version
aws rds describe-db-snapshots --db-snapshot-identifier "${DB}-dev-scheduler" \
  --query 'DBSnapshots[0].{ev:EngineVersion,status:Status,created:SnapshotCreateTime}'
# Expected: <TO>.x, available, tonight's timestamp.
# Still <FROM> means the upgrade never completed.
```

This is the only check that has to happen in the evening, and it is the cheapest of the six. **The snapshot
it reads is the one the morning restore consumes**, so a `<FROM>` reading here predicts tomorrow's failure
before it happens.

#### The morning, after the restore — checks 2 to 6

Run these as one block: `SINCE` is set in check 3 and read again in check 4.

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
| 2 | The deployed e2e lanes are green, with skips promoted to failures. The environment's own release set may stand in for the two expensive lanes - see [Testing](#testing) for what that substitution does and does not cover |
| 3 | The engine-version assertion is green - see [Testing](#testing) |
| 4 | Every scheduled job has run at least once on the new version with no SQL error, by invoking it rather than waiting |
| 5 | The log queries are clean |
| 6 | Production baseline metrics captured - this cannot be done after phase 4 |

**The duration is compressible. The checks are not, and one of them is irreversible.** Under release
pressure the temptation is to shorten the phase by dropping criteria, so rank them before deciding:

| Criterion | What skipping it costs | Verdict |
|-------------------------|-----------------------|-----------------------|
| **6** | **The ability to answer "did the upgrade make it slower?", permanently.** There is no second chance after phase 4 | **Never skip.** It is one command and a few minutes - the worst value-per-minute trade available |
| **2** | The only application-level evidence that the new version runs the application's real queries. The pre-check is schema-driven and never sees a query | **Do not skip** - but it can be made much cheaper. See [Testing](#testing): the release set plus criterion 4 substitutes for the two expensive lanes |
| **4** | Paths that fail silently inside a scheduled job rather than visibly in the application. **It also carries part of criterion 2** once the expensive lanes are substituted, because it is what exercises the storage tracking and premium paths | **Do not skip** |
| 5 | Partly covered by 2 and 4 | Can be shortened |
| 1 | One cycle carries most of the decision; the second protects the environment's own nightly operation | One cycle can suffice to decide phase 4 |
| 3 | Future regression cover, not verification of this upgrade. Every assertion in it is also a manual step in phase 4's checklist | Can follow phase 4 |

So the phase can be finished in a day or two of checks rather than a week of waiting - but **finish it by
running the criteria, not by letting time pass.**

**Each criterion has its own section below.** Three of them are carried out elsewhere in this document and
appear here as pointers, so that every number in the table above resolves to a heading rather than to
whichever section the reader happens to know about.

#### Criterion 1 — two nightly cycles

Carried out by [Phase 2](#phase-2-two-nightly-destroy-and-restore-cycles): its six checks, on each of two
consecutive cycles. Nothing to run here.

**One cycle carries most of the phase 4 decision.** The second protects this environment's own nightly
operation rather than the upgrade, so it can run alongside or after phase 4 - see the ranking above.

#### Criterion 2 — the deployed e2e evidence

Carried out by the lanes in [Testing](#testing), with skips promoted to failures - `E2E_FAIL_ON_SKIP=1`,
which fails the run on any skip *or on zero executed tests*. Without it a lane that skipped everything reads
green, and the cases that reach the database are individually gated.

**The two expensive lanes may be substituted by the environment's own release set**, and
[Choosing the lanes](#choosing-the-lanes-and-substituting-the-release-set-for-the-expensive-two) sets out
what that does and does not cover. **The substitution is conditional on criterion 4 being run in full**,
because the largest hand-written SQL surface in the repository is only ever executed there.

#### Criterion 3 — the engine-applied test case

New test coverage rather than verification of the upgrade in hand: see
[The engine-version assertion](#the-engine-version-assertion). **Every assertion in it also appears as a
manual step in phase 4's checklist**, so it can follow phase 4 without weakening any phase. It is the one
criterion that is safe to defer.

#### Criterion 4 — invoke every scheduled job

Invoking rather than waiting. Each payload is the literal `input` the EventBridge target sends, so a manual invoke reproduces the scheduled invocation.

**This step writes and deletes.** It is not a read-only probe like criterion 6. The cleanup jobs remove data, and `ExpirationLifecycleJob` runs on a 24-hour cadence, so invoking it performs a day's worth of expiry processing at once. On a shared environment, announce it in the same breath as the upgrade.

> **Do not invoke `<ENV>-dev-scheduler`.** It is a Lambda like the others and it sits in the same naming
> scheme, but it is the one that **stops and restores the database**. Invoking it during this phase deletes
> the instance you are soaking. The count of scheduled jobs includes it; the list below deliberately does
> not.

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

**The verdict needs both conditions; neither alone is enough.**

- **`StatusCode: 200` is not success.** A Lambda that starts and then raises still returns 200, with
  `"FunctionError": "Unhandled"` beside it. Trusting the status code would miss a SQL error inside the
  handler, which is the exact failure this criterion exists to detect.
- **Absence of `FunctionError` is not success either.** A `ResourceNotFoundException` or an
  `AccessDeniedException` never reaches the handler, so the CLI's error text contains no `FunctionError` at
  all - testing only for that string would report a Lambda that never ran as having passed.

So the helper requires a 200 **and** no `FunctionError`, and prints one verdict line per invoke instead of
leaving it to be read out of the JSON. Verified against seven shaped inputs in both shells: success,
success with no spaces in the JSON, a raised handler, not-found, access-denied, a 429, and empty output.

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

**One Lambda is reached by none of this.** `free_cleanup` holds about fifteen raw statements, has no EventBridge schedule, and no invoker appears either in the Terraform or in the free-manager package - so it is correctly outside a list of *scheduled* jobs, yet its SQL is exercised by nothing in this phase. Record it as an uncovered path rather than invoking it blind: it deletes, and firing a deletion Lambda whose trigger and payload have not been established is a worse risk than the gap. Establishing what invokes it is separate work.

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

**`docker exec` needs `-i` here, and without it this block silently does nothing.** `python -` reads its program from stdin, and **`docker exec` does not forward stdin to the container unless `-i` is given**. Without it the interpreter reaches EOF immediately, runs an empty program, and **exits 0** - so `set -e` does not fire, SSM reports `Success`, the whole thing returns in a couple of seconds, and not one job has run. The same applies to any `docker exec ... sh -s` or `python -` elsewhere in this document.

**Silence is not a pass.** That failure mode is indistinguishable from success unless the expected output is stated in advance, so state it: the run is only valid when all three of these appear.

| Marker | Proves |
|-------------------------|-----------------------|
| `container: ecs-<ENV>-background-...` | The container lookup resolved. An empty value here means the block ran against nothing |
| **Five** `== <JobName>` lines | The interpreter received its program |
| `FAILED: none` on the last line | Every job was attempted and none raised |

Anything less means the block **did not run**, not that it passed. A correct run takes minutes, not seconds, because `ExpirationLifecycleJob` performs a day of expiry processing.

**Each job is caught individually, and the last line is the verdict.** Without the `try`, the first job to raise ends the loop and the remaining four are never attempted - and the second in the list is the premium sweep, whose query is the one already known to be fragile. A run that stops early looks much like a run that passed: several jobs printed their names and no error was reported for them, because they never executed. This is the same defect as a failing SQL block discarding the readings that preceded it, which is why the pattern is spelled out rather than left to judgement.

#### Criterion 5 — the log queries

**Two things about the window and the filters have to be settled first, or the results cannot be read.**

**1. Choose the window from the apply, not from the clock.** On an environment with a nightly stop, a
24-hour window always contains a large band of connection failures - the scheduler deletes the instance
while the Lambdas keep running on their cadences, so `9501 Timed-out waiting to acquire database
connection` repeats for as long as the environment is down. That band is expected, predates the upgrade,
and is not evidence of anything. Anchor the window **after the apply settled** instead:

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

Production has no nightly stop, so its logs carry no such band - which cuts both ways. **A `9501` in
production's window is a finding, where the same line on a scheduled environment usually is not.**

**2. Numeric error codes are unusable as filter terms.** CloudWatch matches a quoted string as a
*substring*, and MySQL's error numbers are short, so they hit connection ids, ports, request ids and
durations. Both were observed: `1290` matched `clientConnection=1290121671` and port `12903`, and `3065`
matched port `30654` in a `/health` access line. Filter on the exception class names instead - they are
distinctive and they caught the real errors exactly.

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

**The Lambda handlers need their own query, and it is not optional.** A handler can catch a SQL exception, log it, and still return `StatusCode: 200` with a success-shaped body - so criterion 4's verdict does not cover this, and the ECS log groups never see it because these run in Lambda:

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

**Count, do not print, when the question is "any left?".** Piping messages through `head` shows the
*oldest* matches, because the result is time-ascending - the wrong end of the list for a recency question,
and it silently truncates. `length(events)` cannot mislead that way.

```bash
aws logs filter-log-events --log-group-name "/ecs/${ENV}-background-optinist-cloud-taskdef" \
  --start-time "$SINCE" \
  --filter-pattern '?storage_tracking ?premium_expiration ?StorageReconciliation ?DataCleanup ?ExpirationLifecycle' \
  --query 'events[].[timestamp,message]' --output text | ts_fmt | tail -30
# Expected: NOT empty, with entries after the apply. This is separate evidence from
# criterion 4: it shows the container's own scheduler running these on the new
# version unattended, rather than only responding to a manual invoke.
```

**Read that last one for what each job actually did, not just that it ran.** `ExpirationLifecycleJob`
logs `No remote bucket configured, skipping expiration lifecycle` where no remote bucket is set, so it
completes without touching the database. It counts as invoked, not as exercised - and criterion 4's `ok`
for it means only that it returned.

Do not read the `general` and `slowquery` exports as a signal either way: both logs are at the engine default of off, so they produce nothing before or after an upgrade.

#### Criterion 6 — the production baseline

Capture it for **production** before phase 4. The `Average` figures remain queryable for months, but the
`Maximum` ones do not - see the note at the end of this section.

Every command here is read-only, so the step carries no destructive risk. It carries three others, and all three are about reading or keeping the *wrong thing* rather than about breaking something:

1. **The wrong environment, silently.** This is the only step in phase 3 that reads production, while every variable in the shell points at development. A capture against `$DB` succeeds, returns fourteen days of development metrics, and looks exactly like a correct result - and it cannot be retaken after phase 4. **Use `PROD_DB`; do not reassign `$DB` or `$ENV`.**
2. **The shell keeping a production context.** Phase 0 makes no production API call, so this is the first one in the whole upgrade. **Pass `--profile` inline and never `export AWS_PROFILE`**: a `terraform` command inherited into a production-pointing shell is the failure this avoids.
3. **Losing the capture.** It is the one irreversible reading in the procedure, so it is written to a file rather than to the terminal.

```bash
# The only hand-substitution point in this step. PROD_* deliberately, so nothing
# else in the session is repointed.
PROD_DB=<production db instance identifier>
PROD_PROFILE=<production profile name>
OUT=$HOME/baseline-production-$(date +%Y%m%d-%H%M).txt
```

**Why `$HOME` here, when saved plans use `mktemp`.** The two have opposite requirements. A plan file must
live outside the git worktree, because `ecr_build_push.sh` refuses to build a dirty tree, and it is
consumed by the apply that follows it - `mktemp` is right because the file is transient and 0600 suits a
file holding every variable value. This capture is the reverse: it has to survive until phase 5's
comparison, weeks later. `$TMPDIR` is a per-session directory subject to periodic cleanup and cleared on
reboot, so `mktemp` would put a reading that cannot be retaken somewhere it may disappear before it is
used. A gitignored path inside the repository works equally well, and is invisible to the clean-tree
guard because `git status --porcelain` does not list ignored files.

```bash
# A gate in the same spirit as assert_rehearsal: refuse to capture unless this
# really is production and really is still on the outgoing version.
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

`WriteLatency` is in the list alongside `ReadLatency` because edge case 6's second mechanism is background flushing driven by `innodb_io_capacity`, which shows on the write side.

**`BufferCacheHitRatio` returns no datapoints on a standard RDS MySQL instance** - measured, not predicted. It is an Aurora metric and is not published to `AWS/RDS` here, so its section of the capture comes back empty. That is the expected result, and it is why the loop carries `ReadIOPS`, `ReadLatency` and `FreeableMemory`: those hold the observable consequences of a buffer pool change, and they are the metrics to compare in edge case 6's place. In practice they are better instruments anyway - on a cache-served workload `ReadIOPS` is flat enough that a sizing change stands out against it immediately. And once phase 1 pins the pool, edge case 6's concern is closed by the pin regardless of what is measurable.

When posting this capture, redact the instance identifier and **keep the metric values** - the acceptance criteria are written against them.

### Phase 4: apply to production

No scheduler, so the timing is free, but the instance is offline for the whole upgrade and the same apply rolls every ECS service (B6). Announce using the measured upgrade duration plus the rollout time — 0B's figure if it ran, otherwise 0A's plus a margin.

**If 0B was skipped, the precheck runs here for the first time.** It fails safe: a precheck failure
means the upgrade does not complete and the instance stays on `<FROM>`, so the cost is the window rather
than the data. Decide in advance whether that outcome means reschedule or investigate-in-place, so the
decision is not made under time pressure inside the window.

**Run phase 1 steps 1 to 14, with `ENV` set to the production value.** That sequence is numbered so this
phase can cite it rather than paraphrase it. **Four differences** — three in the steps, and one in the file
names that is easy to miss because development hides it:

| | On production |
|-------------------------|-----------------------|
| **`TF_ENV`, not `ENV`, names the backend and tfvars files** | `$ENV` is `subscr` while the files are `backends/production.hcl` and `environments/production.tfvars`. **The two values coincide on development**, so `backends/${ENV}.hcl` works there — and on production it names a file that does not exist, failing at the **first** terraform command of the window. Set `TF_ENV` in the setup block and use it for those two paths only |

**Check the scheduler's absence rather than assuming it.** The next two rows skip steps because production has
no scheduler — but that is **not a property of the environment.** Every resource in `dev_schedule.tf` is gated
by `count = var.enable_dev_schedule ? 1 : 0`; the variable defaults to `false` and **neither tfvars example
sets it either way**, so nothing in the repository settles it. Two steps are skipped unrecoverably on the
answer, and the check costs nothing and needs no AWS call:

```bash
grep -n enable_dev_schedule "environments/${TF_ENV}.tfvars"
# Expected: no match, or "= false". EITHER means the scheduler is absent and the two
# steps below are correctly skipped.
# A "= true" means production DOES have a <ENV>-dev-scheduler, and then phase 1 steps 2
# and 13 must be RUN, not skipped.
```

The three step differences:

| Phase 1 step | On production |
|-------------------------|-----------------------|
| **Step 1** — the `<FROM>` readings and the pins | **Carries more weight here.** On development the "before" half came from phase 0A step 9; **production has no phase 0A**, so step 1 is the only place it can be taken, and after the apply the `<FROM>` instance is gone |
| **Step 2** — the stop schedule and the override | **Skipped.** Production has no scheduler, so there is no deadline and no override to set |
| **Step 13** — the scheduler Lambda's parameter group | **Skipped**, for the same reason — and the blast radius is *smaller* here than on development. Both non-instance consumers of the generated name, the Lambda's `RDS_PARAMETER_GROUP_NAME` and the IAM policy's `pg:` ARN, live in `dev_schedule.tf` under the same `count`. **Where the scheduler is absent, `aws_db_instance.main` is the sole consumer**, and the apply covers it |

Everything else is identical. Two of the steps are restated in full below rather than left to the
reference, because this is the environment where skipping them is unrecoverable.

#### The day before the window — rehearse every read-only check

**A command that has been executed carries a fraction of the defect rate of one that has only been written.**
That is the measured pattern across this work: phase 0A found nine defects on first execution, phase 1 found
one - and the difference was not better writing, it was that phase 0C had already run the same plan gate.
Commands reviewed but never run have kept failing at close to the first-execution rate.

So **run every read-only check against production the day before**, and spend the window on the three things
that actually change something. Nothing below modifies anything.

**A rehearsal proves the command, not the state.** Instance status, service counts and alarm states all move,
so **a rehearsed check still has to be run inside the window** - rehearsing it does not discharge it. Only the
pre-window items complete early.

| Rehearse | What the rehearsal settles |
|-------------------------|-----------------------|
| Phase 1 steps 1, 3 and 6 | Every resource name resolves, and the queries return the shape the assertions expect |
| The ECS and alarm checks below | The four service names and the three alarm names are right, on an environment nobody has queried in this work |
| The proxy reach check below | `ssm_sh` reaches production, and the in-VPC path exists there at all |
| **The health lane** | **A complete `e2e/.env.prod`**, which does not exist yet and which the config refuses to start without, the case count, and a green baseline on `<FROM>` - so a failure afterwards cannot be mistaken for one the upgrade caused |
| The certificate check below | That it will not fail the health lane for an unrelated reason |

**Three things cannot be rehearsed, and they are the whole window**: the snapshot, the parameter-group copy
and the apply. The first two are additive and could be done early, but then **re-verify them at apply time** -
a copy taken a week before is stale if anyone touched the live group since.

```bash
# ORIGIN is a hand-substitution point: this document never sets BASE_URL, and the health
# lane's target file is where the value belongs. Assign it here rather than assuming it.
ORIGIN=<the production https origin, e.g. https://www.example.com>
HOST=$(printf '%s' "$ORIGIN" | sed -E 's#^https?://##; s#[:/].*##')
[ -n "$HOST" ] || { echo "ABORT: ORIGIN did not yield a host" >&2; }
echo "host=$HOST"
```

```bash
# The TLS certificate, which the health lane fails below 14 days. Read it now: an
# expiring certificate discovered after the upgrade reads as an upgrade problem.
# stderr is kept deliberately - see below.
echo | openssl s_client -servername "$HOST" -connect "$HOST":443 \
  | openssl x509 -noout -enddate
# Expected: notAfter more than 14 days out. This is HEALTH-19's own check, run early.
```

**`stderr` is not discarded here, and the host is checked before use.** With `2>/dev/null` and an unset
origin, `openssl` was handed an empty host and answered
`Could not find certificate from <stdin>` — which reads as a certificate fault. **That is the exact
misattribution this step exists to prevent**, arriving from the step itself. The verdict also comes through a
pipe, so `openssl x509`'s status is what survives, not `s_client`'s: read the output rather than the exit
code.

```bash
# The application's own path: the proxy endpoint, reached from inside the VPC. On
# development this was inferred from the ECS services coming up, because cloud-startup.sh
# verifies the connection and runs alembic before the container reports healthy. Here it
# is checked directly, because production's window is not the place to be inferring.
PROXY=$(aws rds describe-db-proxies --db-proxy-name "${ENV}-optinist-rds-proxy"   --query 'DBProxies[0].Endpoint' --output text)
echo "$PROXY"
# Then run phase 0A step 8's SQL block with "$PROXY" in place of CLONE_HOST.
# Expected before the window: <FROM>.x through the proxy. After the apply: <TO>.x.
# A failure here while the instance itself is available is a proxy problem, not an
# engine one - and on production the proxy target is not re-registered automatically.
```

**Use the suite's own target mechanism rather than exporting variables by hand.** `frontend/e2e/.env.prod`
carries the whole environment — `frontend/e2e/.env.prod.example` lists the keys — and
`frontend/playwright.config.ts` **hard-fails at startup** if that file is missing a required one. Hand-rolled
exports get none of that, and the target file's values **override the shell**, so an exported
`RDS_PROXY_HOST` would be silently ignored anyway.

```bash
# Confirm the target file exists and is complete BEFORE the window. The config refuses
# to start on an incomplete one, which is the guard worth having.
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
( cd frontend && E2E_TARGET=prod yarn test:e2e e2e/17-aws-health.spec.ts --retries 0 )
```

Four things about that invocation, each of which was wrong in an earlier draft of this document:

- **`HEALTH_ENV` is the variable, not `TARGET_ENV`.** `frontend/e2e/helpers.ts` has
  `export const TARGET_ENV = process.env.HEALTH_ENV || "development"` — `TARGET_ENV` is a TypeScript constant
  and `process.env.TARGET_ENV` is read nowhere. Setting it is **inert**, and the failure is silent and
  asymmetric: the lane grades **development's** ECS, ALB, alarms and log groups while the `RDS_*` values still
  point SQL at **production**, and the off-development guard `if (ENV !== "development")` never fires. A green
  run would be filed as production's baseline having checked nothing of production's compute layer.
- **Off development the lane requires four variables, plus `BASE_URL`** — `RDS_PROXY_HOST`,
  `RDS_SSM_INSTANCE_NAME`, `RDS_SECRET_ID` and **`STRIPE_SECRET_ENV`**. They are `expect()`s, not skips, so a
  missing one **fails all 29 cases**. `BASE_URL` unset is worse: it defaults to localhost, which **skips** all
  29.
- **`--retries 0`**, because the config retries once by default, which doubles the AWS reads and the HTTP load
  this puts on production and muddies the baseline. And **not `--headed`**: no case in this lane drives a
  browser, so the only window it opens is the login, and it breaks outright on a headless host.
- **`E2E_FAIL_ON_SKIP=1` is deliberately absent here.** On development it is the right flag. On production
  **`HEALTH-27` skips whenever the ALB alarm has no retained ALARM transition** — its own comment notes
  development produces one on every weekday start-up, so a healthy production plausibly has none — and
  `HEALTH-29` skips with no unpublished experiment. Both would turn the rehearsal red for nothing. **Read the
  reporter's skip list and account for each entry** instead of trusting the exit code.

**Expect 29 collected cases.** `HEALTH-19` skips on development, whose ALB is plain HTTP on `:8080`, and
executes here. Read that difference off the **skip-summary reporter's `N executed` line**, not off Playwright's
own total — Playwright reports 29 tests either way.

**This lane is read-only against the database but not inert against the environment.** Every SQL call goes
through a read-only assertion, and nothing calls `update-service` or `set-alarm-state`. But `HEALTH-25` POSTs
a duplicate registration to production and asserts it is refused, `HEALTH-26` fires 20 concurrent requests at
the public tier, and `globalSetup` performs a real login. None of it writes; all of it is traffic.

**Record the rehearsal's results.** The point of the green baseline is that it is on file: after the apply, a
failure is either new or it is not, and that distinction is only available if the before state was written
down.

```bash
# 1. Manual pre-upgrade snapshot. This is the production rollback point, and the only
#    recovery point that survives an instance delete. Take it and confirm it, every time.
#    PRESNAP, not SNAP. This snapshot is a recovery point that must outlive the phase,
#    whereas SNAP elsewhere in this document names a restore *source* that gets deleted -
#    phase 0B ends with drop_snapshot "$SNAP". Two meanings in one shell session is how a
#    recovery point gets deleted by a line copied from another phase.
PRESNAP=${ENV}-pre-upgrade-$(date +%Y%m%d-%H%M)
aws rds create-db-snapshot --db-instance-identifier "$DB" --db-snapshot-identifier "$PRESNAP"
aws rds wait db-snapshot-available --db-snapshot-identifier "$PRESNAP"
aws rds describe-db-snapshots --db-snapshot-identifier "$PRESNAP" \
  --query 'DBSnapshots[0].{id:DBSnapshotIdentifier,status:Status,ev:EngineVersion,
           size:AllocatedStorage,created:SnapshotCreateTime}'
# Expected: available, <FROM>.x. A snapshot still being created is not a recovery point.

# 2. Retain an outgoing-family parameter group, so a rollback has a group to attach.
#    The SOURCE name is the OUTGOING one, and it only exists before the first apply:
#    afterwards name_prefix has replaced it with a generated name (B1). So this command
#    is resolvable exactly once per environment. If the copy was already made, do NOT
#    re-run it - copy-db-parameter-group is not idempotent and returns
#    DBParameterGroupAlreadyExistsFault. Verify it instead, with the two checks below.
aws rds copy-db-parameter-group \
  --source-db-parameter-group-identifier "${ENV}-optinist-ssl" \
  --target-db-parameter-group-identifier "${ENV}-optinist-ssl-rollback" \
  --target-db-parameter-group-description "outgoing-family rollback target"

# 2b. Verify the copy. This is phase 1 step 5's second half, restated because it is the
#     half most easily dropped - and because a copy that silently did not happen is
#     otherwise discovered during the restore, after the restore has been committed.
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

# 3. Switch the backend. Forgetting this corrupts the other environment's state.
terraform init -backend-config="backends/${TF_ENV}.hcl" -reconfigure
terraform workspace show
terraform state list | grep aws_db_instance
# Expected: exactly one line, and it is production's instance. NOT grep -c: a count
# cannot show WHICH instance, which is the half of this check that guards against
# applying production's plan to the other environment's state. The pipe also discards
# terraform's own exit status, so a locked or unconfigured backend prints nothing rather
# than failing - an empty result here is a backend problem, not an empty state.
```

**Step 1 completes before the window opens**, not inside it - it is the `<FROM>` reading that cannot be taken
afterwards, and it changes nothing, so there is no reason to spend window time on it.

Then **phase 1 steps 3 to 12** in order: the instance is `available`; the snapshot; the retained parameter
group **and its verification**; the recovery points are real; a clean worktree and a plan written outside it;
both gate assertions; the plan classified by root cause; the apply; the upgrade proved to have happened rather
than been queued; and the pins proved to be in effect.

**Step 5's verification is not optional and is easy to drop**, because the copy command is restated above
while the two `describe-*` checks are not. They are what catches a copy that lost `require_secure_transport` -
which restores an instance that refuses the application's TLS-only connections - and **a copy that silently
did not happen is otherwise discovered during the restore, after the restore has been committed.**
`copy-db-parameter-group` is **not idempotent**, so if the copy was made early, *verify* it rather than
re-running the command, which returns `DBParameterGroupAlreadyExistsFault`.

After the apply, in addition to **phase 1 step 14** — the proxy target, which matters more here because
production does not get the scheduler's automatic re-registration:

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
  --filter '{"Dimensions":{"Key":"USAGE_TYPE","Values":["APN1-ExtendedSupport:Yr1-Yr2:MySQL${FROM}"]}}' \
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
PLAN=$(mktemp -t tfplan-cleanup)
terraform plan -var-file="environments/${TF_ENV}.tfvars" -out="$PLAN"
terraform show -json "$PLAN" | jq -r '
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
| Manual pre-upgrade snapshot | Phase 1 step 4, phase 4 | `<FROM>` |
| Automatic pre-upgrade snapshots | RDS, up to two, when the retention period is greater than zero | `<FROM>` |
| Automated backup retention | Continuous | `<FROM>` up to the upgrade |
| Retained rollback parameter group | Phase 1 step 5, phase 4 | Outgoing family |

Verify a snapshot's engine version before relying on it.

**An automatic pre-upgrade snapshot survives the deletion of its instance, provided the delete passes `--no-delete-automated-backups`.** This is the property the whole rollback depends on and it is easy to assume rather than check, so it was measured in the phase 0A drill: the instance was deleted, the snapshot was still `available` on `<FROM>` afterwards, and the restore from it succeeded. **The flag defaults to true**, which means a delete written without it takes the restore source down with the instance - the recovery point disappears at exactly the moment it is needed.

Two further properties of the restore, both measured rather than assumed:

- **The endpoint hostname is unchanged, and `DbiResourceId` is not.** Restoring onto the original identifier reproduces the original endpoint, which is why nothing that resolves the hostname needs reconfiguring - but the resource id is new, and that is what forces the proxy target re-registration in step 3 below.
- **Each delete that retains its automated backups leaves its own set.** They accumulate under the same instance identifier, distinguished only by `DbiResourceId`, so a cleanup that handles one leaves the others billing. A rollback deletes the instance once and the eventual cleanup deletes it again, which means two.

**Metadata is not proof.** The control plane reporting the right engine version, endpoint and parameter group says nothing about whether the database serves. Connect to it, over TLS, and read the engine version from the engine. The drill does this and it is the check that distinguishes a rollback from a restore that merely looks right.

### Procedure

**This is the live procedure, and it is self-contained.** Phase 0A rehearses it on a throwaway clone;
nothing here requires reading that phase or substituting its variable names. Read this section only.

Five steps. Do not skip step 0 - a rollback that starts by deleting the evidence cannot be diagnosed.

```bash
# Run the session setup from the top of Procedure, with ENV set to the environment in
# trouble. It sets FROM and TO, which the checks below compare against.
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

### 3. The clone claims to be the live instance

**Problem:** RDS copies the source instance's tags onto a snapshot and back onto anything restored from
it, so a clone arrives carrying the live instance's `Name`, and `ManagedBy = terraform` while being in no
state file. A cost report grouped by `Name` attributes the rehearsal to the live instance, and a drift
audit finds a Terraform-tagged resource nothing manages.

**Solution:** retag it as soon as the restore is available. Nothing keys off these tags functionally, so
this is about not misleading a later reader.

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
- Assert the retention period in every phase 2 cycle check, not just once.
- It does not decay gradually - if the chain loses it, it reads zero at the first cycle, which is why two cycles settle the question.

### 6. The database is slower after the upgrade

**Problem:** A major version changes InnoDB memory and IO defaults, and **a family diff does not show most of them.** Two distinct mechanisms produce the same symptom:

1. **RDS stops pinning a value and the engine derives it instead.** `innodb_buffer_pool_size` is the case that matters: a family pinning a fraction of instance memory gives way to one where `innodb_dedicated_server` derives the size, and the pool can end up smaller. This mechanism *is* visible in a family diff.
2. **RDS pins the value in neither family, and the engine default changed underneath.** Both sides read as unset, so a family diff shows nothing at all - and this is the larger group. Between MySQL 8.0 and 8.4 it covered `innodb_io_capacity`, `innodb_io_capacity_max`, `innodb_adaptive_hash_index`, `innodb_change_buffering` and `innodb_buffer_pool_instances`.

**A value that looks derived from another may not be.** `innodb_buffer_pool_instances` is the trap: the engine forces it to 1 below a 1 GiB pool, so when the pool halved and the instance count fell with it, the obvious reading was that pinning the pool back would bring the count back too. It did not - both groups report it as an unset engine default, and the default itself changed. Pinning one value is not evidence about another, however tightly the documentation couples them. Read every moved value again after the pins are applied, which is what step 11 is for.

**Solution:**
- The symptom is slower, not wrong: `BufferCacheHitRatio` falling, `ReadIOPS` or `WriteIOPS` rising, read latency up. Not an error.
- **Do not attribute by diffing family defaults.** Mechanism 2 is undetectable that way. Attribute by comparing step 8's readings against step 9's - the same running server, before and after.
- `innodb_io_capacity` deserves a decision rather than observation. A large increase tells InnoDB's page cleaner it has IO headroom the volume may not have, so the upgrade doubles as an IO tuning change. Pinning it and `innodb_io_capacity_max` to the outgoing values in the new parameter group separates the two changes; tune deliberately afterwards, as its own piece of work.
- Pin `innodb_buffer_pool_size` explicitly in the new group if the step 8 / step 9 pair shows the pool shrinking, and pin `innodb_buffer_pool_instances` alongside it rather than expecting it to follow. Note the coupling this introduces: the engine requires the pool to be a multiple of `innodb_buffer_pool_chunk_size` times the instance count, so a pinned instance count imposes that granularity on whatever the pool expression evaluates to.
- Compare against the phase 3 baseline. A metric that moved with **no** corresponding difference in the step 8 / step 9 pair is the one to triage as an application question.

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

| Metric | Why | Expected after upgrade |
|-------------------------|-----------------------|-----------------------|
| `BufferCacheHitRatio` | Buffer pool sizing | May fall - see edge case 6 |
| `ReadIOPS` | Corroborates the above | May rise with a smaller buffer pool |
| `WriteIOPS` | Background flushing, if `innodb_io_capacity` rose - edge case 6, mechanism 2 | May rise independently of any application change |
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
| 5 minutes | `free-manager` - the fastest cadence |
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

Only the deployed e2e lanes reach the upgraded instance, and they reach it through the RDS Proxy - the same path every ECS task uses for `MYSQL_SERVER`. That makes them evidence for the proxy authentication chain as well as for the queries.

| Lane | What it covers on a deployed environment |
|-------------------------|-----------------------|
| `17-aws-health` | Read-only. Instance availability, alarms, encrypted connection, and real SQL reads through the proxy. Can be pointed at production |
| `15-premium-aws` | Advisory lock calls, through the premium assignment path |
| `16-storage-aws` | Storage aggregation against the deployed database |
| Browser specs | The application paths, with skips promoted to failures |

Three properties of the suite to know before running it:

- Two lanes are localhost-only by design and cannot reach a deployed environment.
- One subscription lane writes to the shared development database without an opt-in flag. Decide deliberately whether a soak includes it.
- The disruptive lane refuses to run when another account has been active recently, and refuses when it cannot tell. That refusal is usually the correct outcome on a shared environment.

### Choosing the lanes, and substituting the release set for the expensive two

The two AWS-facing lanes beyond the health lane are **expensive** - together they run well over an hour. Before spending that, establish what they uniquely add, because the answer depends on what the other criteria have already exercised rather than on the lanes' names.

**Enumerate the hand-written SQL first.** An engine version change is most likely to surface in SQL written by hand, because the ORM normalises the rest. **Enumerate it in two places, not one** - the application and the Lambda packages are separate codebases with separate database access styles, and searching only for the ORM's `text(...)` wrapper misses the second entirely:

| Hand-written SQL | Where | Already exercised against `<TO>` by |
|-------------------------|-----------------------|-----------------------|
| `GET_LOCK` / `RELEASE_LOCK` | `workspace_data_capacity_services.py` | The premium path, which criterion 4 invokes |
| `GET_LOCK` / `RELEASE_LOCK` | `startup_leader.py` | **Phase 1's own apply.** `__main_unit__.py` calls it from the startup hook on the public tier, so every deploy runs it |
| `GET_LOCK` / `RELEASE_LOCK` | `storage_tracking.py` | Criterion 4 invokes the storage tracking job directly |
| `CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP` server defaults | `models/base.py`, `experiment.py`, the alembic versions | **Phase 1's own apply.** `cloud-startup.sh` runs `alembic upgrade head` and exits non-zero on failure, so a service that is up is a service whose DDL applied |
| **Roughly 120 raw `execute()` statements** | **The Lambda packages** - `premium_manager` (63, plus 5 in its utils), `premium_cleanup` (21), `free_cleanup` (15), `common_user_manager` (6), `cost_tracker` (5), `free_user_utils` (5). They use the driver directly rather than the ORM | **Criterion 4 alone.** Nothing else reaches them |

Two conclusions, and the second is the one that matters:

1. **The advisory lock mechanism is not confined to one lane.** It appears three times, and the site that runs on every deploy is the startup leader election, not the premium path.
2. **The Lambda packages hold by far the largest hand-written SQL surface in the repository, and criterion 4 is the only thing that executes it.** That makes criterion 4 the most valuable application-level check in this phase rather than a formality.

So the expensive lanes are largely redundant with criterion 4, which reaches more SQL than they do. The residual risk in aggregation SQL is `GROUP BY` strictness, and that is closed by measurement rather than by testing: step 8 records `sql_mode`, and a value without `ONLY_FULL_GROUP_BY` cannot start rejecting a query the outgoing version accepted.

**So an environment's own standard release e2e set, plus criterion 4, is an acceptable substitute for the two expensive lanes - but the substitution is conditional on criterion 4 being run in full.** Dropping invokes and substituting the lanes at the same time would leave that 120-statement surface untested. Run the health lane regardless; it is read-only and cheap. Record the substitution and this reasoning in the phase log, so a later reader sees a decision rather than an omission.

**One gap the substitution leaves, and it is cheap to close.** The startup leader election is wrapped in a `try/except` that logs and returns, so **a service reaching `running == desired` does not prove its `GET_LOCK` succeeded.** The evidence is in the log group, not in the service state:

```bash
aws logs filter-log-events --log-group-name "$PUBLIC_LOG_GROUP" \
  --start-time $(( ($(date +%s) - 86400) * 1000 )) \
  --filter-pattern '?"Startup sync" ?"Startup sync error"' \
  --query 'events[].message' --output text | tail -20
```

Expected: no `Startup sync error`, and at least one line showing the election resolved - either this task won and ran the sync, or it deferred to the leader. **An empty result is not a pass**: it means nothing was exercised, in which case the lock was never taken and this check has told you nothing.

### The engine-version assertion

Nothing in the health lane checked the parameter group's apply status or `PendingModifiedValues` before this procedure existed, which meant the B2 failure state - instance on the old version with an upgrade queued - was invisible to the suite. The assertion added for it belongs in `frontend/e2e/17-aws-health.spec.ts` beside the other storage and database cases, and checks:

- `PendingModifiedValues` is empty
- the parameter group is `in-sync`, because holding the right values is not the same as having them in effect
- the version the proxy serves agrees with the version the control plane reports, which is the split-brain B2 leaves behind
- `sql_mode` is unchanged

**Write it so it names no engine version.** The first draft asserted the version against a hard-coded major and the parameter group against a hard-coded family, and both had to be removed:

- **A constant that must be edited in the same commit as the `engine_version` attribute cannot fail during the upgrade it claims to guard.** The two edits ship together. What such an assertion detects is a sequencing slip, and "merged but never applied" is already caught by the plan gate, more directly.
- **RDS rejects a parameter group whose family does not match the engine**, so asserting the family asserts an AWS invariant rather than anything about this change.

Dropping both leaves every remaining assertion comparing two live readings against each other, which is what makes the case meaningful *continuously* rather than only during an upgrade - and it needs no edit at the next major version. `sql_mode` stays as a constant because it is not a version: an upgrade does not move it, so it never needs editing.

**The generalisable rule: the problem is not a constant in a test, it is a constant that must be edited as part of the change the test verifies.**

It deliberately does not assert a buffer pool size: that value is derived from instance memory and is expected to change across a family move, so pinning a number would turn a documented consequence into a failing test.

**This case is regression cover, not verification of the upgrade in hand.** Every assertion in it also appears as a manual step in the phase 4 checklist, and phase 1 measures them on the development instance, so it can be written after the production apply without weakening any phase. Order it behind the release-critical criteria.

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
