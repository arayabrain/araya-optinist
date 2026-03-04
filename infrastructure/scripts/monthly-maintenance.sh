#!/usr/bin/env bash
# Monthly Maintenance Report Generator
# Runs all monthly checks from MAINTENANCE_PROCEDURES.md and produces a markdown report.
#
# Usage: ./monthly-maintenance.sh [output-dir]
#   output-dir: directory for the report file (default: current directory)

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REGION="ap-northeast-1"
COST_REGION="us-east-1"  # Cost Explorer uses us-east-1 endpoint
RDS_INSTANCE="subscr-optinist-cloud-rds"
S3_BUCKET="subscr-optinist-app-storage"
LOG_GROUPS=(
  "/ecs/subscr-optinist-cloud-taskdef"
  "/ecs/subscr-premium-optinist-cloud-taskdef"
  "/ecs/subscr-background-optinist-cloud-taskdef"
)
LAMBDA_FUNCTIONS=(
  "subscr-premium-manager"
  "subscr-free-manager"
  "subscr-common-user-manager"
  "subscr-premium-cleanup"
  "subscr-free-cleanup"
  "subscr-cost-tracker"
)

# ---------------------------------------------------------------------------
# Date helpers (portable macOS / Linux)
# ---------------------------------------------------------------------------
if date -v-1d +%s >/dev/null 2>&1; then
  epoch_days_ago()    { date -u -v-"${1}"d +%s; }
  iso_days_ago()      { date -u -v-"${1}"d +%Y-%m-%dT%H:%M:%SZ; }
  month_start_ago()   { date -u -v-"${1}"m +%Y-%m-01; }
else
  epoch_days_ago()    { date -u -d "${1} days ago" +%s; }
  iso_days_ago()      { date -u -d "${1} days ago" +%Y-%m-%dT%H:%M:%SZ; }
  month_start_ago()   { date -u -d "$1 months ago" +%Y-%m-01; }
fi

# ---------------------------------------------------------------------------
# Output setup
# ---------------------------------------------------------------------------
OUTPUT_DIR="${1:-.}"
mkdir -p "$OUTPUT_DIR"
REPORT="$OUTPUT_DIR/monthly-maintenance-$(date +%Y-%m).md"
TODAY=$(date +%Y-%m-%d)
MONTH_LABEL=$(date +%Y-%m)
MONTH_START=$(date -u +%Y-%m-01)
START_ISO_30=$(iso_days_ago 30)
NOW_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)

echo "Generating monthly maintenance report..."

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
# Monthly Maintenance Report

| Field | Value |
|---|---|
| **Date** | $TODAY |
| **Rotation Period** | $MONTH_LABEL |
| **Maintainer** | *(fill in)* |

---

EOF

# ---------------------------------------------------------------------------
# 1. AWS Cost Review (3-Month Trend)
# ---------------------------------------------------------------------------
echo "  Reviewing AWS costs (3-month trend)..."
COST_START=$(month_start_ago 3)

cost_trend_json=$(run_aws aws ce get-cost-and-usage \
  --time-period "Start=$COST_START,End=$TODAY" \
  --granularity MONTHLY \
  --metrics BlendedCost \
  --group-by Type=DIMENSION,Key=SERVICE \
  --region "$COST_REGION" \
  --output json)

if [ "$AWS_LAST_OK" = false ]; then
  cost_trend_table="*(Failed to retrieve cost data)*"
else

cost_tmp=$(mktemp)
echo "$cost_trend_json" > "$cost_tmp"

cost_trend_table=$(python3 - "$cost_tmp" <<'PYEOF'
import json, sys

with open(sys.argv[1]) as f:
    data = json.load(f)

months = []
service_costs = {}
for period in data.get('ResultsByTime', []):
    month = period['TimePeriod']['Start'][:7]
    months.append(month)
    for group in period.get('Groups', []):
        svc = group['Keys'][0]
        amt = float(group['Metrics']['BlendedCost']['Amount'])
        service_costs.setdefault(svc, {})[month] = amt

# Sort by most recent full month cost (second to last, since last is MTD)
sort_month = months[-2] if len(months) > 1 else months[0]
sorted_svcs = sorted(service_costs.items(),
    key=lambda x: x[1].get(sort_month, 0), reverse=True)

# Header
last_month = months[-1]
cols = [m if m != last_month else f'{m} (MTD)' for m in months]
header = '| Service | ' + ' | '.join(cols) + ' | Trend |'
divider = '|---' + '|---' * len(months) + '|---|'
print(header)
print(divider)

# Rows
totals = {m: 0.0 for m in months}
for svc, costs in sorted_svcs:
    max_cost = max(costs.values()) if costs else 0
    if max_cost < 0.50:
        continue
    row = f'| {svc}'
    for m in months:
        amt = costs.get(m, 0)
        totals[m] += amt
        row += f' | ${amt:.2f}'
    # Trend: compare last two full months
    if len(months) >= 3:
        prev = costs.get(months[-3], 0)
        curr = costs.get(months[-2], 0)
    elif len(months) >= 2:
        prev = costs.get(months[0], 0)
        curr = costs.get(months[1], 0)
    else:
        prev = curr = 0
    if prev > 0.50:
        pct = ((curr - prev) / prev) * 100
        if pct > 20:
            trend = f'**+{pct:.0f}%**'
        elif pct < -20:
            trend = f'{pct:.0f}%'
        else:
            trend = 'stable'
    else:
        trend = '-'
    row += f' | {trend} |'
    print(row)

# Total row
total_row = '| **TOTAL**'
for m in months:
    total_row += f' | **${totals[m]:.2f}**'
if len(months) >= 3 and totals[months[-3]] > 0:
    pct = ((totals[months[-2]] - totals[months[-3]]) / totals[months[-3]]) * 100
    total_row += f' | **{pct:+.0f}%** |'
else:
    total_row += ' | - |'
print(total_row)
PYEOF
) || cost_trend_table="*(Failed to parse cost data)*"

rm -f "$cost_tmp"

fi  # end AWS_LAST_OK check for cost data

cat >> "$REPORT" <<EOF
## 1. AWS Cost Review (3-Month Trend)

$cost_trend_table

**Action items:**
- [ ] Flag any service with > 20% cost increase from previous month
- [ ] Review premium instance uptime vs. utilization
- [ ] Check for unused or orphaned resources

EOF

# ---------------------------------------------------------------------------
# 2. RDS Health Check
# ---------------------------------------------------------------------------
echo "  Checking RDS health..."

# 2a. Backup status
backup_output=$(run_aws aws rds describe-db-instances \
  --db-instance-identifier "$RDS_INSTANCE" \
  --region "$REGION" \
  --query 'DBInstances[0].{Instance:DBInstanceIdentifier,Status:DBInstanceStatus,Engine:Engine,Storage_GB:AllocatedStorage,BackupRetention_Days:BackupRetentionPeriod,LatestBackup:LatestRestorableTime}' \
  --output table)

if [ "$AWS_LAST_OK" = false ]; then
  backup_output="(RDS instance \`$RDS_INSTANCE\` not found)"
fi

# 2b. Free storage
free_storage_bytes=$(aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name FreeStorageSpace \
  --dimensions "Name=DBInstanceIdentifier,Value=$RDS_INSTANCE" \
  --start-time "$(iso_days_ago 1)" \
  --end-time "$NOW_ISO" \
  --period 3600 \
  --statistics Average \
  --region "$REGION" \
  --query 'Datapoints | sort_by(@, &Timestamp) | [-1].Average' \
  --output text 2>/dev/null || echo "N/A")

free_storage_gb="N/A"
if [ "$free_storage_bytes" != "N/A" ] && [ "$free_storage_bytes" != "None" ]; then
  free_storage_gb=$(python3 -c "print(f'{float($free_storage_bytes) / 1073741824:.2f}')" 2>/dev/null || echo "N/A")
fi

# 2c. Slow query check
slowquery_output=$(run_aws aws logs filter-log-events \
  --log-group-name "/aws/rds/instance/$RDS_INSTANCE/slowquery" \
  --start-time "$(epoch_days_ago 30)000" \
  --region "$REGION" \
  --max-items 20 \
  --query 'events[*].message' \
  --output text)

slowquery_status="No slow queries found (or slow query logging not enabled)."
slowquery_count=0
if [ -n "$slowquery_output" ] && ! echo "$slowquery_output" | grep -qi "error\|ResourceNotFoundException\|None"; then
  slowquery_count=$(echo "$slowquery_output" | wc -l | tr -d ' ')
  slowquery_status="$slowquery_count slow queries logged in the past 30 days."
fi

cat >> "$REPORT" <<EOF
## 2. RDS Health Check

### Backup Status

\`\`\`
$backup_output
\`\`\`

**Free storage: ${free_storage_gb} GB**

### Slow Query Review

$slowquery_status

<details>
<summary>Slow query sample</summary>

\`\`\`
$slowquery_output
\`\`\`

</details>

EOF

# ---------------------------------------------------------------------------
# 3. Lambda Log Review
# ---------------------------------------------------------------------------
echo "  Reviewing Lambda errors..."
total_lambda_errors=0

cat >> "$REPORT" <<'EOF'
## 3. Lambda Log Review (Past 30 Days)

EOF

lambda_table="| Function | Days with Errors | Total Errors |
|---|---|---|"

for fn in "${LAMBDA_FUNCTIONS[@]}"; do
  echo "    $fn..."
  fn_output=$(run_aws aws cloudwatch get-metric-statistics \
    --namespace AWS/Lambda \
    --metric-name Errors \
    --dimensions "Name=FunctionName,Value=$fn" \
    --start-time "$START_ISO_30" \
    --end-time "$NOW_ISO" \
    --period 86400 \
    --statistics Sum \
    --region "$REGION" \
    --query 'Datapoints[?Sum>`0`].[Timestamp,Sum]' \
    --output text)

  if [ -z "$fn_output" ] || echo "$fn_output" | grep -q "None"; then
    days_with_errors=0
    fn_total=0
  else
    days_with_errors=$(echo "$fn_output" | wc -l | tr -d ' ')
    fn_total=$(echo "$fn_output" | awk '{sum += $2} END {printf "%.0f", sum}')
  fi

  total_lambda_errors=$((total_lambda_errors + fn_total))
  lambda_table="$lambda_table
| \`$fn\` | $days_with_errors | $fn_total |"
done

cat >> "$REPORT" <<EOF
$lambda_table

**Total Lambda errors (30 days): $total_lambda_errors**

EOF

# ---------------------------------------------------------------------------
# 4. Alarm Summary (Past 30 Days)
# ---------------------------------------------------------------------------
echo "  Compiling 30-day alarm summary..."
alarm_history=$(run_aws aws cloudwatch describe-alarm-history \
  --region "$REGION" \
  --start-date "$START_ISO_30" \
  --end-date "$NOW_ISO" \
  --history-item-type StateUpdate \
  --query 'AlarmHistoryItems[*].[AlarmName,Timestamp,HistorySummary]' \
  --output text)

if [ "$AWS_LAST_OK" = false ]; then
  alarm_history=""
fi

# Count transitions to ALARM state
alarm_fires=0
if [ -n "$alarm_history" ]; then
  alarm_fires=$(echo "$alarm_history" | grep -c "to ALARM" || true)
fi

# Count unique alarms that fired
unique_alarms="(none)"
if [ "$alarm_fires" -gt 0 ]; then
  unique_alarms=$(echo "$alarm_history" | grep "to ALARM" | awk '{print $1}' | sort -u | tr '\n' ', ' | sed 's/,$//')
fi

cat >> "$REPORT" <<EOF
## 4. Alarm Summary (Past 30 Days)

| Metric | Value |
|---|---|
| Times alarms entered ALARM state | $alarm_fires |
| Unique alarms that fired | $unique_alarms |

<details>
<summary>Full alarm history</summary>

\`\`\`
$alarm_history
\`\`\`

</details>

EOF

# ---------------------------------------------------------------------------
# 5. Storage Overview
# ---------------------------------------------------------------------------
echo "  Checking storage usage..."

# S3 main bucket
s3_objects="N/A"
s3_size="N/A"
s3_raw=$(aws s3 ls "s3://$S3_BUCKET" --recursive --summarize 2>&1) || true

s3_summary=""
if [ -n "$s3_raw" ] && ! echo "$s3_raw" | grep -qi "An error occurred\|NoSuchBucket\|fatal error"; then
  s3_summary=$(echo "$s3_raw" | tail -2)
fi

if [ -n "$s3_summary" ]; then
  s3_objects=$(echo "$s3_summary" | grep "Total Objects" | awk '{print $3}' || echo "N/A")
  s3_bytes=$(echo "$s3_summary" | grep "Total Size" | awk '{print $3}' || echo "0")
  if [ "$s3_bytes" != "0" ] && [ -n "$s3_bytes" ]; then
    s3_size=$(python3 -c "b=float($s3_bytes); print(f'{b/1073741824:.2f} GB')" 2>/dev/null || echo "N/A")
  fi
fi

# EFS
efs_info=$(aws efs describe-file-systems --region "$REGION" \
  --query 'FileSystems[*].{Id:FileSystemId,Name:Name,SizeGB:SizeInBytes.Value,Status:LifeCycleState}' \
  --output json 2>/dev/null || echo "[]")

efs_table=$(echo "$efs_info" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print('| FileSystem | Name | Size | Status |')
print('|---|---|---|---|')
for fs in data:
    size_gb = fs.get('SizeGB', 0) / 1073741824
    name = fs.get('Name', '-') or '-'
    print(f'| {fs[\"Id\"]} | {name} | {size_gb:.1f} GB | {fs[\"Status\"]} |')
" 2>/dev/null || echo "*(Failed to retrieve EFS data)*")

# Log groups
log_group_table=$(aws logs describe-log-groups \
  --region "$REGION" \
  --query 'logGroups[?storedBytes>`0`].[logGroupName,storedBytes,retentionInDays]' \
  --output json 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
data.sort(key=lambda x: x[1] or 0, reverse=True)
print('| Log Group | Stored | Retention |')
print('|---|---|---|')
total = 0
for name, stored, retention in data[:15]:
    stored = stored or 0
    total += stored
    mb = stored / 1048576
    ret = f'{retention} days' if retention else 'Never expire'
    print(f'| \`{name}\` | {mb:.1f} MB | {ret} |')
print(f'| **Total** | **{total/1048576:.1f} MB** | |')
" 2>/dev/null || echo "*(Failed to retrieve log group data)*")

cat >> "$REPORT" <<EOF
## 5. Storage Overview

### S3 ($S3_BUCKET)

| Metric | Value |
|---|---|
| Objects | $s3_objects |
| Total size | $s3_size |

### EFS

$efs_table

### CloudWatch Log Groups

$log_group_table

> Log storage is a major CloudWatch cost driver. Review retention periods if costs are rising.

EOF

# ---------------------------------------------------------------------------
# 6. Rotation Summary (Manual)
# ---------------------------------------------------------------------------
cat >> "$REPORT" <<'EOF'
---

## 6. Rotation Summary

### Support Emails

| Date | Category | Summary | Status |
|---|---|---|---|
| | | | |

### Recurring Issues

- *(describe any patterns observed)*

### Handoff Notes

- *(anything the next on-call person should know)*

### Action Items

- [ ] *(fill in any follow-up actions)*
EOF

echo ""
echo "Report saved to: $REPORT"
