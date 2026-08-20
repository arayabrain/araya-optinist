#!/usr/bin/env bash
# Release health probes from release sheet "11 AWS Monitoring" (BT-1101..1111).
# Read-only. Deterministic rows print PASS/FAIL; rows the sheet leaves to
# human judgement print REVIEW with their data and never fail the run.
# BT-1109 is covered by the opt-in e2e lane (15-premium-aws.spec.ts).
#
# Usage:
#   ENV=development APP_URL=https://<frontend-host> make release_health_check
#   ENV=subscr make release_health_check
set -u

ENV="${ENV:-development}"
REGION="ap-northeast-1"
if [ -z "${APP_URL:-}" ] && [ "$ENV" = "subscr" ]; then
  APP_URL="https://www.araya-optinist.com"
fi

CLUSTER="${ENV}-optinist-cloud-cluster"
FREE_SERVICE="${ENV}-optinist-cloud-service"
PREMIUM_SERVICE="${ENV}-premium-optinist-cloud-service"
FREE_TG="${ENV}-optinist-tg"
PUBLIC_TG="${ENV}-optinist-public-tg"
RDS_ID="${ENV}-optinist-cloud-rds"
APP_LOG_GROUP="/ecs/${ENV}-optinist-cloud-taskdef"
BG_LOG_GROUP="/ecs/${ENV}-background-optinist-cloud-taskdef"
METRIC_NS="OptiNiSt/BackgroundJobs/${ENV}"
BUCKET_PREFIX="${ENV}-optinist-user-"

FAILS=0
pass()   { printf 'PASS    %s: %s\n' "$1" "$2"; }
fail()   { printf 'FAIL    %s: %s\n' "$1" "$2"; FAILS=$((FAILS + 1)); }
review() { printf 'REVIEW  %s: %s\n' "$1" "$2"; }
skipped() { printf 'SKIP    %s: %s\n' "$1" "$2"; }
indent() { sed 's/^/          /'; }

NOW=$(date +%s)
echo "Release health check: ENV=$ENV REGION=$REGION APP_URL=${APP_URL:-<unset>}"
echo

# BT-1101: application entry point and TLS
if [ -n "${APP_URL:-}" ]; then
  host="${APP_URL#https://}"
  host="${host%%/*}"
  code=$(curl -sS -o /dev/null -w '%{http_code}' "$APP_URL") || code="000"
  if [ "$code" = "200" ]; then
    pass BT-1101 "$APP_URL answers HTTP 200"
  else
    fail BT-1101 "$APP_URL answered HTTP $code"
  fi
  if echo | openssl s_client -connect "$host:443" -servername "$host" 2>/dev/null \
      | openssl x509 -noout -checkend 0 >/dev/null 2>&1; then
    pass BT-1101 "TLS certificate for $host is valid"
  else
    fail BT-1101 "TLS certificate for $host is missing or expired"
  fi
else
  skipped BT-1101 "APP_URL not set (required for the curl and TLS probes on ENV=$ENV)"
fi

# BT-1102: both ECS services ACTIVE, desired == running, pending == 0
svc=$(aws ecs describe-services --cluster "$CLUSTER" \
  --services "$FREE_SERVICE" "$PREMIUM_SERVICE" --region "$REGION" \
  --query 'services[].[serviceName,status,desiredCount,runningCount,pendingCount]' \
  --output text 2>/dev/null)
if [ -z "$svc" ]; then
  fail BT-1102 "describe-services returned nothing for $FREE_SERVICE / $PREMIUM_SERVICE"
else
  svc_ok=1
  # A deleted or renamed service lands under `failures`, not `services`
  rows=$(printf '%s\n' "$svc" | wc -l | tr -d ' ')
  if [ "$rows" != "2" ]; then
    fail BT-1102 "describe-services resolved $rows of 2 services"
    svc_ok=0
  fi
  while read -r name status desired running pending; do
    if [ "$status" != "ACTIVE" ] || [ "$desired" != "$running" ] || [ "$pending" != "0" ]; then
      fail BT-1102 "$name status=$status desired=$desired running=$running pending=$pending"
      svc_ok=0
    fi
  done <<< "$svc"
  if [ "$svc_ok" = 1 ]; then
    pass BT-1102 "both services ACTIVE with desired == running and pending == 0"
  fi
fi

# BT-1103: latest task healthy; stopped tasks are a judgement call
task=$(aws ecs list-tasks --cluster "$CLUSTER" --service-name "$FREE_SERVICE" \
  --region "$REGION" --query 'taskArns[0]' --output text 2>/dev/null)
if [ -z "$task" ] || [ "$task" = "None" ]; then
  fail BT-1103 "no running task on $FREE_SERVICE"
else
  read -r last health <<< "$(aws ecs describe-tasks --cluster "$CLUSTER" \
    --tasks "$task" --region "$REGION" \
    --query 'tasks[0].[lastStatus,healthStatus]' --output text)"
  if [ "$last" = "RUNNING" ] && [ "$health" = "HEALTHY" ]; then
    pass BT-1103 "latest task on $FREE_SERVICE is RUNNING and HEALTHY"
  elif [ "$last" = "RUNNING" ] && [ "$health" = "UNKNOWN" ]; then
    review BT-1103 "latest task RUNNING with health UNKNOWN (inside the container health check's 300s startPeriod right after a deploy - re-check shortly)"
  else
    fail BT-1103 "latest task on $FREE_SERVICE: lastStatus=$last healthStatus=$health"
  fi
  stopped=$(aws ecs list-tasks --cluster "$CLUSTER" --desired-status STOPPED \
    --region "$REGION" --query 'taskArns' --output text)
  review BT-1103 "recently stopped tasks (confirm no restart loop / OOM): ${stopped:-none}"
fi

# BT-1104: all targets healthy in both target groups
for tg in "$FREE_TG" "$PUBLIC_TG"; do
  arn=$(aws elbv2 describe-target-groups --names "$tg" --region "$REGION" \
    --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null)
  if [ -z "$arn" ] || [ "$arn" = "None" ]; then
    fail BT-1104 "target group $tg not found"
    continue
  fi
  states=$(aws elbv2 describe-target-health --target-group-arn "$arn" \
    --region "$REGION" --query 'TargetHealthDescriptions[].TargetHealth.State' \
    --output text)
  if [ -z "$states" ]; then
    fail BT-1104 "$tg has no registered targets"
  elif echo "$states" | tr '\t' '\n' | grep -qv '^healthy$'; then
    fail BT-1104 "$tg target states: $states"
  else
    pass BT-1104 "$tg targets all healthy"
  fi
done

# BT-1105: RDS available and CPU sane over the last hour
status=$(aws rds describe-db-instances --db-instance-identifier "$RDS_ID" \
  --region "$REGION" --query 'DBInstances[0].DBInstanceStatus' \
  --output text 2>/dev/null)
if [ "$status" = "available" ]; then
  pass BT-1105 "RDS $RDS_ID is available"
else
  fail BT-1105 "RDS $RDS_ID status: ${status:-not found}"
fi
cpu=$(aws cloudwatch get-metric-statistics --namespace AWS/RDS \
  --metric-name CPUUtilization \
  --dimensions Name=DBInstanceIdentifier,Value="$RDS_ID" \
  --statistics Average --period 300 \
  --start-time "$((NOW - 3600))" --end-time "$NOW" --region "$REGION" \
  --query 'Datapoints[].Average | max(@)' --output text)
if [ -z "$cpu" ] || [ "$cpu" = "None" ]; then
  review BT-1105 "no RDS CPU datapoints in the last hour"
elif awk "BEGIN{exit !($cpu < 80)}"; then
  pass BT-1105 "RDS CPU max 5-min average over 1h: ${cpu}%"
else
  fail BT-1105 "RDS CPU max 5-min average over 1h: ${cpu}% (>= 80%)"
fi

# BT-1106: nothing in ALARM state. A scale-in trigger (its only actions are
# autoscaling policies) sits in ALARM by design when the cluster idles, so it
# is a review row, not a failure.
alarms=$(aws cloudwatch describe-alarms --alarm-name-prefix "${ENV}-optinist-" \
  --state-value ALARM --region "$REGION" \
  --query "MetricAlarms[].[AlarmName, join(' ', AlarmActions)]" --output text)
if [ -z "$alarms" ] || [ "$alarms" = "None" ]; then
  pass BT-1106 "no alarm in ALARM state"
else
  alarm_ok=1
  while read -r name actions; do
    non_scaling=$(printf '%s' "$actions" | tr ' ' '\n' \
      | grep -cv '^arn:aws:autoscaling:.*:scalingPolicy:' || true)
    if [ "${non_scaling:-0}" -gt 0 ] || [ -z "$actions" ]; then
      fail BT-1106 "alarm in ALARM state: $name (actions: ${actions:-none})"
      alarm_ok=0
    else
      review BT-1106 "$name is in ALARM but only drives autoscaling scale-in (expected when idle)"
    fi
  done <<< "$alarms"
  if [ "$alarm_ok" = 1 ]; then
    pass BT-1106 "no alarm in ALARM state beyond idle scale-in triggers"
  fi
fi

# BT-1107: the sheet allows "expected-only" errors, so this is a review row.
# A failed CLI call must never read as "no errors".
if errors_raw=$(aws logs filter-log-events --log-group-name "$APP_LOG_GROUP" \
  --start-time "$(((NOW - 3600) * 1000))" --filter-pattern 'ERROR' \
  --region "$REGION" --query 'events[].message' --output text); then
  errors=$(printf '%s\n' "$errors_raw" | head -5)
  if [ -z "$errors" ] || [ "$errors" = "None" ]; then
    pass BT-1107 "no ERROR lines in $APP_LOG_GROUP over the last hour"
  else
    review BT-1107 "ERROR lines in the last hour (confirm expected-only); first lines:"
    printf '%s\n' "$errors" | indent
  fi
else
  fail BT-1107 "filter-log-events failed for $APP_LOG_GROUP"
fi

# BT-1108: background schedulers alive; metric listing is reference only
synced=$(aws cloudwatch get-metric-statistics --namespace "$METRIC_NS" \
  --metric-name ExperimentsSynced --statistics Sum --period 300 \
  --start-time "$((NOW - 900))" --end-time "$NOW" --region "$REGION" \
  --query 'length(Datapoints)' --output text)
if [ "${synced:-0}" -ge 1 ] 2>/dev/null; then
  pass BT-1108 "sync scheduler alive (ExperimentsSynced datapoints in 15 min: $synced)"
else
  fail BT-1108 "no ExperimentsSynced datapoint in 15 min (the 5-min sync scheduler looks down)"
fi
cleanup=$(aws logs filter-log-events --log-group-name "$BG_LOG_GROUP" \
  --start-time "$(((NOW - 7200) * 1000))" \
  --filter-pattern '"Starting data cleanup job"' --region "$REGION" \
  --query 'length(events)' --output text)
if [ "${cleanup:-0}" -ge 1 ] 2>/dev/null; then
  pass BT-1108 "cleanup scheduler alive ('Starting data cleanup job' lines in 2h: $cleanup)"
else
  fail BT-1108 "no 'Starting data cleanup job' line in 2h (the 60-min cleanup scheduler looks down)"
fi
metrics=$(aws cloudwatch list-metrics --namespace "$METRIC_NS" \
  --region "$REGION" --query 'Metrics[].MetricName' --output text)
review BT-1108 "published metric names (~2-week lookback, reference only; DataCleanupCount absent is NORMAL): ${metrics:-none}"
if echo "$metrics" | grep -q PersistentSyncFailure; then
  recent=$(aws cloudwatch get-metric-statistics --namespace "$METRIC_NS" \
    --metric-name PersistentSyncFailure --statistics Sum --period 300 \
    --start-time "$((NOW - 3600))" --end-time "$NOW" --region "$REGION" \
    --query 'length(Datapoints)' --output text)
  if [ "${recent:-0}" -ge 1 ] 2>/dev/null; then
    fail BT-1108 "PersistentSyncFailure has a datapoint in the last hour"
  else
    pass BT-1108 "PersistentSyncFailure is historical only (no datapoint in 1h)"
  fi
fi

# BT-1109: real assign/release evidence lives in the opt-in e2e lane
skipped BT-1109 "covered by the e2e lane's CloudWatch asserts (PREM-01 / PREM-02); run 15-premium-aws.spec.ts"

# BT-1110: public dataview open, protected dataview closed
if [ -n "${APP_URL:-}" ]; then
  pub=$(curl -sS -o /dev/null -w '%{http_code}' -H 'Accept: application/json' \
    "$APP_URL/api/public/dataview?limit=5") || pub="000"
  if [ "$pub" = "200" ]; then
    pass BT-1110 "/api/public/dataview answers 200 anonymously"
  else
    fail BT-1110 "/api/public/dataview answered $pub"
  fi
  bad=$(curl -sS -o /dev/null -w '%{http_code}' -H 'Accept: application/json' \
    -H 'Authorization: Bearer invalid-token' "$APP_URL/api/dataview?limit=5") || bad="000"
  case "$bad" in
    401|403) pass BT-1110 "/api/dataview rejects a bad token with $bad" ;;
    *) fail BT-1110 "/api/dataview with a bad token answered $bad" ;;
  esac
else
  skipped BT-1110 "APP_URL not set"
fi

# BT-1111: per-user buckets exist; layout spot-check is a review row
if ! buckets=$(aws s3api list-buckets \
  --query "Buckets[?starts_with(Name, '${BUCKET_PREFIX}')].Name" --output text); then
  fail BT-1111 "list-buckets failed"
elif [ -z "$buckets" ] || [ "$buckets" = "None" ]; then
  fail BT-1111 "no per-user buckets matching ${BUCKET_PREFIX}*"
else
  count=$(echo "$buckets" | wc -w | tr -d ' ')
  pass BT-1111 "$count per-user bucket(s) exist with prefix ${BUCKET_PREFIX}"
  first=$(printf '%s' "$buckets" | tr '\t' '\n' | head -1)
  sample=$(aws s3 ls "s3://$first/" --region "$REGION" 2>/dev/null | head -5)
  review BT-1111 "structure spot-check of $first (actual keys live under app/studio_data/{input,output}/{workspace_id}/):"
  printf '%s\n' "${sample:-<empty>}" | indent
fi

echo
if [ "$FAILS" -gt 0 ]; then
  echo "$FAILS check(s) FAILED"
  exit 1
fi
echo "All deterministic checks passed (REVIEW rows still need human eyes)"
