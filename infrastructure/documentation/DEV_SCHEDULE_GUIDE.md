# Development Environment Schedule Guide

The dev environment automatically starts and stops on a schedule to save costs
(~46% reduction in compute spend).

## Schedule

| | Start | Stop |
|---|---|---|
| **Monday - Friday** | 08:00 JST (23:00 UTC prev day) | 22:00 JST (13:00 UTC) |
| **Saturday - Sunday** | Off | Off |

The environment is completely stopped from **Friday 22:00 JST** until **Monday 08:00 JST**.

All resources start at 08:00 JST. RDS is restored from a snapshot — the slowest step
(~10-15 min) — so the database is typically ready by ~08:15. A verify-start pass runs at
08:15 JST to finish anything deferred during the initial start (notably registering the
RDS Proxy target once the database is available) and to enable the delayed scaling rules.

## What Gets Started/Stopped

| Resource | Start Action | Stop Action |
|---|---|---|
| RDS (MySQL) | `restore_db_instance_from_db_snapshot` | `delete_db_instance` (with final snapshot) |
| NAT instance | `start_instances` | `stop_instances` |
| Background service instance | `start_instances` | `stop_instances` |
| Premium instances | `start_instances` | `stop_instances` |
| Free tier ASG | Set min=1, desired=1 | Set min=0, desired=0 |
| Lambda schedules (5 rules) | Enable | Disable |
| CloudWatch alarm actions | Enable | Disable |

The **ALB stays running** at all times (cannot be stopped; fixed cost ~$16-22/month).

### Important: RDS is destroyed during off-hours

Unlike other resources which are simply stopped, the **RDS instance is fully deleted**
each evening (with a snapshot taken automatically). It is restored from that snapshot
the next morning. This eliminates AWS's 7-day forced restart, but has implications:

- **Do NOT run `terraform apply` during off-hours or weekends.** Terraform will see the
  missing RDS instance and try to create a fresh one (empty database). Wait until the
  scheduler has restored the instance first. If you must apply during off-hours, either
  start the environment manually first, or use `-target` to exclude the RDS resource.
- **Do NOT manually delete the snapshot** named `<identifier>-dev-scheduler`. It is the
  only copy of the database while the instance is destroyed. If deleted, the next
  morning's restore will fail.
- **Morning restore takes ~10-15 minutes** (longer than a simple RDS start). The
  instance may not be ready until ~08:15 even though the Lambda fires at 08:00.
- **RDS configuration changes** (instance class, parameter group, security groups, etc.)
  in Terraform also need to be reflected in the Lambda's environment variables, otherwise
  the restored instance will use stale settings. The env vars are already wired via
  Terraform references, so a normal `terraform apply` keeps them in sync — but be aware
  if you change RDS config manually in the console.

## Working After Hours

> All `aws lambda invoke` examples below pass `--cli-binary-format raw-in-base64-out`.
> AWS CLI v2 decodes `--payload` as base64 by default; without this flag the raw JSON
> is rejected with `Invalid base64`.

### Option 1: Skip the Next Stop (Working Late)

If you need to keep the environment running past 22:00 JST, set an override
with a duration **before** 22:00:

```bash
# Keep running for 3 more hours
aws lambda invoke \
  --function-name development-dev-scheduler \
  --payload '{"action":"override","hours":3}' \
  --cli-binary-format raw-in-base64-out \
  --region ap-northeast-1 \
  /dev/stdout
```

The override **automatically expires** after the specified hours (max 12h).
No need to remember to turn it off — it self-clears.

The next morning's start also clears any leftover override.

### Option 2: Manual Start (Weekends / After Hours)

To start the environment outside scheduled hours:

```bash
aws lambda invoke \
  --function-name development-dev-scheduler \
  --payload '{"action":"start"}' \
  --cli-binary-format raw-in-base64-out \
  --region ap-northeast-1 \
  /dev/stdout
```

The environment will stay running until the next scheduled stop (or until you
manually stop it).

### Option 3: Manual Stop

To stop the environment immediately:

```bash
aws lambda invoke \
  --function-name development-dev-scheduler \
  --payload '{"action":"stop"}' \
  --cli-binary-format raw-in-base64-out \
  --region ap-northeast-1 \
  /dev/stdout
```

This respects the override — if an active override hasn't expired yet, the
stop will be skipped. Clear it first if you want to force a stop:

```bash
aws ssm put-parameter \
  --name /development/optinist/schedule-override \
  --value off \
  --type String \
  --overwrite \
  --region ap-northeast-1
```

## Checking Current State

### Is the environment running?

```bash
# Check ASG desired capacity (1 = running, 0 = stopped)
aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names development-optinist-asg \
  --query 'AutoScalingGroups[0].{Desired:DesiredCapacity,Running:length(Instances[?LifecycleState==`InService`])}' \
  --region ap-northeast-1 \
  --output table
```

### Is the override active?

The value is a UTC expiry timestamp (e.g., `2026-03-18T16:00:00Z`) or `off`:


```bash
aws ssm get-parameter \
  --name /development/optinist/schedule-override \
  --query 'Parameter.Value' \
  --region ap-northeast-1 \
  --output text
```

### Check Lambda logs for the last start/stop

```bash
aws logs tail /aws/lambda/development-dev-scheduler \
  --since 1d \
  --region ap-northeast-1
```

## Terraform Configuration

The schedule is controlled by a single variable in `development.tfvars`:

```hcl
enable_dev_schedule = true
```

Setting this to `false` and running `terraform apply` will remove all schedule
resources and the environment will run 24/7 again.

Production (`subscr`) is unaffected -- the variable defaults to `false`.

## Troubleshooting

### Environment didn't start in the morning

1. Check the Lambda logs (see above)
2. Verify the EventBridge rules are enabled:
   ```bash
   aws events describe-rule \
     --name development-dev-schedule-start \
     --region ap-northeast-1 \
     --query 'State'
   ```
3. Try a manual start (see above)

### Environment didn't stop in the evening

1. Check if the override is set to "on" (see above)
2. Check the Lambda logs
3. Try a manual stop (see above)

### RDS 7-day auto-start (solved)

AWS automatically restarts stopped RDS instances after 7 days. This was a problem
during extended breaks (Golden Week, year-end holidays). The scheduler now **destroys**
the RDS instance (with a final snapshot) on stop and **restores** it from that snapshot
on start. Since the instance doesn't exist while stopped, AWS cannot auto-restart it.

The RDS Proxy target automatically reconnects when the instance is restored with the
same identifier -- no reconfiguration needed.

### Snapshot not found on start

If the restore fails with `snapshot_not_found`, the snapshot may have been manually
deleted. Check available snapshots:

```bash
aws rds describe-db-snapshots \
  --db-instance-identifier development-optinist-cloud-rds \
  --query 'DBSnapshots[?contains(DBSnapshotIdentifier, `dev-scheduler`)].[DBSnapshotIdentifier,Status]' \
  --region ap-northeast-1 \
  --output table
```

If no scheduler snapshot exists, you can create one manually from a recent automated
backup, or re-create the RDS instance via Terraform.

### Terraform drift after RDS destroy

While the RDS instance is destroyed (during off-hours), `terraform plan` will show
the instance as needing to be created. This is expected. Once the scheduler restores
the instance on the next start, `terraform plan` should show no changes (the instance
is restored with the same identifier and configuration).

### Lambda schedules are disabled after a stop

This is expected. The stop action disables the free_manager, premium_manager,
premium_cleanup, cost_tracker, and free_manager_asg_events rules to prevent
them from re-scaling resources during off-hours. The next start action
re-enables them all.

If you need to manually re-enable them:

```bash
for rule in \
  development-free-manager-schedule \
  development-free-manager-asg-events \
  development-premium-manager-schedule \
  development-premium-cleanup-schedule \
  development-cost-tracker-schedule; do
  aws events enable-rule --name "$rule" --region ap-northeast-1
done
```

## Cost Impact

| Scenario | Monthly Compute Cost | Savings |
|---|---|---|
| 24/7 operation | ~$200-216 | - |
| Weekday 08:00-22:00 JST only (14h/day, 5 days) | ~$89-94 | ~$111-122 (~54%) |

Note: ALB (~$16-22/month) runs continuously and is excluded from savings.
