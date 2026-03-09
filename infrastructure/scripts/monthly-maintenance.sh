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

# Temp dir for appendix data
APPENDIX_TMP=$(mktemp -d)
trap 'rm -rf "$APPENDIX_TMP"' EXIT

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

# Discover which AWS services have Project=subscr-optinist tagged resources.
# Cost allocation tags are not activated (requires management account), so we
# query the tagging API and manually cross-reference with Cost Explorer results.
echo "    Discovering tagged resources..."
tagged_arns_json=$(aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=Project,Values=subscr-optinist \
  --region "$REGION" \
  --query 'ResourceTagMappingList[].ResourceARN' \
  --output json 2>/dev/null || echo '[]')
echo "$tagged_arns_json" > "$APPENDIX_TMP/tagged_arns.json"

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

cost_trend_table=$(python3 - "$cost_tmp" "$APPENDIX_TMP/tagged_arns.json" <<'PYEOF'
import json, sys

with open(sys.argv[1]) as f:
    data = json.load(f)
with open(sys.argv[2]) as f:
    tagged_arns = json.load(f)

# Map ARN service prefixes to Cost Explorer service dimension names.
# CE names don't follow a standard pattern, so we maintain an explicit map.
ARN_TO_CE = {
    'ec2':                  ['Amazon Elastic Compute Cloud - Compute', 'EC2 - Other', 'Amazon Virtual Private Cloud'],
    'rds':                  ['Amazon Relational Database Service', 'AWS Backup'],
    'elasticloadbalancing': ['Amazon Elastic Load Balancing'],
    'ecs':                  ['Amazon Elastic Container Service'],
    'elasticfilesystem':    ['Amazon Elastic File System'],
    's3':                   ['Amazon Simple Storage Service'],
    'lambda':               ['AWS Lambda'],
    'logs':                 ['AmazonCloudWatch', 'Amazon CloudWatch'],
    'cloudwatch':           ['AmazonCloudWatch', 'Amazon CloudWatch'],
    'monitoring':           ['AmazonCloudWatch', 'Amazon CloudWatch'],
    'route53':              ['Amazon Route 53'],
    'acm':                  ['AWS Certificate Manager'],
    'secretsmanager':       ['AWS Secrets Manager'],
    'ecr':                  ['Amazon EC2 Container Registry', 'Amazon Elastic Container Registry Public'],
    'events':               ['Amazon EventBridge', 'CloudWatch Events'],
    'sns':                  ['Amazon Simple Notification Service'],
    'sqs':                  ['Amazon Simple Queue Service'],
    'kms':                  ['AWS Key Management Service'],
    'autoscaling':          ['Amazon Elastic Compute Cloud - Compute', 'EC2 - Other'],
    'rds-db':               ['Amazon Relational Database Service'],
    'dynamodb':             ['Amazon DynamoDB'],
    'servicediscovery':     ['AWS Cloud Map'],
    'ssm':                  ['AWS Systems Manager'],
}

# Build set of CE service names that correspond to tagged resources
tagged_ce_services = set()
for arn in tagged_arns:
    parts = arn.split(':')
    if len(parts) >= 3:
        svc = parts[2]
        for ce_name in ARN_TO_CE.get(svc, []):
            tagged_ce_services.add(ce_name)

months = []
service_costs = {}
for period in data.get('ResultsByTime', []):
    month = period['TimePeriod']['Start'][:7]
    months.append(month)
    for group in period.get('Groups', []):
        svc = group['Keys'][0]
        amt = float(group['Metrics']['BlendedCost']['Amount'])
        service_costs.setdefault(svc, {})[month] = amt

if not months:
    print('*(No cost data returned)*')
    sys.exit(0)

# Account-level services that cannot be tagged to individual resources
# but are project costs in a dedicated account.
ALWAYS_INCLUDE = {
    'Tax',
    'AWS CloudTrail',
    'AWS Cost Explorer',
    'AWS Glue',
    'AWS Key Management Service',
    'Amazon Location Service',
    'Amazon EC2 Container Registry (ECR)',
    'Amazon Route 53',
}

# Filter to only services with tagged resources (if we found any)
if tagged_ce_services:
    filtered = {s: c for s, c in service_costs.items() if s in tagged_ce_services or s in ALWAYS_INCLUDE}
    excluded = {s: c for s, c in service_costs.items() if s not in tagged_ce_services and s not in ALWAYS_INCLUDE}
else:
    filtered = service_costs
    excluded = {}

# Sort by most recent full month cost (second to last, since last is MTD)
sort_month = months[-2] if len(months) > 1 else months[0]
sorted_svcs = sorted(filtered.items(),
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

# Show excluded costs if any were filtered out
if excluded:
    excluded_total = sum(
        sum(c.values()) for c in excluded.values()
    )
    if excluded_total > 1.0:
        print()
        print(f'*{len(excluded)} untagged services excluded (${excluded_total:.2f} total across all months): {", ".join(sorted(excluded.keys()))}*')
PYEOF
) || cost_trend_table="*(Failed to parse cost data)*"

rm -f "$cost_tmp"

fi  # end AWS_LAST_OK check for cost data

cat >> "$REPORT" <<EOF
## 1. AWS Cost Review (3-Month Trend)

$cost_trend_table

EOF

cat >> "$REPORT" <<'EOF'
**Action items:**
- [ ] Flag any service with > 20% cost increase from previous month
- [ ] Review premium instance uptime vs. utilization
- [ ] Check for unused or orphaned resources

EOF

# 1b. Cost Tracker Custom Metrics (from Optinist/CostTracking namespace)
echo "  Querying cost tracker metrics..."
COST_METRICS=(
  "ActualMonthToDateSpend"
  "ExpectedMonthlyBudget"
  "CostPerPremiumUser"
  "CostPerFreeUser"
  "PremiumUtilization"
  "FreeUtilization"
  "PremiumSessionHoursMTD"
  "ActivePremiumUsers"
  "ActiveFreeUsers"
  "PremiumInstanceCount"
  "FreeInstanceCount"
)

cost_tracker_table="| Metric | Latest Value |
|---|---|"

for metric in "${COST_METRICS[@]}"; do
  val=$(aws cloudwatch get-metric-statistics \
    --namespace "Optinist/CostTracking" \
    --metric-name "$metric" \
    --start-time "$(iso_days_ago 2)" \
    --end-time "$NOW_ISO" \
    --period 3600 \
    --statistics Maximum \
    --region "$REGION" \
    --query 'Datapoints | sort_by(@, &Timestamp) | [-1].Maximum' \
    --output text 2>/dev/null || echo "N/A")
  if [ "$val" = "None" ] || [ -z "$val" ]; then
    val="N/A"
  elif echo "$metric" | grep -qi "cost\|spend\|budget"; then
    val=$(python3 -c "print(f'\${float($val):.2f}')" 2>/dev/null || echo "$val")
  elif echo "$metric" | grep -qi "utilization"; then
    val=$(python3 -c "print(f'{float($val):.1f}%')" 2>/dev/null || echo "$val")
  elif echo "$metric" | grep -qi "hours"; then
    val=$(python3 -c "print(f'{float($val):.1f}h')" 2>/dev/null || echo "$val")
  else
    val=$(python3 -c "v=float($val); print(f'{v:.0f}' if v==int(v) else f'{v:.2f}')" 2>/dev/null || echo "$val")
  fi
  cost_tracker_table="$cost_tracker_table
| \`$metric\` | $val |"
done

cat >> "$REPORT" <<EOF
### Cost Tracker Metrics (Latest)

$cost_tracker_table

> Source: \`Optinist/CostTracking\` CloudWatch namespace (published hourly by \`subscr-cost-tracker\` Lambda).

EOF

# 1c. Per-User Usage Breakdown (via cost-tracker Lambda)
echo "  Querying per-user usage breakdown..."
USAGE_REPORT_TMP=$(mktemp)
usage_invoke_ok=true
aws lambda invoke \
  --function-name "subscr-cost-tracker" \
  --payload '{"mode":"usage_report"}' \
  --region "$REGION" \
  "$USAGE_REPORT_TMP" >/dev/null 2>&1 || usage_invoke_ok=false

usage_summary=""
usage_detail_table=""

if [ "$usage_invoke_ok" = true ] && [ -f "$USAGE_REPORT_TMP" ]; then
  # Lambda response wraps data in {"statusCode":200,"body":"..."}
  usage_body=$(python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    resp = json.load(f)
body = resp.get('body', '{}')
if isinstance(body, str):
    body = json.loads(body)
print(json.dumps(body))
" "$USAGE_REPORT_TMP" 2>/dev/null || echo "{}")

  usage_summary=$(echo "$usage_body" | python3 -c "
import json, sys
data = json.load(sys.stdin)
s = data.get('summary', {})
if not s:
    print('*(No usage data available — instance_usage_log may be empty)*')
    sys.exit(0)

month = s.get('month', 'N/A')
p = s.get('premium', {})
f = s.get('free', {})

print(f'**Month: {month}**')
print()
print('| Tier | Users | Total Hours | Total Cost | Avg Hours/User | Avg Cost/User |')
print('|---|---|---|---|---|---|')
if p.get('users', 0) > 0:
    print(f'| Premium | {p[\"users\"]} | {p[\"total_hours\"]:.1f}h | \${p[\"total_cost\"]:.2f} | {p[\"avg_hours\"]:.1f}h | \${p[\"avg_cost\"]:.2f} |')
else:
    print('| Premium | 0 | — | — | — | — |')
if f.get('users', 0) > 0:
    print(f'| Free | {f[\"users\"]} | {f[\"total_hours\"]:.1f}h | \${f[\"total_cost\"]:.2f} | {f[\"avg_hours\"]:.1f}h | \${f[\"avg_cost\"]:.2f} |')
else:
    print('| Free | 0 | — | — | — | — |')
grand_hours = p.get('total_hours', 0) + f.get('total_hours', 0)
grand_cost = p.get('total_cost', 0) + f.get('total_cost', 0)
grand_users = p.get('users', 0) + f.get('users', 0)
print(f'| **Total** | **{grand_users}** | **{grand_hours:.1f}h** | **\${grand_cost:.2f}** | | |')
" 2>/dev/null || echo "*(Failed to parse usage summary)*")

  usage_detail_table=$(echo "$usage_body" | python3 -c "
import json, sys
data = json.load(sys.stdin)
users = data.get('users', [])
if not users:
    print('*(No per-user data available)*')
    sys.exit(0)

print('| User | Tier | Sessions | Hours | Cost |')
print('|---|---|---|---|---|')
for u in users:
    print(f'| \`{u[\"user_id\"]}\` | {u[\"tier\"]} | {u[\"sessions\"]} | {u[\"hours\"]:.1f}h | \${u[\"cost\"]:.2f} |')
" 2>/dev/null || echo "*(Failed to parse per-user data)*")
else
  usage_summary="*(Failed to invoke cost-tracker Lambda — is it deployed?)*"
  usage_detail_table="*(No data)*"
fi

# Store detail table for appendix
echo "$usage_detail_table" > "$APPENDIX_TMP/usage_detail.txt"
rm -f "$USAGE_REPORT_TMP"

cat >> "$REPORT" <<EOF
### Per-User Usage Summary

$usage_summary

> Per-user detail in [Appendix C](#appendix-c-per-user-usage-breakdown). Source: \`instance_usage_log\` table via \`subscr-cost-tracker\` Lambda.

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

# 2d. RDS error log check
echo "  Checking RDS error log..."
rds_error_output=$(run_aws aws logs filter-log-events \
  --log-group-name "/aws/rds/instance/$RDS_INSTANCE/error" \
  --start-time "$(epoch_days_ago 30)000" \
  --region "$REGION" \
  --max-items 20 \
  --query 'events[*].message' \
  --output text)

rds_error_status="No RDS errors logged in the past 30 days."
rds_error_count=0
if [ -n "$rds_error_output" ] && ! echo "$rds_error_output" | grep -qi "ResourceNotFoundException\|None"; then
  rds_error_count=$(echo "$rds_error_output" | wc -l | tr -d ' ')
  rds_error_status="$rds_error_count RDS error/warning entries in the past 30 days."
fi

# Store RDS logs for appendix
echo "$slowquery_output" > "$APPENDIX_TMP/rds_slowquery.txt"
echo "$rds_error_output" > "$APPENDIX_TMP/rds_errors.txt"

cat >> "$REPORT" <<EOF
## 2. RDS Health Check

### Backup Status

\`\`\`
$backup_output
\`\`\`

**Free storage: ${free_storage_gb} GB**

### Slow Query Review

$slowquery_status

### RDS Error Log

$rds_error_status

> Full RDS logs in [Appendix A](#appendix-a-rds-logs).

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

# Store alarm history for appendix
echo "$alarm_history" > "$APPENDIX_TMP/alarm_history.txt"

cat >> "$REPORT" <<EOF
## 4. Alarm Summary (Past 30 Days)

| Metric | Value |
|---|---|
| Times alarms entered ALARM state | $alarm_fires |
| Unique alarms that fired | $unique_alarms |

> Full alarm history in [Appendix B](#appendix-b-alarm-history).

EOF

# ---------------------------------------------------------------------------
# 5. Storage Overview (with month-over-month trends)
# ---------------------------------------------------------------------------
echo "  Checking storage usage..."

# S3 main bucket — current snapshot
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

# S3 — 3-month trend via CloudWatch BucketSizeBytes (daily metric published by S3)
echo "    Fetching S3 storage trend (3 months)..."
s3_trend_json=$(aws cloudwatch get-metric-statistics \
  --namespace AWS/S3 \
  --metric-name BucketSizeBytes \
  --dimensions "Name=BucketName,Value=$S3_BUCKET" "Name=StorageType,Value=StandardStorage" \
  --start-time "$(iso_days_ago 90)" \
  --end-time "$NOW_ISO" \
  --period 2592000 \
  --statistics Average \
  --region "$REGION" \
  --output json 2>/dev/null || echo '{"Datapoints":[]}')

s3_trend_table=$(echo "$s3_trend_json" | python3 -c "
import sys, json
data = json.load(sys.stdin)
pts = sorted(data.get('Datapoints', []), key=lambda x: x['Timestamp'])
if not pts:
    print('*(No S3 CloudWatch metric data available — metric may take 24h to appear)*')
    sys.exit(0)
print('| Month | Size | Change |')
print('|---|---|---|')
prev = None
for pt in pts:
    month = pt['Timestamp'][:7]
    gb = pt['Average'] / 1073741824
    if prev is not None and prev > 0:
        pct = ((gb - prev) / prev) * 100
        change = f'{pct:+.1f}%'
    else:
        change = '-'
    print(f'| {month} | {gb:.2f} GB | {change} |')
    prev = gb
" 2>/dev/null || echo "*(Failed to parse S3 trend data)*")

# EFS — current size + 3-month trend
efs_info=$(aws efs describe-file-systems --region "$REGION" \
  --query 'FileSystems[*].{Id:FileSystemId,Name:Name,SizeGB:SizeInBytes.Value,Status:LifeCycleState}' \
  --output json 2>/dev/null || echo "[]")

# Get EFS filesystem IDs for trend queries
efs_ids=$(echo "$efs_info" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for fs in data:
    if 'subscr' in (fs.get('Name') or '').lower():
        print(fs['Id'])
" 2>/dev/null)

echo "    Fetching EFS storage trends (3 months)..."
efs_trend_data=""
for fs_id in $efs_ids; do
  efs_metric_json=$(aws cloudwatch get-metric-statistics \
    --namespace AWS/EFS \
    --metric-name StorageBytes \
    --dimensions "Name=FileSystemId,Value=$fs_id" "Name=StorageClass,Value=Total" \
    --start-time "$(iso_days_ago 90)" \
    --end-time "$NOW_ISO" \
    --period 2592000 \
    --statistics Average \
    --region "$REGION" \
    --output json 2>/dev/null || echo '{"Datapoints":[]}')
  efs_metric_compact=$(echo "$efs_metric_json" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)))" 2>/dev/null || echo '{"Datapoints":[]}')
  efs_trend_data="${efs_trend_data}${fs_id}|${efs_metric_compact}"$'\n'
done

efs_table=$(echo "$efs_info" | python3 -c "
import sys, json
data = json.load(sys.stdin)
data = [fs for fs in data if 'subscr' in (fs.get('Name') or '').lower()]
print('| FileSystem | Name | Size | Status |')
print('|---|---|---|---|')
for fs in data:
    size_gb = fs.get('SizeGB', 0) / 1073741824
    name = fs.get('Name', '-') or '-'
    print(f'| {fs[\"Id\"]} | {name} | {size_gb:.1f} GB | {fs[\"Status\"]} |')
" 2>/dev/null || echo "*(Failed to retrieve EFS data)*")

efs_trend_table=$(echo "$efs_trend_data" | python3 -c "
import sys, json
lines = [l.strip() for l in sys.stdin if l.strip()]
if not lines:
    print('*(No EFS trend data available)*')
    sys.exit(0)
all_trends = []
for line in lines:
    parts = line.split('|', 1)
    if len(parts) != 2:
        continue
    fs_id = parts[0]
    try:
        data = json.loads(parts[1])
    except json.JSONDecodeError:
        continue
    pts = sorted(data.get('Datapoints', []), key=lambda x: x['Timestamp'])
    if not pts:
        continue
    for i, pt in enumerate(pts):
        gb = pt['Average'] / 1073741824
        month = pt['Timestamp'][:7]
        if i > 0:
            prev_gb = pts[i-1]['Average'] / 1073741824
            pct = ((gb - prev_gb) / prev_gb) * 100 if prev_gb > 0 else 0
            change = f'{pct:+.1f}%'
        else:
            change = '-'
        all_trends.append((fs_id, month, gb, change))
if not all_trends:
    print('*(No EFS CloudWatch metric data available)*')
    sys.exit(0)
print('| FileSystem | Month | Size | Change |')
print('|---|---|---|---|')
for fs_id, month, gb, change in all_trends:
    print(f'| {fs_id} | {month} | {gb:.2f} GB | {change} |')
" 2>/dev/null || echo "*(Failed to parse EFS trend data)*")

# Log groups
log_group_table=$(aws logs describe-log-groups \
  --region "$REGION" \
  --query 'logGroups[?storedBytes>`0`].[logGroupName,storedBytes,retentionInDays]' \
  --output json 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
# Filter to project log groups only (subscr- prefix in path)
data = [r for r in data if 'subscr' in r[0]]
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

#### S3 Storage Trend (3 Months)

$s3_trend_table

### EFS

$efs_table

#### EFS Storage Trend (3 Months)

$efs_trend_table

### CloudWatch Log Groups

$log_group_table

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

---
---

# Appendices — Raw Logs

EOF

# ---------------------------------------------------------------------------
# Appendix A: RDS Logs
# ---------------------------------------------------------------------------
echo "  Writing appendices..."
{
  echo "## Appendix A: RDS Logs"
  echo ""
  echo "### A.1 Slow Query Sample"
  echo ""
  echo "$slowquery_count slow queries logged in the past 30 days."
  echo ""
  echo '```'
  cat "$APPENDIX_TMP/rds_slowquery.txt" 2>/dev/null || echo "(no data)"
  echo '```'
  echo ""
  echo "### A.2 RDS Error Log"
  echo ""
  echo "$rds_error_count error/warning entries in the past 30 days."
  echo ""
  echo '```'
  cat "$APPENDIX_TMP/rds_errors.txt" 2>/dev/null || echo "(no data)"
  echo '```'
  echo ""
} >> "$REPORT"

# ---------------------------------------------------------------------------
# Appendix B: Alarm History
# ---------------------------------------------------------------------------
{
  echo "## Appendix B: Alarm History"
  echo ""
  echo "$alarm_fires ALARM transitions in the past 30 days."
  echo ""
  echo '```'
  cat "$APPENDIX_TMP/alarm_history.txt" 2>/dev/null || echo "(no data)"
  echo '```'
  echo ""
} >> "$REPORT"

# ---------------------------------------------------------------------------
# Appendix C: Per-User Usage Breakdown
# ---------------------------------------------------------------------------
{
  echo "## Appendix C: Per-User Usage Breakdown"
  echo ""
  cat "$APPENDIX_TMP/usage_detail.txt" 2>/dev/null || echo "*(No data)*"
  echo ""
} >> "$REPORT"

echo ""
echo "Report saved to: $REPORT"
