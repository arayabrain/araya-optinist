#!/usr/bin/env bash
# Weekly Maintenance Report Generator
# Runs all weekly checks from MAINTENANCE_PROCEDURES.md and produces a markdown report.
#
# Usage: ./weekly-maintenance.sh [output-dir]
#   output-dir: directory for the report file (default: current directory)

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REGION="ap-northeast-1"
CLUSTER="subscr-optinist-cloud-cluster"
SERVICES=(
  "subscr-optinist-cloud-service"
  "subscr-premium-optinist-cloud-service"
  "subscr-background-optinist-cloud-service"
)
LOG_GROUPS=(
  "/ecs/subscr-optinist-cloud-taskdef"
  "/ecs/subscr-premium-optinist-cloud-taskdef"
  "/ecs/subscr-background-optinist-cloud-taskdef"
  "/aws/lambda/subscr-premium-manager"
  "/aws/lambda/subscr-premium-cleanup"
  "/aws/lambda/subscr-free-manager"
  "/aws/lambda/subscr-free-cleanup"
  "/aws/lambda/subscr-common-user-manager"
  "/aws/lambda/subscr-cost-tracker"
)
LOG_LABELS=(
  "Free Tier"
  "Premium Tier"
  "Background Service"
  "Lambda: Premium Manager"
  "Lambda: Premium Cleanup"
  "Lambda: Free Manager"
  "Lambda: Free Cleanup"
  "Lambda: Common User Manager"
  "Lambda: Cost Tracker"
)
ERROR_SAMPLE_LIMIT=20  # max error lines to include per log group
ALB_NAME="subscr-optinist-lb"
ASG_NAME="subscr-optinist-asg"
RDS_INSTANCE="subscr-optinist-cloud-rds"
LAMBDA_FUNCTIONS=(
  "subscr-premium-manager"
  "subscr-free-manager"
  "subscr-common-user-manager"
  "subscr-premium-cleanup"
  "subscr-free-cleanup"
  "subscr-cost-tracker"
)
ASG_ALARM_PATTERN="subscr-optinist-cpu-high|subscr-optinist-cpu-low|subscr-optinist-memory-high|subscr-optinist-memory-low"

# ---------------------------------------------------------------------------
# Date helpers (portable macOS / Linux)
# ---------------------------------------------------------------------------
if date -v-1d +%s >/dev/null 2>&1; then
  epoch_days_ago()  { date -u -v-"${1}"d +%s; }
  iso_days_ago()    { date -u -v-"${1}"d +%Y-%m-%dT%H:%M:%SZ; }
  human_days_ago()  { date -u -v-"${1}"d +%Y-%m-%d; }
  iso_hours_ago()   { date -u -v-"${1}"H +%Y-%m-%dT%H:%M:%SZ; }
else
  epoch_days_ago()  { date -u -d "${1} days ago" +%s; }
  iso_days_ago()    { date -u -d "${1} days ago" +%Y-%m-%dT%H:%M:%SZ; }
  human_days_ago()  { date -u -d "${1} days ago" +%Y-%m-%d; }
  iso_hours_ago()   { date -u -d "${1} hours ago" +%Y-%m-%dT%H:%M:%SZ; }
fi

# ---------------------------------------------------------------------------
# Output setup
# ---------------------------------------------------------------------------
OUTPUT_DIR="${1:-.}"
mkdir -p "$OUTPUT_DIR"
REPORT="$OUTPUT_DIR/weekly-maintenance-$(date +%Y-%m-%d).md"
TODAY=$(date +%Y-%m-%d)
WEEK_START=$(human_days_ago 7)
START_EPOCH="$(epoch_days_ago 7)000"
START_ISO=$(iso_days_ago 7)
NOW_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)
NOW_EPOCH_S=$(date -u +%s)
START_EPOCH_S=$(epoch_days_ago 7)

# Temp dir for appendix data (log samples, alarm history, scaling activity)
APPENDIX_TMP=$(mktemp -d)
trap 'rm -rf "$APPENDIX_TMP"' EXIT

echo "Generating weekly maintenance report..."

# ---------------------------------------------------------------------------
# Helper: run a command and capture stdout+stderr; return output
# On AWS error: returns empty string, sets AWS_LAST_OK=false, stores error
# ---------------------------------------------------------------------------
AWS_LAST_OK=true
AWS_LAST_ERROR=""

run_aws() {
  local output
  AWS_LAST_OK=true
  AWS_LAST_ERROR=""
  output=$("$@" 2>&1) || true
  if echo "$output" | grep -qi "An error occurred"; then
    AWS_LAST_OK=false
    AWS_LAST_ERROR="$output"
    echo ""
  else
    echo "$output"
  fi
}

# ============================= REPORT START =================================
cat > "$REPORT" <<EOF
# Weekly Maintenance Report

| Field | Value |
|---|---|
| **Date** | $TODAY |
| **Period** | $WEEK_START to $TODAY |
| **Maintainer** | *(fill in)* |

---

EOF

# ---------------------------------------------------------------------------
# 1. Current Alarm Status
# ---------------------------------------------------------------------------
echo "  Checking alarm status..."
alarm_output=$(run_aws aws cloudwatch describe-alarms \
  --state-value ALARM \
  --region "$REGION" \
  --query 'MetricAlarms[*].[AlarmName,StateReason]' \
  --output table)

if [ "$AWS_LAST_OK" = false ]; then
  alarm_output="(Failed to retrieve alarm status)"
fi

alarm_count=0
asg_alarm_count=0
actionable_alarm_count=0
if echo "$alarm_output" | grep -q "subscr-"; then
  alarm_count=$(echo "$alarm_output" | grep -c "subscr-" || true)
  asg_alarm_count=$(echo "$alarm_output" | grep -cE "$ASG_ALARM_PATTERN" || true)
  actionable_alarm_count=$((alarm_count - asg_alarm_count))
fi

{
  echo "## 1. Current Alarm Status"
  echo ""
  if [ "$alarm_count" -eq 0 ]; then
    echo "**All alarms OK.**"
  else
    echo "**Actionable alarms: $actionable_alarm_count**"
    echo ""
    if [ "$actionable_alarm_count" -gt 0 ]; then
      echo '```'
      echo "$alarm_output" | grep -vE "$ASG_ALARM_PATTERN"
      echo '```'
    fi
    if [ "$asg_alarm_count" -gt 0 ]; then
      echo "> $asg_alarm_count ASG scaling alarm(s) also active — expected autoscaling behavior (see Section 5)."
    fi
  fi
  echo ""
} >> "$REPORT"

# ---------------------------------------------------------------------------
# 2. CloudWatch Log Review (Errors)
# ---------------------------------------------------------------------------
echo "  Reviewing CloudWatch logs for errors and warnings..."
total_errors=0
declare -a LOG_COUNTS=()

for i in "${!LOG_GROUPS[@]}"; do
  label="${LOG_LABELS[$i]}"
  group="${LOG_GROUPS[$i]}"
  echo "    $label..."

  # Use CloudWatch Logs Insights to match error/warning entries.
  # Catches: Python logging (ERROR:/WARNING:), Lambda runtime ([ERROR]),
  # Python tracebacks, and Lambda timeouts.
  # The parse+ispresent handles per-line filtering for ECS multi-line events;
  # the like clauses catch Lambda-specific formats that don't use Python logging.
  query_id=$(run_aws aws logs start-query \
    --log-group-name "$group" \
    --start-time "$START_EPOCH_S" \
    --end-time "$NOW_EPOCH_S" \
    --query-string "fields @timestamp, @message | parse @message / (?<level>ERROR|WARNING):/ | filter ispresent(level) or @message like /\[ERROR\]/ or @message like /Traceback/ or @message like /Task timed out/ | sort @timestamp desc | limit $ERROR_SAMPLE_LIMIT" \
    --region "$REGION" \
    --query 'queryId' \
    --output text)

  error_count=0
  sample="No errors found."

  if [ "$AWS_LAST_OK" = false ] || [ -z "$query_id" ] || [ "$query_id" = "None" ]; then
    sample="(Log group \`$group\` not found or query failed)"
  else
    # Poll for query completion (up to ~25s)
    # Initial sleep gives CloudWatch time to start the query
    sleep 1
    query_status="Running"
    poll_attempts=0
    while [ "$query_status" = "Running" ] || [ "$query_status" = "Scheduled" ]; do
      sleep 2
      poll_attempts=$((poll_attempts + 1))
      if [ "$poll_attempts" -gt 10 ]; then
        query_status="Timeout"
        break
      fi
      query_result=$(run_aws aws logs get-query-results \
        --query-id "$query_id" \
        --region "$REGION" \
        --output json)
      if [ "$AWS_LAST_OK" = false ]; then
        query_status="Failed"
        break
      fi
      query_status=$(echo "$query_result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','Unknown'))" 2>/dev/null || echo "Unknown")
    done

    # If status is still Unknown, check if it's actually Complete with 0 results
    if [ "$query_status" = "Unknown" ]; then
      actual_status=$(echo "$query_result" | python3 -c "
import sys, json
data = json.load(sys.stdin)
s = data.get('status', 'Unknown')
print(s)
" 2>/dev/null || echo "Unknown")
      if [ "$actual_status" = "Complete" ]; then
        query_status="Complete"
      fi
    fi

    if [ "$query_status" = "Complete" ]; then
      # Get accurate count from statistics.recordsMatched
      error_count=$(echo "$query_result" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(int(float(data.get('statistics', {}).get('recordsMatched', 0))))
" 2>/dev/null || echo "0")

      # Extract sample messages — filter per-line to strip neighboring DEBUG/INFO
      # from ECS multi-line events, while also keeping Lambda-style error formats.
      sample=$(echo "$query_result" | python3 -c "
import sys, json, re
data = json.load(sys.stdin)
results = data.get('results', [])
ERROR_RE = re.compile(r' (ERROR|WARNING):|\[ERROR\]|Traceback|Task timed out')
if not results:
    print('No errors found.')
else:
    for row in results:
        fields = {f['field']: f['value'] for f in row}
        ts = fields.get('@timestamp', '')
        msg = fields.get('@message', '').strip()
        filtered = [l for l in msg.splitlines() if ERROR_RE.search(l)]
        if not filtered:
            continue
        for line in filtered:
            if len(line) > 200:
                line = line[:200] + '...'
            print(f'{ts}  {line}')
" 2>/dev/null || echo "(Failed to parse query results)")
    else
      sample="(Query timed out or returned no results — status: $query_status)"
    fi
  fi

  total_errors=$((total_errors + error_count))
  LOG_COUNTS+=("$error_count")

  # Store sample for appendix
  echo "$sample" > "$APPENDIX_TMP/log_sample_$i.txt"
done

# --- Section 2 output: summary table + error type breakdown ---
{
  echo "## 2. CloudWatch Log Review (Errors & Warnings)"
  echo ""
  echo "| Log Group | Errors/Warnings |"
  echo "|---|---|"
  for i in "${!LOG_GROUPS[@]}"; do
    echo "| ${LOG_LABELS[$i]} (\`${LOG_GROUPS[$i]}\`) | ${LOG_COUNTS[$i]} |"
  done
  echo "| **Total** | **$total_errors** |"
  echo ""
} >> "$REPORT"

# Error type breakdown per log group (from sampled entries)
for i in "${!LOG_GROUPS[@]}"; do
  count="${LOG_COUNTS[$i]}"
  if [ "$count" -eq 0 ] 2>/dev/null; then
    continue
  fi

  sample_file="$APPENDIX_TMP/log_sample_$i.txt"
  sample_lines=$(grep -c . "$sample_file" 2>/dev/null || echo "0")
  if [ "$sample_lines" -eq 0 ]; then
    continue
  fi

  breakdown=$(python3 - < "$sample_file" <<'PYEOF'
import sys, re
from collections import defaultdict

PATTERNS = [
    (re.compile(r'stripe_webhook\(\).*Invalid signature'), 'Stripe webhook invalid signature', 'ERROR', 'stripe_webhook():700 — signature mismatch'),
    (re.compile(r'_should_upgrade_to_ws\(\)'), 'WebSocket upgrade unsupported', 'WARNING', 'uvicorn.error — missing WebSocket library'),
    (re.compile(r'Firebase token validation failed|Token expired'), 'Firebase token expired', 'WARNING', 'get_current_user():272 — routine token expiry'),
    (re.compile(r'update_user_storage_usage\(\)'), 'Storage usage update failed', 'WARNING', 'update_user_storage_usage():236'),
    (re.compile(r'upload_experiment\(\)'), 'Experiment file upload failed', 'ERROR', 'upload_experiment():1101'),
    (re.compile(r'read_experiment_status\(\)'), 'Experiment config read error', 'WARNING', 'read_experiment_status():228'),
    (re.compile(r'Traceback'), 'Unhandled traceback', 'ERROR', ''),
    (re.compile(r'Task timed out'), 'Lambda timeout', 'ERROR', ''),
]

counts = defaultdict(lambda: {'count': 0, 'severity': '', 'note': ''})
for line in sys.stdin:
    line = line.strip()
    if not line or line in ('No errors found.', ) or line.startswith('('):
        continue

    matched = False
    for pattern, name, severity, note in PATTERNS:
        if pattern.search(line):
            counts[name]['count'] += 1
            counts[name]['severity'] = severity
            counts[name]['note'] = note
            matched = True
            break

    if not matched:
        m = re.search(r'(\w+)\(\):(\d+)', line)
        if m:
            name = f'{m.group(1)}() error'
            severity = 'ERROR' if ' ERROR:' in line or '[ERROR]' in line else 'WARNING'
            counts[name] = {'count': counts[name]['count'] + 1, 'severity': severity, 'note': f'{m.group(1)}():{m.group(2)}'}
        else:
            counts['Other']['count'] += 1
            counts['Other']['severity'] = 'ERROR'
            counts['Other']['note'] = ''

if counts:
    print('| Error Type | Count | Severity | Notes |')
    print('|---|---|---|---|')
    for key in sorted(counts, key=lambda k: counts[k]['count'], reverse=True):
        d = counts[key]
        print(f'| {key} | ~{d["count"]} | {d["severity"]} | {d["note"]} |')
PYEOF
  ) 2>/dev/null || breakdown=""

  if [ -n "$breakdown" ]; then
    {
      echo "**${LOG_LABELS[$i]}** ($count total) — error types from sample of $sample_lines:"
      echo ""
      echo "$breakdown"
      echo ""
    } >> "$REPORT"
  fi
done

{
  echo "> Full log samples in [Appendix A](#appendix-a-cloudwatch-log-samples)."
  echo ""
} >> "$REPORT"

# ---------------------------------------------------------------------------
# 3. Alarm History (Past 7 Days)
# ---------------------------------------------------------------------------
echo "  Reviewing alarm history..."
history_output=$(run_aws aws cloudwatch describe-alarm-history \
  --region "$REGION" \
  --start-date "$START_ISO" \
  --end-date "$NOW_ISO" \
  --history-item-type StateUpdate \
  --query 'AlarmHistoryItems[*].[AlarmName,Timestamp,HistorySummary]' \
  --output table)

if [ "$AWS_LAST_OK" = false ]; then
  history_output=""
fi

transition_count=0
asg_history_count=0
actionable_alarm_fires=0
actionable_history_lines=""
if echo "$history_output" | grep -q "subscr-"; then
  transition_count=$(echo "$history_output" | grep -c "subscr-" || true)
  asg_history_count=$(echo "$history_output" | grep -cE "$ASG_ALARM_PATTERN" || true)
  actionable_alarm_fires=$(echo "$history_output" | grep "to ALARM" | grep -vcE "$ASG_ALARM_PATTERN" || true)
  actionable_history_lines=$(echo "$history_output" | grep "to ALARM" | grep -vE "$ASG_ALARM_PATTERN" || true)
fi

# Build per-alarm summary table from history output
alarm_summary_table=""
if [ "$transition_count" -gt 0 ]; then
  alarm_summary_table=$(echo "$history_output" | python3 -c "
import sys, re
from collections import defaultdict

alarm_data = defaultdict(lambda: {'ALARM': 0, 'total': 0})
for line in sys.stdin:
    m = re.search(r'(subscr-\S+)', line)
    if not m:
        continue
    alarm = m.group(1)
    alarm_data[alarm]['total'] += 1
    if 'to ALARM' in line:
        alarm_data[alarm]['ALARM'] += 1

if alarm_data:
    print('| Alarm | ALARM fires | Total transitions |')
    print('|---|---|---|')
    for alarm in sorted(alarm_data):
        d = alarm_data[alarm]
        short = alarm.replace('subscr-optinist-', '')
        print(f'| {short} | {d[\"ALARM\"]} | {d[\"total\"]} |')
" 2>/dev/null || echo "*(Failed to parse alarm history)*")
fi

{
  echo "## 3. Alarm History (Past 7 Days)"
  echo ""
  echo "**Actionable alarms that entered ALARM state: $actionable_alarm_fires**"
  echo ""
  if [ -n "$actionable_history_lines" ]; then
    echo '```'
    echo "$actionable_history_lines"
    echo '```'
    echo ""
  fi
  echo "Total transitions: $transition_count (of which $asg_history_count are ASG scaling alarms)"
  echo ""
  if [ -n "$alarm_summary_table" ]; then
    echo "### Per-Alarm Summary"
    echo ""
    echo "- **ALARM fires** — the alarm threshold was breached. For non-ASG alarms (ALB 5xx,"
    echo "  RDS, EFS), any fires should be investigated. For ASG scaling alarms, fires are"
    echo "  expected and trigger automatic scale up/down."
    echo "- **Total transitions** — includes routine INSUFFICIENT_DATA ↔ OK cycling, which"
    echo "  happens during zero-traffic periods (ALB metrics stop reporting) or when ECS tasks"
    echo "  scale to zero (CPU/memory metrics become unavailable). High totals with zero ALARM"
    echo "  fires are normal and need no action."
    echo ""
    echo "$alarm_summary_table"
    echo ""
  fi
  echo "> Full alarm history in [Appendix B](#appendix-b-alarm-history)."
  echo ""
} >> "$REPORT"

# Store alarm history for appendix
echo "$history_output" > "$APPENDIX_TMP/alarm_history.txt"

# ---------------------------------------------------------------------------
# 4. ECS Service Health
# ---------------------------------------------------------------------------
echo "  Checking ECS service health..."
all_healthy=true

# Collect ECS data into arrays for a single summary table
declare -a ECS_NAMES=()
declare -a ECS_STATUSES=()
declare -a ECS_DESIRED=()
declare -a ECS_RUNNING=()
declare -a ECS_ROLLOUTS=()
declare -a ECS_HEALTH=()

for svc in "${SERVICES[@]}"; do
  svc_json=$(run_aws aws ecs describe-services \
    --cluster "$CLUSTER" \
    --services "$svc" \
    --region "$REGION" \
    --query 'services[0].{Service:serviceName,Status:status,Running:runningCount,Desired:desiredCount,Rollout:deployments[0].rolloutState}' \
    --output json)

  if [ "$AWS_LAST_OK" = false ] || [ -z "$svc_json" ]; then
    ECS_NAMES+=("$svc")
    ECS_STATUSES+=("UNKNOWN")
    ECS_DESIRED+=("?")
    ECS_RUNNING+=("?")
    ECS_ROLLOUTS+=("UNKNOWN")
    ECS_HEALTH+=("UNKNOWN")
    all_healthy=false
  else
    svc_name=$(echo "$svc_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('Service','$svc'))" 2>/dev/null || echo "$svc")
    svc_status=$(echo "$svc_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('Status','UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")
    running=$(echo "$svc_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('Running',0))" 2>/dev/null || echo "0")
    desired=$(echo "$svc_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('Desired',0))" 2>/dev/null || echo "0")
    rollout=$(echo "$svc_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('Rollout','UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")

    health="HEALTHY"
    if [ "$running" != "$desired" ]; then
      health="UNHEALTHY"
      all_healthy=false
    fi

    ECS_NAMES+=("$svc_name")
    ECS_STATUSES+=("$svc_status")
    ECS_DESIRED+=("$desired")
    ECS_RUNNING+=("$running")
    ECS_ROLLOUTS+=("$rollout")
    ECS_HEALTH+=("$health")
  fi
done

# Output single summary table
{
  echo "## 4. ECS Service Health"
  echo ""
  echo "| Service | Status | Desired | Running | Rollout | Health |"
  echo "|---|---|---|---|---|---|"
  for i in "${!ECS_NAMES[@]}"; do
    echo "| ${ECS_NAMES[$i]} | ${ECS_STATUSES[$i]} | ${ECS_DESIRED[$i]} | ${ECS_RUNNING[$i]} | ${ECS_ROLLOUTS[$i]} | ${ECS_HEALTH[$i]} |"
  done
  echo ""
  if [ "$all_healthy" = true ]; then
    echo "**All services HEALTHY.**"
  else
    echo "**One or more services UNHEALTHY — see details above.**"
  fi
  echo ""
} >> "$REPORT"

# ---------------------------------------------------------------------------
# 5. Autoscaling Activity
# ---------------------------------------------------------------------------
echo "  Checking autoscaling activity..."
asg_output=$(run_aws aws autoscaling describe-scaling-activities \
  --auto-scaling-group-name "$ASG_NAME" \
  --region "$REGION" \
  --max-items 20 \
  --query "Activities[?StartTime>=\`$START_ISO\`].[StartTime,StatusCode,Description]" \
  --output table)

if [ "$AWS_LAST_OK" = false ]; then
  asg_output="(ASG \`$ASG_NAME\` not found)"
fi

scale_events=0
if echo "$asg_output" | grep -q "Successful\|InProgress\|Failed"; then
  scale_events=$(echo "$asg_output" | grep -c "Successful\|InProgress\|Failed" || true)
fi

# Reuse ASG transition count from Section 3 (already computed)
autoscale_transitions=$asg_history_count

# Current ASG capacity
asg_capacity=$(run_aws aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names "$ASG_NAME" \
  --region "$REGION" \
  --query 'AutoScalingGroups[0].{Min:MinSize,Max:MaxSize,Desired:DesiredCapacity,InService:length(Instances[?LifecycleState==`InService`])}' \
  --output table)

if [ "$AWS_LAST_OK" = false ]; then
  asg_capacity="(ASG \`$ASG_NAME\` not found)"
fi

# Count launches vs terminations from ASG output
launch_count=0
termination_count=0
if echo "$asg_output" | grep -q "Launching"; then
  launch_count=$(echo "$asg_output" | grep -c "Launching" || true)
fi
if echo "$asg_output" | grep -q "Terminating"; then
  termination_count=$(echo "$asg_output" | grep -c "Terminating" || true)
fi

# Build per-alarm transition table for ASG alarms (reuse history_output from Section 3)
asg_alarm_table=""
if [ "$asg_history_count" -gt 0 ]; then
  asg_alarm_table=$(echo "$history_output" | python3 -c "
import sys, re
from collections import defaultdict

pattern = re.compile(r'($ASG_ALARM_PATTERN)')
alarm_data = defaultdict(lambda: {'ALARM': 0, 'total': 0})
for line in sys.stdin:
    m = pattern.search(line)
    if not m:
        continue
    alarm = m.group(1)
    alarm_data[alarm]['total'] += 1
    if 'to ALARM' in line:
        alarm_data[alarm]['ALARM'] += 1

if alarm_data:
    print('| Alarm | ALARM fires | Total transitions |')
    print('|---|---|---|')
    for alarm in sorted(alarm_data):
        d = alarm_data[alarm]
        short = alarm.replace('subscr-optinist-', '')
        print(f'| {short} | {d[\"ALARM\"]} | {d[\"total\"]} |')
" 2>/dev/null || echo "*(Failed to parse alarm history)*")
fi

cat >> "$REPORT" <<EOF
## 5. Autoscaling Activity

These 4 alarms trigger scale up/down automatically and do not send email notifications.
The weekly report is the primary visibility for autoscaling behavior.

### Current ASG Capacity

\`\`\`
$asg_capacity
\`\`\`

### Scaling Event Summary (7 days)

| Event Type | Count |
|---|---|
| Instance launches | $launch_count |
| Instance terminations | $termination_count |
| Total | $scale_events |

EOF

if [ -n "$asg_alarm_table" ]; then
  cat >> "$REPORT" <<EOF
### Per-Alarm Transition Breakdown (7 days)

$asg_alarm_table

EOF
else
  cat >> "$REPORT" <<EOF
**Autoscaling alarm transitions (7 days): $autoscale_transitions**

EOF
fi

cat >> "$REPORT" <<'EOF'
> Full scaling activity in [Appendix C](#appendix-c-scaling-activity).

EOF

# Store scaling activity for appendix
echo "$asg_output" > "$APPENDIX_TMP/scaling_activity.txt"

# ---------------------------------------------------------------------------
# 6. Infrastructure Metrics
# ---------------------------------------------------------------------------
echo "  Collecting infrastructure metrics..."
METRIC_START=$(iso_hours_ago 1)

# --- RDS ---
rds_free_storage=$(run_aws aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name FreeStorageSpace \
  --dimensions "Name=DBInstanceIdentifier,Value=$RDS_INSTANCE" \
  --start-time "$METRIC_START" \
  --end-time "$NOW_ISO" \
  --period 300 \
  --statistics Average \
  --region "$REGION" \
  --query 'sort_by(Datapoints,&Timestamp)[-1].Average' \
  --output text)

if [ -n "$rds_free_storage" ] && [ "$rds_free_storage" != "None" ]; then
  rds_free_gb=$(echo "scale=1; $rds_free_storage / 1073741824" | bc 2>/dev/null || echo "N/A")
else
  rds_free_gb="N/A"
fi

rds_cpu=$(run_aws aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name CPUUtilization \
  --dimensions "Name=DBInstanceIdentifier,Value=$RDS_INSTANCE" \
  --start-time "$METRIC_START" \
  --end-time "$NOW_ISO" \
  --period 300 \
  --statistics Average \
  --region "$REGION" \
  --query 'sort_by(Datapoints,&Timestamp)[-1].Average' \
  --output text)

if [ -n "$rds_cpu" ] && [ "$rds_cpu" != "None" ]; then
  rds_cpu_pct=$(printf "%.1f" "$rds_cpu" 2>/dev/null || echo "N/A")
else
  rds_cpu_pct="N/A"
fi

rds_connections=$(run_aws aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name DatabaseConnections \
  --dimensions "Name=DBInstanceIdentifier,Value=$RDS_INSTANCE" \
  --start-time "$METRIC_START" \
  --end-time "$NOW_ISO" \
  --period 300 \
  --statistics Average \
  --region "$REGION" \
  --query 'sort_by(Datapoints,&Timestamp)[-1].Average' \
  --output text)

if [ -n "$rds_connections" ] && [ "$rds_connections" != "None" ]; then
  rds_conn=$(printf "%.0f" "$rds_connections" 2>/dev/null || echo "N/A")
else
  rds_conn="N/A"
fi

# --- EFS ---
EFS_ID=$(aws efs describe-file-systems --region "$REGION" \
  --query 'FileSystems[0].FileSystemId' --output text 2>/dev/null || echo "")

efs_burst="N/A"
efs_io="N/A"
if [ -n "$EFS_ID" ] && [ "$EFS_ID" != "None" ]; then
  efs_burst_raw=$(run_aws aws cloudwatch get-metric-statistics \
    --namespace AWS/EFS \
    --metric-name BurstCreditBalance \
    --dimensions "Name=FileSystemId,Value=$EFS_ID" \
    --start-time "$METRIC_START" \
    --end-time "$NOW_ISO" \
    --period 300 \
    --statistics Average \
    --region "$REGION" \
    --query 'sort_by(Datapoints,&Timestamp)[-1].Average' \
    --output text)

  if [ -n "$efs_burst_raw" ] && [ "$efs_burst_raw" != "None" ]; then
    efs_burst=$(echo "scale=1; $efs_burst_raw / 1000000000000" | bc 2>/dev/null || echo "N/A")
    efs_burst="${efs_burst} TB"
  fi

  efs_io_raw=$(run_aws aws cloudwatch get-metric-statistics \
    --namespace AWS/EFS \
    --metric-name PercentIOLimit \
    --dimensions "Name=FileSystemId,Value=$EFS_ID" \
    --start-time "$METRIC_START" \
    --end-time "$NOW_ISO" \
    --period 300 \
    --statistics Average \
    --region "$REGION" \
    --query 'sort_by(Datapoints,&Timestamp)[-1].Average' \
    --output text)

  if [ -n "$efs_io_raw" ] && [ "$efs_io_raw" != "None" ]; then
    efs_io=$(printf "%.1f%%" "$efs_io_raw" 2>/dev/null || echo "N/A")
  fi
fi

cat >> "$REPORT" <<EOF
## 6. Infrastructure Metrics

Most recent values (last hour average):

| Metric | Value | Alarm Threshold |
|---|---|---|
| RDS Free Storage | ${rds_free_gb} GB | < 10 GB |
| RDS CPU Utilization | ${rds_cpu_pct}% | > 80% |
| RDS Connections | $rds_conn | > 80 |
| EFS Burst Credits | $efs_burst | < 1 TB |
| EFS I/O Utilization | $efs_io | > 80% |

EOF

# ---------------------------------------------------------------------------
# 7. Lambda Health
# ---------------------------------------------------------------------------
echo "  Checking Lambda health..."
total_lambda_errors=0

cat >> "$REPORT" <<'EOF'
## 7. Lambda Health

Error counts by function (past 7 days):

| Function | Errors |
|---|---|
EOF

for fn in "${LAMBDA_FUNCTIONS[@]}"; do
  fn_errors=$(run_aws aws cloudwatch get-metric-statistics \
    --namespace AWS/Lambda \
    --metric-name Errors \
    --dimensions "Name=FunctionName,Value=$fn" \
    --start-time "$START_ISO" \
    --end-time "$NOW_ISO" \
    --period 604800 \
    --statistics Sum \
    --region "$REGION" \
    --query 'Datapoints[0].Sum' \
    --output text)

  if [ -z "$fn_errors" ] || [ "$fn_errors" = "None" ]; then
    fn_errors=0
  else
    fn_errors=$(printf "%.0f" "$fn_errors" 2>/dev/null || echo "0")
  fi

  total_lambda_errors=$((total_lambda_errors + fn_errors))
  echo "| \`$fn\` | $fn_errors |" >> "$REPORT"
done

cat >> "$REPORT" <<EOF

**Total Lambda errors: $total_lambda_errors**

EOF

# ---------------------------------------------------------------------------
# 8. ALB Performance (Past 7 Days)
# ---------------------------------------------------------------------------
echo "  Checking ALB performance..."
alb_arn_raw=$(aws elbv2 describe-load-balancers \
  --names "$ALB_NAME" \
  --region "$REGION" \
  --query 'LoadBalancers[0].LoadBalancerArn' \
  --output text 2>&1) || true

alb_arn_suffix=""
if [ -n "$alb_arn_raw" ] && ! echo "$alb_arn_raw" | grep -qi "An error occurred"; then
  alb_arn_suffix=$(echo "$alb_arn_raw" | sed 's|.*:loadbalancer/||')
fi

total_5xx_week=0
total_requests_week=0

if [ -n "$alb_arn_suffix" ] && [ "$alb_arn_suffix" != "None" ]; then
  alb_tmp=$(mktemp -d)

  aws cloudwatch get-metric-statistics \
    --namespace AWS/ApplicationELB \
    --metric-name RequestCount \
    --dimensions "Name=LoadBalancer,Value=$alb_arn_suffix" \
    --start-time "$START_ISO" --end-time "$NOW_ISO" \
    --period 86400 --statistics Sum \
    --region "$REGION" \
    --output json > "$alb_tmp/requests.json" 2>/dev/null || echo '{}' > "$alb_tmp/requests.json"

  aws cloudwatch get-metric-statistics \
    --namespace AWS/ApplicationELB \
    --metric-name HTTPCode_ELB_5XX_Count \
    --dimensions "Name=LoadBalancer,Value=$alb_arn_suffix" \
    --start-time "$START_ISO" --end-time "$NOW_ISO" \
    --period 86400 --statistics Sum \
    --region "$REGION" \
    --output json > "$alb_tmp/5xx.json" 2>/dev/null || echo '{}' > "$alb_tmp/5xx.json"

  aws cloudwatch get-metric-statistics \
    --namespace AWS/ApplicationELB \
    --metric-name TargetResponseTime \
    --dimensions "Name=LoadBalancer,Value=$alb_arn_suffix" \
    --start-time "$START_ISO" --end-time "$NOW_ISO" \
    --period 86400 --statistics Average Maximum \
    --region "$REGION" \
    --output json > "$alb_tmp/latency.json" 2>/dev/null || echo '{}' > "$alb_tmp/latency.json"

  alb_table=$(python3 - "$alb_tmp" <<'PYEOF'
import json, sys, os
d = sys.argv[1]
with open(f"{d}/requests.json") as f: requests = json.load(f)
with open(f"{d}/5xx.json") as f: errors = json.load(f)
with open(f"{d}/latency.json") as f: latency = json.load(f)

req_by_date = {dp['Timestamp'][:10]: dp['Sum'] for dp in requests.get('Datapoints', [])}
err_by_date = {dp['Timestamp'][:10]: dp['Sum'] for dp in errors.get('Datapoints', [])}
lat_by_date = {}
for dp in latency.get('Datapoints', []):
    lat_by_date[dp['Timestamp'][:10]] = (dp.get('Average', 0), dp.get('Maximum', 0))

dates = sorted(set(list(req_by_date.keys()) + list(err_by_date.keys()) + list(lat_by_date.keys())))
print('| Date | Requests | 5xx Errors | Error Rate | Avg Response | Max Response |')
print('|---|---|---|---|---|---|')

total_req = 0
total_5xx = 0
for date in dates:
    req = req_by_date.get(date, 0)
    err = err_by_date.get(date, 0)
    avg_lat, max_lat = lat_by_date.get(date, (0, 0))
    rate = (err / req * 100) if req > 0 else 0
    total_req += req
    total_5xx += err
    print(f'| {date} | {req:,.0f} | {err:.0f} | {rate:.2f}% | {avg_lat*1000:.0f}ms | {max_lat*1000:,.0f}ms |')

total_rate = (total_5xx / total_req * 100) if total_req > 0 else 0
print(f'| **Total** | **{total_req:,.0f}** | **{total_5xx:.0f}** | **{total_rate:.2f}%** | | |')
with open(f"{d}/totals.txt", 'w') as f:
    f.write(f'{total_req:.0f}\n{total_5xx:.0f}\n')
PYEOF
  ) || alb_table="*(Failed to parse ALB metrics)*"

  if [ -f "$alb_tmp/totals.txt" ]; then
    total_requests_week=$(sed -n '1p' "$alb_tmp/totals.txt")
    total_5xx_week=$(sed -n '2p' "$alb_tmp/totals.txt")
  fi
  rm -rf "$alb_tmp"

  cat >> "$REPORT" <<EOF
## 8. ALB Performance (Past 7 Days)

$alb_table

EOF
else
  cat >> "$REPORT" <<EOF
## 8. ALB Performance (Past 7 Days)

*(Could not retrieve ALB metrics -- load balancer "$ALB_NAME" not found)*

EOF
fi

# ---------------------------------------------------------------------------
# 9. Summary
# ---------------------------------------------------------------------------
health_status="Yes"
if [ "$all_healthy" = false ]; then
  health_status="**No -- see details above**"
fi

cat >> "$REPORT" <<EOF
---

## 9. Summary

| Metric | Value |
|---|---|
| Actionable alarms | $actionable_alarm_count |
| App errors/warnings (7 days) | $total_errors |
| Actionable alarm fires (7 days) | $actionable_alarm_fires |
| All ECS services healthy | $health_status |
| ASG scaling events (7 days) | $scale_events |
| Autoscaling alarm transitions | $autoscale_transitions |
| RDS free storage | ${rds_free_gb} GB |
| Lambda errors (7 days) | $total_lambda_errors |
| ALB requests (7 days) | $total_requests_week |
| ALB 5xx errors (7 days) | $total_5xx_week |

### Action Items

- [ ] *(fill in any follow-up actions)*

### Notes

*(add any observations, patterns, or concerns)*

---
---

# Appendices — Raw Logs

EOF

# ---------------------------------------------------------------------------
# Appendix A: CloudWatch Log Samples
# ---------------------------------------------------------------------------
echo "  Writing appendices..."
{
  echo "## Appendix A: CloudWatch Log Samples"
  echo ""
  for i in "${!LOG_GROUPS[@]}"; do
    label="${LOG_LABELS[$i]}"
    group="${LOG_GROUPS[$i]}"
    count="${LOG_COUNTS[$i]}"
    echo "### A.$((i+1)) $label"
    echo ""
    echo "Log group: \`$group\` — $count errors/warnings"
    echo ""
    echo '```'
    cat "$APPENDIX_TMP/log_sample_$i.txt" 2>/dev/null || echo "(no data)"
    echo '```'
    echo ""
  done
} >> "$REPORT"

# ---------------------------------------------------------------------------
# Appendix B: Alarm History
# ---------------------------------------------------------------------------
{
  echo "## Appendix B: Alarm History"
  echo ""
  echo "$transition_count total transitions ($asg_history_count ASG scaling)"
  echo ""
  echo '```'
  cat "$APPENDIX_TMP/alarm_history.txt" 2>/dev/null || echo "(no data)"
  echo '```'
  echo ""
} >> "$REPORT"

# ---------------------------------------------------------------------------
# Appendix C: Scaling Activity
# ---------------------------------------------------------------------------
{
  echo "## Appendix C: Scaling Activity"
  echo ""
  echo "$scale_events events ($launch_count launches, $termination_count terminations)"
  echo ""
  echo '```'
  cat "$APPENDIX_TMP/scaling_activity.txt" 2>/dev/null || echo "(no data)"
  echo '```'
} >> "$REPORT"

echo ""
echo "Report saved to: $REPORT"
