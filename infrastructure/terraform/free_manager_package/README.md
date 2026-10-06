# Free Manager Lambda

## Overview
Manages autoscaling and load balancing for free tier users. Monitors active user count and proactively scales ECS service to maintain performance.

## Primary Responsibilities
- **User Monitoring**: Count active free tier users
- **Proactive Scaling**: Scale ECS service when user threshold reached
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
    │ 1. Count Active Users │      │ 1. Detect ASG      │
    │    (Last 10 min)      │      │    Scale Event     │
    │                       │      │                    │
    │ 2. Check Threshold    │      │ 2. Get ASG         │
    │    (≥5 users?)        │      │    Desired Count   │
    │                       │      │                    │
    │ 3. Calculate Desired  │      │ 3. Sync ECS        │
    │    Instances          │      │    Service         │
    │    (1 per 5 users)    │      │                    │
    │                       │      └────────────────────┘
    │ 4. Scale ASG          │
    │    (if needed)        │
    │                       │
    │ 5. Wait for Instances │
    │    (up to 17 min)     │
    │                       │
    │ 6. Rebalance Users    │
    │    - Multi-instance   │
    │    - Even distribution│
    │    - Skip workflows   │
    │                       │
    │ 7. Publish Metrics    │
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
- `FREE_USER_THRESHOLD` - Users to trigger scaling (default: 5)
- `FREE_IDLE_THRESHOLD_MINUTES` - Idle threshold (default: 5)

The instance count floor and ceiling are not environment variables: they are
read from the ASG's own `MinSize`/`MaxSize` on every run.

## Scaling Algorithm

### Capacity Calculation
```
desired_instances = min(max(asg_min_size, ceil(active_users / 5)), asg_max_size)
```

- **Formula**: 1 instance per 5 users (rounded up)
- **Bounds**: the ASG's own `MinSize`/`MaxSize`, read via
  `describe_auto_scaling_groups` each run. `SetDesiredCapacity` rejects
  anything outside them, so the group's configuration is the single source of
  truth and capacity can be retuned without redeploying this function.

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

Waits up to 17 minutes for new instances to become operational:
- **Lifecycle Hook**: ~5 minutes
- **EC2 Launch**: ~5 minutes  
- **ECS Task Start**: ~7 minutes
- **Total**: ~17 minutes for full readiness

Retries every 60 seconds until instances are ready or timeout.

## Coordination with ALB

Works with ALB sticky sessions (5-minute duration):
- Users migrate to new instances within 5 minutes after rebalancing
- Database update triggers routing change
- ALB sticky session expires naturally
- User's next request routes to new instance
