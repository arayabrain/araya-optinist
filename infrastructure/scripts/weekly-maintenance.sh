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
)
LOG_LABELS=("Free Tier" "Premium Tier" "Background Service")
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
echo "  Reviewing CloudWatch logs for errors..."
total_errors=0

cat >> "$REPORT" <<'EOF'
## 2. CloudWatch Log Review (Errors)

EOF

for i in "${!LOG_GROUPS[@]}"; do
  label="${LOG_LABELS[$i]}"
  group="${LOG_GROUPS[$i]}"
  echo "    $label..."

  # Use CloudWatch Logs Insights to match only Python logging ERROR level
  query_id=$(run_aws aws logs start-query \
    --log-group-name "$group" \
    --start-time "$START_EPOCH_S" \
    --end-time "$NOW_EPOCH_S" \
    --query-string "filter @message like / ERROR:/ | sort @timestamp desc | limit $ERROR_SAMPLE_LIMIT" \
    --region "$REGION" \
    --query 'queryId' \
    --output text)

  error_count=0
  sample="No errors found."

  if [ "$AWS_LAST_OK" = false ] || [ -z "$query_id" ] || [ "$query_id" = "None" ]; then
    sample="(Log group \`$group\` not found or query failed)"
  else
    # Poll for query completion (up to ~20s)
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

    if [ "$query_status" = "Complete" ]; then
      # Get accurate count from statistics.recordsMatched
      error_count=$(echo "$query_result" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(int(float(data.get('statistics', {}).get('recordsMatched', 0))))
" 2>/dev/null || echo "0")

      # Extract sample messages from results
      sample=$(echo "$query_result" | python3 -c "
import sys, json
data = json.load(sys.stdin)
results = data.get('results', [])
if not results:
    print('No errors found.')
else:
    for row in results:
        fields = {f['field']: f['value'] for f in row}
        ts = fields.get('@timestamp', '')
        msg = fields.get('@message', '').strip()
        # Truncate long messages
        if len(msg) > 200:
            msg = msg[:200] + '...'
        print(f'{ts}  {msg}')
" 2>/dev/null || echo "(Failed to parse query results)")
    else
      sample="(Query did not complete: status=$query_status)"
    fi
  fi

  total_errors=$((total_errors + error_count))

  cat >> "$REPORT" <<EOF
### $label

**Application errors: $error_count** (log group: \`$group\`)

<details>
<summary>Last $ERROR_SAMPLE_LIMIT error entries</summary>

\`\`\`
$sample
\`\`\`

</details>

EOF
done

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
  echo "<details>"
  echo "<summary>Full alarm history</summary>"
  echo ""
  echo '```'
  echo "$history_output"
  echo '```'
  echo ""
  echo "</details>"
  echo ""
} >> "$REPORT"

# ---------------------------------------------------------------------------
# 4. ECS Service Health
# ---------------------------------------------------------------------------
echo "  Checking ECS service health..."
all_healthy=true

cat >> "$REPORT" <<'EOF'
## 4. ECS Service Health

EOF

for svc in "${SERVICES[@]}"; do
  svc_output=$(run_aws aws ecs describe-services \
    --cluster "$CLUSTER" \
    --services "$svc" \
    --region "$REGION" \
    --query 'services[0].{Service:serviceName,Status:status,Running:runningCount,Desired:desiredCount,Rollout:deployments[0].rolloutState}' \
    --output table)

  if [ "$AWS_LAST_OK" = false ]; then
    svc_output="(Service \`$svc\` not found in cluster \`$CLUSTER\`)"
    status_icon="UNKNOWN"
    all_healthy=false
  else
    # Check healthy: running == desired and status == ACTIVE
    running=$(run_aws aws ecs describe-services \
      --cluster "$CLUSTER" --services "$svc" --region "$REGION" \
      --query 'services[0].runningCount' --output text)
    if [ "$AWS_LAST_OK" = false ] || [ -z "$running" ] || [ "$running" = "None" ]; then
      running="0"
    fi
    desired=$(run_aws aws ecs describe-services \
      --cluster "$CLUSTER" --services "$svc" --region "$REGION" \
      --query 'services[0].desiredCount' --output text)
    if [ "$AWS_LAST_OK" = false ] || [ -z "$desired" ] || [ "$desired" = "None" ]; then
      desired="0"
    fi

    status_icon="HEALTHY"
    if [ "$running" != "$desired" ]; then
      status_icon="UNHEALTHY (running=$running, desired=$desired)"
      all_healthy=false
    fi
  fi

  cat >> "$REPORT" <<EOF
### $svc — $status_icon

\`\`\`
$svc_output
\`\`\`

EOF
done

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

cat >> "$REPORT" <<EOF
## 5. Autoscaling Activity

These 4 alarms trigger scale up/down automatically and do not send email notifications.
The weekly report is the primary visibility for autoscaling behavior.

**ASG scaling events (7 days): $scale_events**
**Autoscaling alarm transitions (7 days): $autoscale_transitions**

Current ASG capacity:

\`\`\`
$asg_capacity
\`\`\`

<details>
<summary>Recent scaling activity</summary>

\`\`\`
$asg_output
\`\`\`

</details>

EOF

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
| Application errors (7 days) | $total_errors |
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
EOF

echo ""
echo "Report saved to: $REPORT"
