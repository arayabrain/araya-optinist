# OptiNiSt Cloud Maintenance Procedures

This document defines the roles, responsibilities, and periodic maintenance tasks required to keep the OptiNiSt Cloud production environment healthy and reliable.

Weekly and monthly checks are automated by maintenance scripts that collect data from AWS and produce a markdown report. **Always use the scripts** — do not run the underlying CLI commands manually unless you are debugging a specific issue.

| Script | Cadence | Location |
|---|---|---|
| `weekly-maintenance.sh` | Once per week | `infrastructure/scripts/weekly-maintenance.sh` |
| `monthly-maintenance.sh` | End of each month | `infrastructure/scripts/monthly-maintenance.sh` |

```bash
# Generate the weekly report (output to current directory by default)
./infrastructure/scripts/weekly-maintenance.sh [output-dir]

# Generate the monthly report
./infrastructure/scripts/monthly-maintenance.sh [output-dir]
```

---

## Table of Contents

- [Roles and Responsibilities](#roles-and-responsibilities)
- [Periodic Maintenance Schedule](#periodic-maintenance-schedule)
- [Daily Checks](#daily-checks)
- [Weekly Checks — weekly-maintenance.sh](#weekly-checks--weekly-maintenancesh)
- [Monthly Checks — monthly-maintenance.sh](#monthly-checks--monthly-maintenancesh)
- [Quarterly Checks](#quarterly-checks)
- [Reference: CloudWatch Dashboard](#reference-cloudwatch-dashboard)
- [Reference: Log Groups and Error Patterns](#reference-log-groups-and-error-patterns)
- [Reference: Alarm Response Procedures](#reference-alarm-response-procedures)
- [Reference: Critical Alert Configuration](#reference-critical-alert-configuration)
- [Reference: AWS Cost Monitoring](#reference-aws-cost-monitoring)
- [Reference: Database Maintenance](#reference-database-maintenance)
- [Reference: Security Maintenance](#reference-security-maintenance)
- [Reference: Incident Response](#reference-incident-response)
- [Reference: Support Email Monitoring](#reference-support-email-monitoring)

---

## Roles and Responsibilities

### On-Call Maintainer (Rotating Monthly)

The on-call maintainer is the primary person responsible for system health during their rotation.

| Responsibility | Frequency | Details |
|---|---|---|
| Check support email | Daily | Respond to or triage user-reported issues |
| Run `weekly-maintenance.sh` and review report | Weekly | All weekly checks (see sections below) |
| Respond to alarms | As triggered | Follow [alarm response procedures](#reference-alarm-response-procedures) |
| Run `monthly-maintenance.sh` and review report | End of month | All monthly checks (see sections below) |
| Post monthly summary | End of month | Brief status report to team |

### Infrastructure Lead

The infrastructure lead has Terraform access and handles infrastructure-level changes.

| Responsibility | Frequency | Details |
|---|---|---|
| Review monthly report cost section | Monthly | Check cost trends, identify anomalies |
| Review monthly report storage section | Monthly | S3, EFS, and CloudWatch Log Group usage |
| Apply security patches | Monthly / as needed | OS, dependencies, Docker base images |
| Terraform plan review | Before any infra change | Review `terraform plan` and approve `terraform apply` |
| RDS maintenance windows | Quarterly | Coordinate AWS-scheduled maintenance |
| Rotate secrets | Quarterly | Update credentials in Secrets Manager |
| Capacity planning | Quarterly | Review ASG limits, instance sizing |
| Review RDS storage growth | Quarterly | Track storage trends, plan increases |
| Security audit | Quarterly | Dependencies, IAM, security groups |

---

## Periodic Maintenance Schedule

| Cadence | Task | Owner | How |
|---|---|---|---|
| **Daily** | Support email triage | On-call maintainer | Manual (see [Support Email Monitoring](#reference-support-email-monitoring)) |
| **Weekly** | Full weekly health check | On-call maintainer | Run `weekly-maintenance.sh` |
| **Monthly** | Full monthly review | On-call maintainer + Infrastructure lead | Run `monthly-maintenance.sh` |
| **Monthly** | Post monthly summary | On-call maintainer | Fill in rotation summary in monthly report |
| **Quarterly** | Rotate AWS secrets | Infrastructure lead | Manual (see [Quarterly Checks](#quarterly-checks)) |
| **Quarterly** | Review and update ASG capacity settings | Infrastructure lead | Manual |
| **Quarterly** | Review RDS storage growth | Infrastructure lead | Manual |
| **Quarterly** | Security audit (dependencies, IAM) | Infrastructure lead | Manual |

---

## Daily Checks

The on-call maintainer should perform this check once per business day (recommended: start of day, JST). This is not scripted.

### Support Email Triage

Check the designated support email inbox for:

- User-reported bugs or issues
- Account/subscription problems
- Feature requests (log and forward to team)
- Bounced emails or delivery failures

**Response SLA:**
- Critical issues (service down, data loss): respond within 4 hours
- Normal issues (bugs, questions): respond within 1 business day
- Feature requests: acknowledge within 2 business days

---

## Weekly Checks — `weekly-maintenance.sh`

Run the script once per week. It produces a markdown report with the sections below.

```bash
./infrastructure/scripts/weekly-maintenance.sh [output-dir]
# Output: weekly-maintenance-YYYY-MM-DD.md
```

After the report is generated, review each section and fill in the **Action Items** and **Notes** at the end.

### Report Section 1: Current Alarm Status

The script queries all alarms currently in `ALARM` state.

**What to look for:**
- Any alarm in `ALARM` state requires immediate attention — see [Alarm Response Procedures](#reference-alarm-response-procedures)
- Zero alarms is the expected healthy state

### Report Section 2: CloudWatch Log Review (Errors)

The script pulls `ERROR` entries from the past 7 days across three log groups (free tier, premium tier, background service) and reports the count and a sample of recent entries for each.

**What to look for:**
- Repeated exceptions or stack traces
- Database connection errors
- Authentication failures
- Workflow execution failures
- Memory or resource exhaustion messages
- See [Common Error Patterns](#common-error-patterns-to-watch) for known patterns and actions

### Report Section 3: Alarm History (Past 7 Days)

The script lists all alarm state transitions from the past week.

**What to look for:**
- Alarms that fire repeatedly may indicate an underlying issue even if they auto-resolve
- Patterns in timing (e.g., alarms during business hours only → user load; overnight → batch jobs)

### Report Section 4: ECS Service Health

The script checks all three ECS services (free tier, premium, background) and reports running vs. desired task count. Services are marked `HEALTHY` or `UNHEALTHY`.

**What to look for:**
- Any service marked `UNHEALTHY` (running != desired)
- Rollout state should be `COMPLETED` — `IN_PROGRESS` or `FAILED` indicates a deployment issue

### Report Section 5: Autoscaling Activity

The script reports ASG scaling events, autoscaling alarm transitions, and current ASG capacity for the past 7 days.

The 4 autoscaling alarms (`cpu-high`, `cpu-low`, `memory-high`, `memory-low`) do not send email notifications since they self-heal via ASG scaling. **This report section is the primary visibility for autoscaling behavior.**

**What to look for:**
- Frequent scaling events may indicate the ASG is undersized or workload is spiky
- ASG running at max capacity → consider increasing the max or using larger instances
- Failed scaling activities

### Report Section 6: Infrastructure Metrics

The script collects the most recent RDS and EFS metrics (last hour average).

| Metric | Alarm Threshold |
|---|---|
| RDS Free Storage | < 10 GB |
| RDS CPU Utilization | > 80% |
| RDS Connections | > 80 |
| EFS Burst Credits | < 1 TB |
| EFS I/O Utilization | > 80% |

**What to look for:**
- Values approaching alarm thresholds — act before the alarm fires
- Trends over several weeks (compare to previous reports)

### Report Section 7: Lambda Health

The script reports error counts for each of the 6 Lambda functions over the past 7 days.

**What to look for:**
- Any non-zero error count — investigate in the Lambda CloudWatch logs
- Rising error counts compared to previous weeks

### Report Section 8: ALB Performance (Past 7 Days)

The script reports daily request count, 5XX errors, error rate, and response times (average and max).

**What to look for:**
- 5XX error rate above 1%
- Average response times exceeding 5 seconds
- Large day-over-day request volume changes (may indicate traffic spikes or outages)

### Report Section 9: Summary

The script produces a summary table of key metrics. Fill in the **Action Items** and **Notes** sections at the end of the report before filing it.

---

## Monthly Checks — `monthly-maintenance.sh`

Run the script at the end of each month. It produces a markdown report with the sections below.

```bash
./infrastructure/scripts/monthly-maintenance.sh [output-dir]
# Output: monthly-maintenance-YYYY-MM.md
```

After the report is generated, the on-call maintainer reviews sections 1–5 and fills in section 6 (Rotation Summary) before handing off to the next on-call person.

### Report Section 1: AWS Cost Review (3-Month Trend)

The script pulls a 3-month cost breakdown by AWS service, highlights services with > 20% month-over-month increase, and shows a trend column.

**What to look for:**
- Any service flagged with a large increase
- Premium instance costs vs. utilization
- Unused or orphaned resources (unattached EBS volumes, unused Elastic IPs)
- Also check the Cost Tracking panel on the [CloudWatch dashboard](#reference-cloudwatch-dashboard)

### Report Section 2: RDS Health Check

The script reports backup status (retention, latest restorable time), current free storage, and slow query log samples.

**What to look for:**
- `LatestBackup` should be within the last 24 hours
- Free storage trending downward — the RDS instance has 20GB gp3 storage; plan to increase if below 5GB
- Slow queries that could be optimized (add indexes, refactor queries)

> **Note:** Slow query logging must be enabled in the RDS parameter group (`slow_query_log = 1`, `long_query_time = 2`). If not yet enabled, configure it via Terraform or the RDS console.

### Report Section 3: Lambda Log Review (Past 30 Days)

The script reports error counts and days-with-errors for each Lambda function over the past 30 days.

**What to look for:**
- Functions with errors on multiple days
- Rising error trends compared to previous months
- Investigate errors in the Lambda CloudWatch logs

### Report Section 4: Alarm Summary (Past 30 Days)

The script compiles a retrospective of all alarm state transitions over the past month.

**What to look for:**
- Total times alarms entered `ALARM` state
- Which unique alarms fired most frequently
- Recurring alarms may indicate an underlying issue even if they auto-resolve

### Report Section 5: Storage Overview

The script reports storage usage across S3 (main bucket), EFS file systems, and CloudWatch Log Groups.

**What to look for:**
- S3 bucket growth rate — plan lifecycle policies if needed
- EFS size trends — large working directories left by Snakemake workflows
- CloudWatch Log storage — a major cost driver; review retention periods if costs are rising

### Report Section 6: Rotation Summary (Manual)

The script generates empty tables for the on-call maintainer to fill in before handing off:

- **Support Emails** — categorized list of support emails received and their resolution status
- **Recurring Issues** — patterns observed during the rotation
- **Handoff Notes** — anything the next on-call person should know
- **Action Items** — follow-up tasks

---

## Quarterly Checks

Quarterly checks are manual and not covered by the scripts.

### 1. Secret Rotation

Rotate credentials stored in AWS Secrets Manager:

```bash
# List all OptiNiSt secrets
aws secretsmanager list-secrets \
  --filters Key=name,Values=subscr-optinist \
  --region ap-northeast-1 \
  --query 'SecretList[*].[Name,LastRotatedDate,LastChangedDate]' \
  --output table
```

**Secrets to review:**
- `subscr-optinist/database/config` — Database credentials
- `subscr-optinist/app/config` — Application secrets
- `subscr-optinist/firebase/private-key` — Firebase service account key
- `subscr-optinist/firebase/config` — Firebase configuration
- `subscr-optinist/stripe/config` — Stripe payment configuration

**Procedure:** Update the secret value in Secrets Manager, then force a new ECS deployment so containers pick up the new credentials at startup.

### 2. Capacity Planning Review

Review the Auto Scaling Group configuration and recent scaling activity:

```bash
# Current ASG settings
aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names subscr-optinist-asg \
  --region ap-northeast-1 \
  --query 'AutoScalingGroups[0].[MinSize,MaxSize,DesiredCapacity]' \
  --output table

# Recent scaling activity
aws autoscaling describe-scaling-activities \
  --auto-scaling-group-name subscr-optinist-asg \
  --region ap-northeast-1 \
  --max-items 20 \
  --query 'Activities[*].[StartTime,StatusCode,Description]' \
  --output table
```

If the ASG is frequently hitting max capacity, consider increasing the max or moving to larger instance types. Use the weekly report's autoscaling section to identify trends over the quarter.

### 3. RDS Storage Growth

```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name FreeStorageSpace \
  --dimensions Name=DBInstanceIdentifier,Value=subscr-optinist-cloud-rds \
  --start-time $(date -u -v-90d +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 604800 \
  --statistics Average \
  --region ap-northeast-1 \
  --output table
```

The RDS instance has 20GB gp3 storage. Plan to increase if free storage trends below 5GB.

### 4. Security Audit

- Review IAM roles and policies for least-privilege compliance
- Check for any unused IAM users or access keys
- Review security group rules — ensure no unnecessary ports are open
- Check for dependency vulnerabilities (`pip audit`, `npm audit`)
- Verify TLS certificates are not approaching expiration (ACM auto-renews, but verify)

---

## Reference: CloudWatch Dashboard

**Dashboard name:** `subscr-optinist-monitoring`

```
AWS Console > CloudWatch > Dashboards > subscr-optinist-monitoring
```

The dashboard contains the following widget groups. Here is what to look for in each:

| Widget | Key Metrics | Warning Signs |
|---|---|---|
| Free vs Premium CPU & Memory | CPU/Memory % for both tiers | Sustained > 70% CPU or > 85% memory |
| Autoscaling Capacity | Desired vs. running instance count | Frequent scaling, stuck pending instances |
| ECS Service Metrics | Per-service CPU and memory | Large divergence between free and premium |
| Cost Tracking | EC2/ECS/ALB costs, instance counts | Sudden cost spikes, underutilized premium instances |
| ALB Performance | Request count, response time, 2XX/5XX | 5XX spike, response time > 5s |
| User Tier Operations | Active free/premium users, Lambda metrics | Lambda errors, low utilization |
| EC2 Performance | CPU, I/O wait, load average | I/O wait > 30%, load average > 80% |
| RDS & EFS Health | RDS CPU, connections, storage; EFS I/O | RDS CPU > 80%, connections > 80%, EFS I/O > 80% |
| Autoscaling Activity | Instance lifecycle (total, pending, terminating) | Instances stuck in pending/terminating |
| Autoscaling Triggers | CPU & memory vs. scale thresholds | Oscillating near thresholds (thrashing) |
| Background Jobs | Background service CPU/memory, user manager | Background CPU/memory sustained high |
| Lambda Operations | Duration and errors for all Lambda functions | Rising error counts, increasing duration |
| Alarm Status Overview | All 19 alarms at a glance | Any alarm in red (ALARM state) |

---

## Reference: Log Groups and Error Patterns

### Log Groups

| Log Group | Retention | Contents |
|---|---|---|
| `/ecs/subscr-optinist-cloud-taskdef` | 365 days | Free-tier application logs |
| `/ecs/subscr-premium-optinist-cloud-taskdef` | 365 days | Premium-tier application logs |
| `/ecs/subscr-background-optinist-cloud-taskdef` | 14 days | Background job service logs |
| `/aws/rds/instance/subscr-optinist-cloud-rds/error` | Per RDS config | RDS error logs |
| `/aws/rds/proxy/subscr-optinist-rds-proxy` | Per RDS config | RDS Proxy logs |

### Common Error Patterns to Watch

| Pattern | Possible Cause | Action |
|---|---|---|
| `ConnectionRefusedError` | RDS Proxy or database down | Check RDS status, proxy health |
| `MemoryError` or `OOMKilled` | Container out of memory | Check task definition memory limits, user workload size |
| `TimeoutError` on DB queries | Slow queries or DB overload | Review slow query log, check RDS CPU |
| `FirebaseError` | Firebase auth issues | Verify Firebase service account key in Secrets Manager |
| `botocore.exceptions` | AWS API errors | Check IAM permissions, service quotas |
| `StripeError` | Payment processing issues | Check Stripe dashboard, webhook configuration |
| Repeated `5XX` in ALB logs | Application crashes | Check ECS task health, recent deployment |

---

## Reference: Alarm Response Procedures

When an alarm fires, follow the procedure for that alarm category.

### ECS / Autoscaling Alarms

| Alarm | Threshold | Action |
|---|---|---|
| `subscr-optinist-cpu-high` | ECS CPU > 60% (2 periods) | Auto-triggers scale-up. Monitor if scaling resolves the issue. If sustained at max capacity, consider increasing ASG max. |
| `subscr-optinist-cpu-low` | ECS CPU < 20% (2 periods) | Auto-triggers scale-down. Informational only. |
| `subscr-optinist-memory-high` | ECS Memory > 80% (3 periods) | Auto-triggers scale-up. If sustained, check for memory leaks in application logs. |
| `subscr-optinist-memory-low` | ECS Memory < 10% (3 periods) | Auto-triggers scale-down. Informational only. |

### EC2 Alarms

| Alarm | Threshold | Action |
|---|---|---|
| `subscr-optinist-load-average-high` | EC2 CPU > 80% (2 periods) | Check if user workloads are unusually heavy. May need larger instances. |
| `subscr-optinist-high-iowait` | I/O wait > 30% (2 periods) | Check EFS throughput, disk-heavy operations. Consider EFS provisioned throughput. |
| `subscr-optinist-ebs-queue-length-high` | EBS I/O queue > 8 (2 of 3 min) | Disk saturation — check for heavy Snakemake I/O. Sustained saturation can fail health checks and evict the instance. |

### RDS Alarms

| Alarm | Threshold | Action |
|---|---|---|
| `subscr-optinist-rds-cpu-high` | CPU > 80% (2 periods) | Review slow query log. Consider RDS instance upgrade. |
| `subscr-optinist-rds-connections-high` | Connections > 80 (2 periods) | Check for connection leaks. Review RDS Proxy settings. |
| `subscr-optinist-rds-storage-low` | Free storage < 10 GB | **URGENT.** Increase RDS storage allocation immediately via Terraform or AWS Console. |

### EFS Alarms

| Alarm | Threshold | Action |
|---|---|---|
| `subscr-optinist-efs-burst-credits-low` | Credits < 1 TB | Reduce I/O-heavy workloads or switch to provisioned throughput mode. |
| `subscr-optinist-efs-throughput-high` | I/O limit > 80% (2 periods) | Consider provisioned throughput or offloading large files to S3. |

### ALB Alarms

| Alarm | Threshold | Action |
|---|---|---|
| `subscr-optinist-alb-5xx-errors` | >= 20 5XX errors in 5 min | **HIGH PRIORITY.** A sustained error storm. Check ECS task health, recent deployments, application logs. |
| `subscr-optinist-free-tg-response-time-high` | p95 > 10s for 25 of 30 min | Persistent (not transient) latency. Check RDS, ECS utilization, heavy workloads. |
| `subscr-optinist-public-tg-response-time-high` | p95 > 5s for 25 of 30 min | Sustained SPA/public-dataview latency. Check public instance health and cold-cache reads. |
| `subscr-optinist-free-tg-unhealthy-hosts` | UnHealthyHostCount > 0 | Free workflow instance failing health checks. Check ECS task and EBS I/O. |
| `subscr-optinist-public-tg-unhealthy-hosts` | UnHealthyHostCount > 0 | SPA delivery / public-dataview at risk. Check public ASG instances. |

### Background Service Alarms

| Alarm | Threshold | Action |
|---|---|---|
| `subscr-background-task-stopped` | Running tasks < 1 (2 periods) | **CRITICAL.** Background jobs are not running. Check ECS service events, force new deployment if needed. |
| `subscr-background-cpu-high` | CPU > 400 units (3 periods) | Background jobs are overloaded. Check for stuck or excessively large jobs. |
| `subscr-background-memory-high` | Memory > 600 MB (3 periods) | Check for memory leaks in background job code. |

### Account Cost Alarm

| Alarm | Threshold | Action |
|---|---|---|
| `subscr-monthly-cost-high` | Projected monthly spend > `var.monthly_budget_usd` | Review cost trend. Check for orphaned or idle resources. |

### Premium Alarms

| Alarm | Threshold | Action |
|---|---|---|
| `subscr-premium-cpu-high` | CPU > 80% (2 periods) | Check premium user workload. May need larger instance type. |
| `subscr-premium-memory-high` | Memory > 85% (3 periods) | Check for memory-intensive workflows. Review premium task definition limits. |
| `subscr-premium-<user_id>-tg-unhealthy-hosts` | UnHealthyHostCount > 0 | Created/deleted per-user by the premium-manager Lambda. That user's dedicated instance is failing health checks. |

---

## Reference: Critical Alert Configuration

Critical CloudWatch alarms send email notifications to `optinist-support@araya.org` via an SNS topic (`subscr-optinist-critical-alerts`, defined in `monitoring.tf`). Most alarms wire both `alarm_actions` and `ok_actions`, so the team is notified when an alarm fires and when it recovers. The ALB 5XX and response-time alarms wire only `alarm_actions` (no recovery email) and treat missing data as not-breaching, to suppress the OK/INSUFFICIENT_DATA flapping that sparse traffic would otherwise generate.

**Note:** After the initial `terraform apply`, AWS sends a confirmation email to the subscription endpoint. The subscription must be confirmed before alerts are delivered.

### Alarms with email notifications

**Critical — service or data at risk:**

| Alarm | File | Why |
|---|---|---|
| `subscr-background-task-stopped` | `background_service.tf` | Background jobs stop entirely — user workflows affected |
| `subscr-optinist-rds-storage-low` | `monitoring.tf` | Database could become read-only if storage is exhausted |
| `subscr-optinist-rds-cpu-high` | `monitoring.tf` | Database overloaded, queries slow or timing out |
| `subscr-optinist-rds-connections-high` | `monitoring.tf` | Connection exhaustion → application errors |
| `subscr-optinist-alb-5xx-errors` | `monitoring.tf` | Sustained 5XX storm — users seeing server errors |
| `subscr-optinist-free-tg-response-time-high` | `monitoring.tf` | Persistent high latency on the free tier |
| `subscr-optinist-public-tg-response-time-high` | `monitoring.tf` | Persistent high latency on the public tier |
| `subscr-optinist-free-tg-unhealthy-hosts` | `monitoring.tf` | Free workflow instance down |
| `subscr-optinist-public-tg-unhealthy-hosts` | `monitoring.tf` | SPA delivery / public-dataview at risk |
| `subscr-optinist-ebs-queue-length-high` | `monitoring.tf` | Disk saturation can fail health checks and evict the instance |
| `subscr-optinist-load-average-high` | `monitoring.tf` | EC2 hosts saturated, all services degrade |
| `subscr-monthly-cost-high` | `monitoring.tf` | Unexpected cost spike |

**High — degraded service:**

| Alarm | File | Why |
|---|---|---|
| `subscr-optinist-high-iowait` | `monitoring.tf` | I/O bottleneck, workflows stall |
| `subscr-optinist-efs-burst-credits-low` | `monitoring.tf` | EFS will throttle, Snakemake workflows break |
| `subscr-optinist-efs-throughput-high` | `monitoring.tf` | EFS approaching I/O limit |
| `subscr-background-cpu-high` | `background_service.tf` | Background jobs delayed |
| `subscr-background-memory-high` | `background_service.tf` | Background service may OOM |
| `subscr-premium-cpu-high` | `premium_manager.tf` | Premium users degraded |
| `subscr-premium-memory-high` | `premium_manager.tf` | Premium service may OOM |

**Not wired (self-healing via autoscaling):**

| Alarm | Why no email |
|---|---|
| `subscr-optinist-cpu-high` / `cpu-low` | Triggers ASG scale up/down automatically |
| `subscr-optinist-memory-high` / `memory-low` | Triggers ASG scale up/down automatically |

---

## Reference: AWS Cost Monitoring

### Expected Monthly Costs (Approximate)

| Service | Purpose | Approximate Cost |
|---|---|---|
| EC2 (Free tier ASG) | ECS container instances (t3.large, 1-3) | $60-$180 |
| EC2 (Premium instances) | Dedicated premium user instances | Variable |
| EC2 (NAT instances) | 2x t3.nano for private subnet egress | ~$8 |
| EC2 (Background service) | t3.micro for background jobs | ~$8 |
| RDS | MySQL t4g.small, 20GB gp3 | ~$30 |
| ALB | Application Load Balancer | ~$20 + data |
| EFS | Snakemake workflow cache | Variable (usage-based) |
| S3 | Application storage bucket | Variable (usage-based) |
| Lambda | 6 functions (managers, cleanup, cost tracker) | Minimal (< $5) |
| CloudWatch | Logs (365-day retention), metrics, dashboard | ~$10-20 |
| VPC Endpoints | 5 interface endpoints | ~$35 |
| Route53 | DNS hosted zone | ~$0.50 |
| ACM | SSL/TLS certificate | Free |
| Secrets Manager | 5 secrets | ~$2 |

**Cost alerts:** The `subscr-monthly-cost-high` alarm triggers when projected monthly spend exceeds `var.monthly_budget_usd` (set in `terraform.tfvars`).

---

## Reference: Database Maintenance

### Automated (No Action Required)

- **Backups:** Automated daily snapshots with 35-day retention
- **Encryption:** Enabled at rest
- **SSL:** Required for all connections (via RDS Proxy)
- **Enhanced Monitoring:** 60-second interval

### Manual Checks

- **Monthly:** Review slow query log for optimization opportunities (covered by `monthly-maintenance.sh` section 2)
- **Quarterly:** Check storage growth trend and plan increases (see [Quarterly Checks](#3-rds-storage-growth))
- **As needed:** If connection count alarms fire, review application connection pooling

### Emergency: RDS Storage Full

If `subscr-optinist-rds-storage-low` fires:

1. Check current usage:
   ```bash
   # Check allocated storage
   aws rds describe-db-instances \
     --db-instance-identifier subscr-optinist-cloud-rds \
     --region ap-northeast-1 \
     --query 'DBInstances[0].[DBInstanceIdentifier,AllocatedStorage,DBInstanceStatus]' \
     --output table

   # Check free storage (CloudWatch metric)
   aws cloudwatch get-metric-statistics \
     --namespace AWS/RDS \
     --metric-name FreeStorageSpace \
     --dimensions Name=DBInstanceIdentifier,Value=subscr-optinist-cloud-rds \
     --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%SZ) \
     --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
     --period 300 \
     --statistics Average \
     --region ap-northeast-1 \
     --output table
   ```
2. Increase storage (can be done without downtime):
   ```bash
   aws rds modify-db-instance \
     --db-instance-identifier subscr-optinist-cloud-rds \
     --allocated-storage 40 \
     --apply-immediately \
     --region ap-northeast-1
   ```
3. Update Terraform to match the new value to prevent drift.

### Emergency: Restore the Database from a Snapshot

**A snapshot restore is the only way back from a corrupted or mis-migrated database, and from a bad engine
upgrade.** There is no engine downgrade: **the engine version is a property of the snapshot** —
`RestoreDBInstanceFromDBSnapshot` has no engine-version parameter — so restoring a pre-change snapshot is the
rollback, whatever the change was.

Three things to know before starting, all measured rather than assumed:

| | |
|---|---|
| **A restore creates a new instance** | It does not modify the existing one. The original identifier has to be **freed first**, or the restore has to land on a temporary identifier and be renamed afterwards |
| **The identifier carries the ARN and the endpoint hostname; `DbiResourceId` does not** | Restoring onto the original identifier reproduces the original endpoint, so nothing that resolves the hostname needs reconfiguring. **`DbiResourceId` is the one attribute that moves**, and that is what forces the proxy re-registration in step 6 |
| **The snapshot does not carry the instance's configuration** | Instance class, storage type, subnet group, security groups and parameter group are **restore arguments**, not snapshot contents. Read them off the instance before deleting it (step 3); the declared values are in `infrastructure/terraform/infrastructure.tf` |

**Measured durations**, from the rehearsal drill: **about 13 min 30 s end to end — 5 minutes for the delete,
8 min 30 s for the restore.** Creating a fresh manual snapshot first adds about a minute. Budget the
announcement against those figures, and say in it that **a rollback loses every write made since the
snapshot**.

**For a bad engine upgrade**, follow this runbook from
[RDS_ENGINE_UPGRADE_PROCEDURE.md](RDS_ENGINE_UPGRADE_PROCEDURE.md)'s Rollback section, which supplies the
outgoing-family parameter group to attach and the Terraform revert to run before step 7.

```bash
export AWS_PROFILE=<the profile for this account>
export AWS_REGION=ap-northeast-1
ENV=<the Terraform var.environment value: development or subscr>
DB=${ENV}-optinist-cloud-rds
RESTORE_PG=          # leave empty unless the restored version needs a parameter group of
                     # another family; an engine-upgrade rollback sets it
```

**Step 1. Choose the restore source and confirm it.**

```bash
# EVERY manual snapshot, newest first. An empty filtered result and an empty snapshot
# list mean different things, so list them all rather than filtering.
aws rds describe-db-snapshots --db-instance-identifier "$DB" --snapshot-type manual \
  --query 'reverse(sort_by(DBSnapshots,&SnapshotCreateTime))[].{
             id:DBSnapshotIdentifier,ev:EngineVersion,status:Status,
             created:SnapshotCreateTime}' --output table

# The fallback: automated backups, which also cover point-in-time recovery.
aws rds describe-db-snapshots --db-instance-identifier "$DB" --snapshot-type automated \
  --query 'reverse(sort_by(DBSnapshots,&SnapshotCreateTime))[:3].{
             id:DBSnapshotIdentifier,ev:EngineVersion,created:SnapshotCreateTime}' \
  --output table

RESTORE_FROM=<the chosen snapshot id>
```

**Prefer a manual snapshot: its contents are known and it survives an instance delete.** Verify the engine
version on the row you pick — a snapshot restores the version it was taken on, which is the whole mechanism.

**Step 2. Stop application traffic, and preserve the evidence.** Nothing should write to an instance that is
about to be replaced. Scaling to zero does not hold on its own: the manager Lambdas re-scale on their own
schedules, so disable their EventBridge rules first, as the development scheduler's own stop path does.

> `for R in $(echo "$RULES")`, never `for R in $RULES`: zsh does not word-split an unquoted parameter
> expansion, so the bare form iterates once over the whole string — and in step 7 that would set the *first*
> service to the *last* service's count.

```bash
# Record the current state as "name=count" pairs that step 7 replays. The counts are NOT
# all 1. WRITE THIS LINE DOWN alongside RULES: a restore can outlive the shell it started in.
COUNTS=$(aws ecs describe-services --cluster "${ENV}-optinist-cloud-cluster" \
  --services "${ENV}-optinist-cloud-service" "${ENV}-premium-optinist-cloud-service" \
             "${ENV}-background-optinist-cloud-service" "${ENV}-public-optinist-cloud-service" \
  --query 'services[].[serviceName,desiredCount]' --output text | awk '{print $1"="$2}' | tr '\n' ' ')
echo "COUNTS=$COUNTS"
# Expected: four name=count pairs.

# DERIVE the rule list from the scheduler Lambda rather than transcribing it: a hand-copied
# list drifts, and the rule most easily missed, free-manager-asg-events, is triggered BY
# scaling ECS to zero. Production has no scheduler, so fall back to the rules that exist.
RULES=$(aws lambda get-function-configuration --function-name "${ENV}-dev-scheduler" \
  --query 'Environment.Variables.[SCHEDULE_RULE_NAMES,DELAYED_RULE_NAMES]' --output text 2>/dev/null \
  | tr '\t' '\n' | python3 -c 'import json,sys;print(" ".join(n for l in sys.stdin for n in json.loads(l)))')
[ -n "$RULES" ] || RULES=$(aws events list-rules --name-prefix "${ENV}-" \
  --query "Rules[?contains(Name,'manager')||contains(Name,'cleanup')||contains(Name,'tracker')].Name" \
  --output text | tr '\t' ' ')
echo "RULES=$RULES"
# Expected: five names on development - free-manager-schedule, free-manager-asg-events,
# cost-tracker-schedule, premium-manager-schedule, premium-cleanup-schedule. WRITE THIS
# LINE DOWN: step 7 re-enables exactly this set.

for R in $(echo "$RULES"); do aws events disable-rule --name "$R" && echo "disabled $R"; done
for R in $(echo "$RULES"); do aws events describe-rule --name "$R" --query '[Name,State]' --output text; done
# Expected: DISABLED for every line. A rule missed here is a service that scales back up
# mid-restore.

for S in "${ENV}-optinist-cloud-service" "${ENV}-premium-optinist-cloud-service" \
         "${ENV}-background-optinist-cloud-service" "${ENV}-public-optinist-cloud-service"; do
  aws ecs update-service --cluster "${ENV}-optinist-cloud-cluster" --service "$S" \
    --desired-count 0 --query 'service.{name:serviceName,desired:desiredCount}'
done
# Expected: desired 0 for all four, and still 0 a few minutes later. If one has come
# back, a rule is still enabled.
```

**Step 3. Read the restore parameters off the instance, while it still exists.**

```bash
read -r CLASS STORAGE SUBNET SG <<<"$(aws rds describe-db-instances \
  --db-instance-identifier "$DB" --output text \
  --query 'DBInstances[0].[DBInstanceClass,StorageType,DBSubnetGroup.DBSubnetGroupName,
           VpcSecurityGroups[0].VpcSecurityGroupId]')"
echo "$CLASS $STORAGE $SUBNET $SG"
# Expected: four non-empty values. They are unavailable once the instance is gone.
```

**Step 4. Clear deletion protection — production only.** `DeleteDBInstance` is **refused outright** while
protection is on, so a legitimate delete stops here with nothing but an error message to explain why.

```bash
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].DeletionProtection'
# Expected: true on production, false on development. Only continue if it said true.

aws rds modify-db-instance --db-instance-identifier "$DB" \
  --no-deletion-protection --apply-immediately --query 'DBInstance.DBInstanceIdentifier'
aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].DeletionProtection'
# Expected: false. Confirm it rather than assuming.
```

**Terraform puts it back on the next apply**, because `deletion_protection` is declared — so there is no
separate re-enable step to forget. If the restore is abandoned before that apply, re-enable it by hand.

**Step 5. Free the identifier, then restore under it.**

```bash
date -u
# The final snapshot IS the diagnostic copy of the broken instance. Keep the automated
# backups too: --no-delete-automated-backups, because the flag DEFAULTS TO TRUE and a
# delete written without it takes the point-in-time window with the instance.
aws rds delete-db-instance --db-instance-identifier "$DB" \
  --final-db-snapshot-identifier "${DB}-broken-$(date +%Y%m%d-%H%M)" \
  --no-delete-automated-backups
aws rds wait db-instance-deleted --db-instance-identifier "$DB"

# An instance cannot attach a group from another engine family, and the restore fails on
# that argument AFTER it has been committed - hence RESTORE_PG, set at the top.
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier "$DB" --db-snapshot-identifier "$RESTORE_FROM" \
  --db-instance-class "$CLASS" --storage-type "$STORAGE" --port 3306 \
  --db-subnet-group-name "$SUBNET" --vpc-security-group-ids "$SG" \
  ${RESTORE_PG:+--db-parameter-group-name "$RESTORE_PG"} \
  --no-publicly-accessible --no-multi-az
aws rds wait db-instance-available --db-instance-identifier "$DB"
date -u

aws rds describe-db-instances --db-instance-identifier "$DB" \
  --query 'DBInstances[0].{ev:EngineVersion,endpoint:Endpoint.Address,rid:DbiResourceId,
           pg:DBParameterGroups[0].DBParameterGroupName,
           pgs:DBParameterGroups[0].ParameterApplyStatus,
           retention:BackupRetentionPeriod}'
# Expected: the snapshot's engine version, the ORIGINAL endpoint hostname, a NEW
# DbiResourceId, the parameter group in-sync, and retention unchanged. A changed endpoint
# means the restore did not land on the original identifier - stop and rename before
# continuing.
```

**Step 6. Re-register the RDS Proxy target.** The restored instance has a new `DbiResourceId`, and **the proxy
does not pick up a replacement on its own.** On development the scheduler's next start would do this through
`ensure_rds_proxy_target()`, but a restore should not wait until morning; on production it is manual and
load-bearing.

```bash
aws rds register-db-proxy-targets --db-proxy-name "${ENV}-optinist-rds-proxy" \
  --target-group-name default --db-instance-identifiers "$DB"
aws rds describe-db-proxy-targets --db-proxy-name "${ENV}-optinist-rds-proxy" \
  --query '{found:length(Targets),
            targets:Targets[].{id:RdsResourceId,state:TargetHealth.State}}'
# Expected: found == 1, AVAILABLE. REGISTERING for a minute or two is normal.
```

**Step 7. Bring traffic back, and prove the database serves.** The control plane reporting the right version,
endpoint and parameter group says nothing about whether the database answers: **a restore with correct
metadata and an unreachable database is a failed restore.**

```bash
# If the shell from step 2 is gone, redeclare both from the lines written down there. An
# empty value makes the loops run zero times and print nothing, so refuse rather than proceed.
[ -n "$COUNTS" ] && [ -n "$RULES" ] || {
  echo "ABORT: COUNTS and RULES must be set - redeclare them from step 2's output" >&2; }

# Restore each service to ITS OWN recorded count, with fresh tasks so no connection pool
# is left holding a dead connection.
for P in $(echo "$COUNTS"); do
  aws ecs update-service --cluster "${ENV}-optinist-cloud-cluster" \
    --service "${P%%=*}" --desired-count "${P##*=}" --force-new-deployment >/dev/null \
    && echo "restored ${P%%=*} to ${P##*=}"
done

# Re-enable every rule disabled in step 2. Leaving one disabled silently stops
# autoscaling, premium assignment cleanup or cost metrics, with no alarm for it.
for R in $(echo "$RULES"); do aws events enable-rule --name "$R" && echo "enabled $R"; done
for R in $(echo "$RULES"); do aws events describe-rule --name "$R" --query '[Name,State]' --output text; done
# Expected: ENABLED for every one, and the count matches what step 2 disabled.

aws ecs describe-services --cluster "${ENV}-optinist-cloud-cluster" \
  --services "${ENV}-optinist-cloud-service" "${ENV}-premium-optinist-cloud-service" \
             "${ENV}-background-optinist-cloud-service" "${ENV}-public-optinist-cloud-service" \
  --query 'services[].{name:serviceName,desired:desiredCount,running:runningCount,
           rollout:deployments[0].rolloutState}'
# Expected: running == desired, COMPLETED, at the recorded counts.

curl -fsS "<the environment's base URL>/api/health" && echo
# Expected: a healthy response through the application, which reaches the database over
# TLS through the proxy.
```

**Afterwards: check Terraform converges.** Run `terraform plan` and confirm `aws_db_instance.main` shows
`update` or no change. **A create or delete there would destroy the instance just restored**, so anything
else aborts. Each delete that retained its automated backups leaves its own set, distinguished only by
`DbiResourceId`, so a later cleanup has more than one to handle.

---

## Reference: Security Maintenance

### Monthly

- Check for and apply Docker base image updates (Python 3.11-slim)
- Review GitHub security alerts (Dependabot)
- Run `pip audit` on Python dependencies
- Run `npm audit` on frontend dependencies

### Quarterly

- Rotate credentials in Secrets Manager (database, AWS keys, app secrets)
- Review IAM roles and policies — remove unused permissions
- Review security group rules — ensure only necessary ports are open
- Verify ACM certificates are auto-renewing
- Check VPC endpoint policies

### As Needed

- Apply critical security patches immediately (hotfix procedure in DEPLOYMENT_PROCEDURE.md)
- Respond to AWS security advisories

---

## Reference: Incident Response

### Severity Levels

| Level | Definition | Response Time | Examples |
|---|---|---|---|
| **P1 - Critical** | Service completely down or data at risk | Immediate (within 30 min) | Application unreachable, database corruption, security breach |
| **P2 - High** | Major feature broken, many users affected | Within 2 hours | Login failures, workflow execution broken, 5XX errors |
| **P3 - Medium** | Minor feature broken, workaround exists | Within 1 business day | UI bug, slow performance, non-critical error |
| **P4 - Low** | Cosmetic or enhancement request | Next sprint | Typo, minor UI issue, feature request |

### Incident Steps

1. **Detect** — Alert fires, user report, or daily check
2. **Acknowledge** — On-call maintainer claims the incident
3. **Diagnose** — Check dashboard, logs, and recent deployments
4. **Mitigate** — Restore service (rollback, restart, scale up)
5. **Resolve** — Fix root cause (code fix, config change)
6. **Review** — Post-incident summary for the team

### Quick Mitigation Commands

```bash
# Force restart of free-tier ECS service
aws ecs update-service \
  --cluster subscr-optinist-cloud-cluster \
  --service subscr-optinist-cloud-service \
  --force-new-deployment \
  --region ap-northeast-1

# Force restart of premium ECS service
aws ecs update-service \
  --cluster subscr-optinist-cloud-cluster \
  --service subscr-premium-optinist-cloud-service \
  --force-new-deployment \
  --region ap-northeast-1

# Force restart of background service
aws ecs update-service \
  --cluster subscr-optinist-cloud-cluster \
  --service subscr-background-optinist-cloud-service \
  --force-new-deployment \
  --region ap-northeast-1

# Health check
curl -s -o /dev/null -w "%{http_code}" https://araya-optinist.com/health
```

---

## Reference: Support Email Monitoring

**Support email:** `optinist-support@araya.org` (requires SNS confirmation after terraform apply)

Critical CloudWatch alarms are configured to send notifications to this address via SNS (see [Critical Alert Configuration](#reference-critical-alert-configuration)).

Remaining setup:

1. Ensure multiple team members have access to the inbox
2. Set up email forwarding rules for critical keywords (e.g., "ALARM", "down", "error")

### Triage Procedure

When a support email arrives:

1. **Categorize** — Is it a bug report, feature request, account issue, or system alert?
2. **Prioritize** — Critical (service affecting), Normal (bug/question), Low (feature request)
3. **Assign** — On-call maintainer handles critical/normal; feature requests go to backlog
4. **Respond** — Acknowledge receipt within the SLA timeframe
5. **Track** — Create a GitHub issue for bugs; log feature requests
