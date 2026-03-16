# Development Environment Schedule Guide

The dev environment automatically starts and stops on a schedule to save costs
(~46% reduction in compute spend).

## Schedule

| | Start | Stop |
|---|---|---|
| **Monday - Friday** | 08:45 JST (23:45 UTC prev day) | 22:00 JST (13:00 UTC) |
| **Saturday - Sunday** | Off | Off |

The environment is completely stopped from **Friday 22:00 JST** until **Monday 08:45 JST**.

RDS starts at 08:45 to allow ~15 minutes warm-up. All other services (ASG, NAT, background)
start at the same time but are typically ready within 5-10 minutes.

## What Gets Started/Stopped

| Resource | Start Action | Stop Action |
|---|---|---|
| RDS (MySQL) | `start_db_instance` | `stop_db_instance` |
| NAT instance | `start_instances` | `stop_instances` |
| Background service instance | `start_instances` | `stop_instances` |
| Premium instances | `start_instances` | `stop_instances` |
| Free tier ASG | Set min=1, desired=1 | Set min=0, desired=0 |
| Lambda schedules (5 rules) | Enable | Disable |
| CloudWatch alarm actions | Enable | Disable |

The **ALB stays running** at all times (cannot be stopped; fixed cost ~$16-22/month).

## Working After Hours

### Option 1: Skip the Next Stop (Working Late)

If you need to keep the environment running past 22:00 JST, set the override
**before** 22:00:

```bash
aws ssm put-parameter \
  --name /development/optinist/schedule-override \
  --value on \
  --type String \
  --overwrite \
  --region ap-northeast-1
```

The override is automatically cleared by the next morning's start, so the
normal schedule resumes the following day.

### Option 2: Manual Start (Weekends / After Hours)

To start the environment outside scheduled hours:

```bash
aws lambda invoke \
  --function-name development-dev-scheduler \
  --payload '{"action":"start"}' \
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
  --region ap-northeast-1 \
  /dev/stdout
```

This respects the override parameter -- if it's set to "on", the stop will be
skipped. Clear it first if you want to force a stop:

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

### RDS won't start (7-day auto-start)

AWS automatically starts RDS instances that have been stopped for 7 days.
This shouldn't happen with the Mon-Fri schedule (max 2.5 days stopped),
but if it does, the next scheduled start will find it already running and
continue normally.

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
| Weekday 9-22 JST only (13h/day, 5 days) | ~$89-94 | ~$111-122 (~54%) |

Note: ALB (~$16-22/month) runs continuously and is excluded from savings.
