# Free Manager Lambda

## Overview
Manages autoscaling and load balancing for free tier users. Monitors active user count and proactively scales ECS service to maintain performance.

## Primary Responsibilities
- **User Monitoring**: Count active free tier users
- **Capacity by user count, both directions**: compute the instance target on every run; scale the ASG out when the count asks for more, in when it asks for fewer
- **Scale-in by workload**: choose the instance to remove (never one running a workflow, then fewest active users) and terminate it with the desired capacity decremented
- **Load Rebalancing**: Distribute users evenly across all instances
- **Workflow Protection**: Preserve users with active workflows on current instance
- **Instance Readiness**: Wait for new instances to launch before rebalancing

## Triggers
- **CloudWatch Events**: Scheduled every 5 minutes
- **ASG Lifecycle Events**: Immediate ECS sync when ASG scales

## Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     FREE MANAGER                            │
│                  (Every 5 Minutes)                          │
└─────────────────────────────────────────────────────────────┘
                              │
                ┌─────────────┴─────────────┐
                │                           │
         ┌──────▼──────┐           ┌───────▼────────┐
         │  Scheduled  │           │  ASG Event     │
         │  Monitoring │           │  (Immediate)   │
         └──────┬──────┘           └───────┬────────┘
                │                           │
                │                           │
    ┌───────────▼───────────┐      ┌───────▼────────────┐
    │ 1. Resync ECS to ASG  │      │ 1. Detect ASG      │
    │    (raise only)       │      │    Scale Event     │
    │                       │      │                    │
    │ 2. Count Active Users │      │ 2. Get ASG         │
    │    (Last 5 min)       │      │    Desired Count   │
    │                       │      │                    │
    │ 3. Calculate Target   │      │ 3. Sync ECS        │
    │    (1 per 5 users,    │      │    Service         │
    │    within Min/Max)    │      │    (lower only on  │
    │    Publish Target     │      │    a termination)  │
    │                       │      └────────────────────┘
    │ 4a. Scale out: raise  │
    │    ASG + ECS, wait    │
    │    for instances,     │
    │    rebalance users    │
    │ 4b. Scale in (gap≥2): │
    │    pick instances     │
    │    with no workflow,  │
    │    fewest users;      │
    │    terminate them     │
    │ 4c. Neither: rebalance│
    │    if imbalanced      │
    └───────────────────────┘
```

## Related Files

### Core Files
- `free_manager.py` - Main Lambda function
- `free_user_utils.py` - Utility functions for user management
- `../../aws_constants.py` - Shared AWS constants

### Database
- `studio/app/common/models/free_user.py` - FreeUserAssignment model

### Terraform Configuration
- `free_manager.tf` - Lambda infrastructure definition
- `infrastructure.tf` - VPC, subnets, security groups
- `compute.tf` - Auto Scaling Group configuration

### Related Lambdas
- `free_cleanup.py` - Test data cleanup (companion)
- `premium_manager.py` - Premium tier equivalent

## Key Environment Variables
- `RDS_HOST` - Database connection string
- `RDS_USER`, `RDS_PASSWORD`, `RDS_DATABASE` - Database credentials
- `CLUSTER_NAME` - ECS cluster name
- `FREE_SERVICE_NAME` - ECS service name (e.g., subscr-optinist-cloud-service)
- `ASG_NAME` - Auto Scaling Group name
- `FREE_IDLE_THRESHOLD_MINUTES` - Idle threshold (default: 5)
- `LOAD_ALARM_NAMES` - the `-high` alarms; no scale-in while one is in ALARM or for 15 minutes after it clears

The instance count floor and ceiling are not environment variables: they are
read from the ASG's own `MinSize`/`MaxSize` on every run. There is no user
threshold either: the target is computed at any user count, so 0 users bring
the group back to `MinSize`.

## Scaling Algorithm

### Capacity Calculation
```
desired_instances = min(max(asg_min_size, ceil(active_users / 5)), asg_max_size)
```

- **Formula**: 1 instance per 5 users (rounded up)
- **Bounds**: the ASG's own `MinSize`/`MaxSize`, read via
  `describe_auto_scaling_groups` each run. `SetDesiredCapacity` rejects
  anything outside them, so the group's configuration is the single source of
  truth and capacity can be adjusted without redeploying this function.
- **Published**: the target goes out as `TargetInstances` (next to
  `ActiveLogins`) on every run, so it can be read against the group's
  `GroupDesiredCapacity`.

### Scale-in

The Lambda is the only thing that removes a free instance (the `cpu-high` /
`memory-high` alarms may add one; there is no scale-down alarm). A surplus
of 2 or more over the target is removed at once; a surplus of 1 once it has
shown in the 3 completed 5-minute periods before the run (read from
`TargetInstances` against the group's `GroupDesiredCapacity`; a failed run
leaves a hole and the wait starts over); nothing while a `-high` alarm is in
ALARM or left it less than 15 minutes ago (`LOAD_ALARM_NAMES`). Then:

1. Candidates are the group's `InService`, unprotected instances with **no
   running workflow** (`SUM(active_workflow_count)` per instance, over every
   assignment row). If too few are safe, only those go; the rest waits for
   workflows to finish.
2. Order: fewest active users, then the availability zone holding the most
   instances, then the newest launch.
3. Each is terminated with `TerminateInstanceInAutoScalingGroup` and
   `ShouldDecrementDesiredCapacity`, so the group never picks an instance
   itself. ECS is lowered by the terminate event once the instance is gone.

### Rebalancing Strategy

**Multi-Instance Algorithm** (improved):
1. Calculate target users per instance (even distribution)
2. Identify overloaded instances (above target + 1)
3. Identify underloaded instances (below target)
4. Migrate idle users from overloaded to underloaded (round-robin)
5. Skip users with active workflows (workflow protection)

## Workflow Protection

Users with active workflows are **never migrated** during rebalancing:
- Prevents workflow interruption
- Prevents data loss
- Checked via `active_workflow_count` field
- Automatically retried on next run after workflows complete

## Instance Readiness

Waits for new instances to become operational, retrying every 60 seconds:
- **Lifecycle Hook**: ~5 minutes
- **EC2 Launch**: ~5 minutes
- **ECS Task Start**: ~7 minutes

The wait is bounded by the invocation's remaining time (less a 60 s margin),
so the `finally` that clears the scaling lock always runs; a launch that
outlasts the invocation is picked up by the next scheduled run.

## Coordination with ALB

Works with ALB sticky sessions (5-minute duration):
- Users migrate to new instances within 5 minutes after rebalancing
- Database update triggers routing change
- ALB sticky session expires naturally
- User's next request routes to new instance
