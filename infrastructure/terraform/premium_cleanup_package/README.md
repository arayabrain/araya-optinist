# Premium Cleanup Lambda

## Overview
Maintains data hygiene and resource reconciliation for premium tier. Runs hourly to clean up stale assignments and orphaned AWS resources.

## Primary Responsibilities
- **Stale Assignment Cleanup**: Remove inactive user assignments (>2 hours idle)
- **Orphaned Resource Cleanup**: Delete ALB rules/target groups with no database entry
- **State Reconciliation**: Sync database instance states with AWS reality
- **Standby Pool Monitoring**: Health checks on standby instances (read-only)

## What It Does NOT Do
- ❌ Make scaling decisions (that's `premium_manager`)
- ❌ Stop or start instances (that's `premium_manager`)
- ❌ Update ECS service count (that's `premium_manager`)

## Triggers
- **CloudWatch Events**: Scheduled hourly
- **Manual Invocation**: Test cleanup via API

## Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                   PREMIUM CLEANUP                           │
│                   (Runs Hourly)                             │
└─────────────────────────────────────────────────────────────┘
                              │
                ┌─────────────┴─────────────┐
                │                           │
         ┌──────▼──────┐           ┌───────▼────────┐
         │  Scheduled  │           │  Manual Test   │
         │  Cleanup    │           │  Invocation    │
         └──────┬──────┘           └───────┬────────┘
                │                           │
                │                           │
    ┌───────────▼───────────────────────────▼────────┐
    │                                                 │
    │  1. STALE ASSIGNMENTS                          │
    │     - Find users idle >2 hours                 │
    │     - Delete ALB rules                         │
    │     - Delete target groups                     │
    │     - Remove from database                     │
    │                                                 │
    │  2. ORPHANED RESOURCES                         │
    │     - List all ALB rules                       │
    │     - Compare with database                    │
    │     - Delete rules without DB entry            │
    │     - Delete orphaned target groups            │
    │                                                 │
    │  3. STATE RECONCILIATION                       │
    │     - Get AWS instance states                  │
    │     - Compare with database                    │
    │     - Update DB to match AWS                   │
    │     - Remove terminated instances              │
    │                                                 │
    │  4. STANDBY POOL CHECK                         │
    │     - Count standby instances                  │
    │     - Check health status                      │
    │     - Report issues (read-only)                │
    │                                                 │
    └─────────────────────────────────────────────────┘
```

## Related Files

### Core Files
- `premium_cleanup.py` - Main Lambda function
- `../../aws_constants.py` - Shared AWS constants

### Database
- `studio/app/common/models/premium_user.py` - PremiumUserAssignment model

### Terraform Configuration
- `premium_manager.tf` - Lambda infrastructure (shared with premium_manager)
- `infrastructure.tf` - VPC, subnets, security groups

### Related Lambdas
- `premium_manager.py` - Compute/capacity management (companion)
- `free_cleanup.py` - Free tier equivalent

## Key Environment Variables
- `RDS_HOST` - Database connection string
- `RDS_USER`, `RDS_PASSWORD`, `RDS_DATABASE` - Database credentials
- `CLUSTER_NAME` - ECS cluster name
- `PREMIUM_IDLE_TIMEOUT_HOURS` - Idle timeout (default: 2, must match premium_manager)
- `PREMIUM_INSTANCE_IDS` - Comma-separated instance IDs

## Division of Labor

### Premium Manager (Real-time)
- User assignment/release
- Instance scaling decisions
- Standby pool management
- ECS service updates

### Premium Cleanup (Hourly)
- Data hygiene
- Resource reconciliation
- Orphaned resource cleanup
- Health monitoring

## Manual Test Cleanup

Supports test cleanup via Lambda invocation:

```python
lambda_client.invoke(
    FunctionName='subscr-premium-cleanup',
    Payload=json.dumps({
        "action": "cleanup_test_users",
        "user_emails": ["test1@example.com", "test2@example.com"]
    })
)
```
