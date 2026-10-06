# Shell helpers for infrastructure/documentation/RDS_ENGINE_UPGRADE_PROCEDURE.md
#
# Source this file once per session, after the session setup block in that document:
#   source infrastructure/scripts/rds_upgrade_helpers.sh
# It defines functions only; nothing runs at source time except the SCHED constant.
# Works under bash and zsh. The procedure document states what each function's
# output must look like; this file states only what each one does.
#
# Reads these variables from the session: AWS_REGION, ENV, FROM, and for the
# production baseline PROD_DB and PROD_PROFILE.

# --- in-VPC command path --------------------------------------------------------

# Runs the script on stdin on the environment's background EC2 instance over SSM and
# prints its stdout. On failure prints the remote stdout and stderr and returns 1,
# because a block that died partway has already produced its diagnosis.
# The case statement carries no comment on purpose: under zsh with
# INTERACTIVE_COMMENTS off a comment there is a parse error when pasted.
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

# Aborts if any named variable is empty, and prints each one otherwise. Every ssm_sh
# heredoc is expanded by the LOCAL shell, so an empty variable surfaces as a remote
# error that reads as an SSM or secret fault; this names it locally, where the fix is.
need() {
  local v val missing=0
  for v in "$@"; do
    eval "val=\$$v"
    if [ -z "$val" ]; then echo "ABORT: $v is empty" >&2; missing=1
    else printf '%-12s = [%s]\n' "$v" "$val"; fi
  done
  return $missing
}

# --- delete guards ---------------------------------------------------------------

# Refuses anything that is not a rehearsal-scoped identifier. The name is the only thing
# standing between a typo and a deleted database, so assert on it rather than on care.
assert_rehearsal() {
  case "$1" in
    *rehearsal*) return 0 ;;
    *) echo "REFUSING: '$1' is not a rehearsal-scoped identifier" >&2; return 1 ;;
  esac
}

# Shows the target's real attributes and requires the identifier typed back: the
# predicate says "this could be a clone", the preview says which.
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

# Deletes a clone, keeping a final snapshot and the automated backups: the habit is
# what makes a mistyped identifier survivable rather than terminal.
drop_clone() {
  assert_rehearsal "$1" || return 1
  confirm_target instance "$1" || return 1
  aws rds delete-db-instance --db-instance-identifier "$1" \
    --final-db-snapshot-identifier "$1-final" --no-delete-automated-backups
  aws rds wait db-instance-deleted --db-instance-identifier "$1"
}

# Deletes a snapshot. There is no final-snapshot fallback for a snapshot, so an
# accepted-but-wrong delete here is unrecoverable; hence the same two guards.
drop_snapshot() {
  assert_rehearsal "$1" || return 1
  confirm_target snapshot "$1" || return 1
  aws rds delete-db-snapshot --db-snapshot-identifier "$1"
}

# Lists every rehearsal-scoped instance, manual snapshot and parameter group. Used
# before the work starts (the guards' accept set must be empty) and after each
# teardown (nothing may survive into phase 1).
list_rehearsal_artefacts() {
  aws rds describe-db-instances \
    --query 'DBInstances[?contains(DBInstanceIdentifier,`rehearsal`)].DBInstanceIdentifier' \
    --output text
  aws rds describe-db-snapshots --snapshot-type manual \
    --query 'DBSnapshots[?contains(DBSnapshotIdentifier,`rehearsal`)].DBSnapshotIdentifier' \
    --output text
  aws rds describe-db-parameter-groups \
    --query 'DBParameterGroups[?contains(DBParameterGroupName,`rehearsal`)].DBParameterGroupName' \
    --output text
}

# --- in-database readings --------------------------------------------------------

# Runs the SQL on stdin against the endpoint given as $1, over TLS, through the mariadb
# client on the background instance, with the application's credentials from Secrets
# Manager. The client aborts on the first SQL error, which under set -e discards the
# rest of the statements.
sql_run() {
  local host="$1" sql
  [ -n "$host" ] || { echo "usage: sql_run <endpoint> <<< 'SQL'" >&2; return 1; }
  need AWS_REGION ENV || return 1
  sql=$(cat)
  ssm_sh <<SH
set -e
CFG=\$(aws secretsmanager get-secret-value --region ${AWS_REGION} \
  --secret-id ${ENV}-optinist/database/config --query SecretString --output text)
export MYSQL_PWD=\$(printf '%s' "\$CFG" | python3 -c 'import json,sys;print(json.load(sys.stdin)["password"])')
DBU=\$(printf '%s' "\$CFG" | python3 -c 'import json,sys;print(json.load(sys.stdin)["username"])')
DBN=\$(printf '%s' "\$CFG" | python3 -c 'import json,sys;print(json.load(sys.stdin)["database"])')
mariadb --ssl -h ${host} -u "\$DBU" --connect-timeout=10 "\$DBN" -t <<'SQL'
${sql}
SQL
SH
}

# The fixed set of version, sql_mode, authentication and InnoDB readings against the
# endpoint given as $1. The same set is read on the clone, on the live instance before
# and after the upgrade, and through the proxy, so the readings are comparable. A
# variable missing on one engine version raises ERROR 1193 and discards the block, so
# drop that line for that side.
sql_readings() {
  local host="$1"
  [ -n "$host" ] || { echo "usage: sql_readings <endpoint>" >&2; return 1; }
  sql_run "$host" <<'SQL'
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
}

# Runs the application's own migration runner against the endpoint given as $1, from
# inside the background container, and aborts unless that endpoint reports the major
# version given as $2. A docker exec does not inherit the entrypoint's DB_* to MYSQL_*
# mapping, so it is repeated here from the container's own values; MYSQL_SERVER is
# overridden rather than DB_HOST, which has already been consumed. The version check
# exits non-zero itself, because a successful connection to the WRONG database is the
# case set -e cannot see, and the deployed configuration points at the live proxy.
alembic_probe() {
  local host="$1" major="$2"
  [ -n "$host" ] && [ -n "$major" ] || { echo "usage: alembic_probe <endpoint> <major version>" >&2; return 1; }
  ssm_sh <<SH
set -e
C=\$(docker ps --format '{{.Names}}' | grep -m1 -- -background-optinist-cloud-container)

docker exec -i -e MYSQL_SERVER=${host} -w /app "\$C" sh -s <<'INNER'
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
if not v.startswith('${major}.'):
    sys.exit('ABORT: expected ${major}.x, got ' + v + ' - this is not the intended target')
"

alembic current && alembic heads && alembic upgrade head && alembic current
INNER
SH
}

# Runs every in-process background job once, inside the background container, each
# caught individually so one raising does not end the loop. docker exec needs -i:
# without it python reads an empty program and exits 0, and SSM reports Success.
run_background_jobs() {
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
}

# --- Lambda invocation and logs ------------------------------------------------------

# Invokes the Lambda "${ENV}-$1" with payload $2 and prints one verdict line. Both
# conditions are required: a handler that raises still returns StatusCode 200 with
# FunctionError beside it, and a ResourceNotFoundException never reaches the handler so
# its error text carries no FunctionError at all.
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
# The envelope every EventBridge-scheduled invocation carries.
SCHED='"source":"aws.events","detail-type":"Scheduled Event"'

# Formats CloudWatch's epoch-millisecond timestamps on stdin as local time. Skips the
# blank line a message ending in a newline produces, which would otherwise print as 1970.
# 'ts_fmt' rather than 'fmt': fmt(1) is a real command.
ts_fmt() {
  while IFS=$'\t' read -r ts rest; do
    [ -n "$ts" ] || continue
    printf '%s  %s\n' "$(date -r $((ts/1000)) '+%Y-%m-%d %H:%M:%S')" "$rest"
  done
}

# --- production baseline ----------------------------------------------------------

# Refuses unless PROD_DB, read through PROD_PROFILE, is still on the outgoing major
# version FROM: the gate against capturing the wrong instance, or one already upgraded.
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

# Captures fourteen days of daily Average and Maximum for the eight baseline metrics of
# PROD_DB into the file given as $1, behind assert_prod so a failed gate creates no file.
capture_baseline() {
  local out="$1"
  [ -n "$out" ] || { echo "usage: capture_baseline <output file>" >&2; return 1; }
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
  } 2>&1 | tee "$out"
}

# --- log redaction -----------------------------------------------------------------

# Usage: <command> 2>&1 | redact
# Replaces the account id, environment prefix, endpoint tokens, instance and resource
# ids, instance class, private DNS names and UUIDs. Durations, engine versions, sql_mode
# values, precheck findings, metric values and cost figures are left intact: acceptance
# criteria are written against them. The rules are anchored on (^|[^[:alnum:]-]) because
# BSD sed -E has no \b - it does not error on one, it simply never matches.
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
