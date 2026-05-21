# Common User Manager Lambda

## Overview
Handles shared user lifecycle operations for both free and premium tiers. Consolidates common functionality that was previously duplicated across tier-specific managers.

## Primary Responsibilities
- **Inactivity Logout**: Automatically logout inactive users (>2 hours) for both tiers
- **Workflow Recovery**: Reset stale workflow counts from crashed workflows
- **Heartbeat Monitoring**: Process activity timeouts consistently across tiers

## Triggers
- **CloudWatch Events**: Scheduled every 10 minutes

## Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│              COMMON USER MANAGER                            │
│              (Every 10 Minutes)                             │
└─────────────────────────────────────────────────────────────┘
                              │
                              │
              ┌───────────────▼───────────────┐
              │                               │
              │  1. WORKFLOW RECOVERY         │
              │     - User inactive >2h AND   │
              │       (workflow completed OR  │
              │        workflow >4h old)      │
              │     - Reset workflow counts   │
              │     - Both free & premium     │
              │                               │
              └───────────────┬───────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        │                     │                     │
┌───────▼────────┐  ┌─────────▼────────┐  ┌────────▼────────┐
│ FREE TIER      │  │ PREMIUM TIER     │  │ RESULTS         │
│ INACTIVITY     │  │ INACTIVITY       │  │                 │
│                │  │                  │  │ • Workflows     │
│ • Find users   │  │ • Find users     │  │   recovered     │
│   idle >2h     │  │   idle >2h       │  │ • Free users    │
│ • Delete from  │  │ • Delete ALB     │  │   logged out    │
│   database     │  │   rules/TGs      │  │ • Premium users │
│                │  │ • Delete from    │  │   logged out    │
│                │  │   database       │  │                 │
└────────────────┘  └──────────────────┘  └─────────────────┘
```

## Related Files

### Core Files
- `common_user_manager.py` - Main Lambda function

### Database
- `studio/app/common/models/free_user.py` - FreeUserAssignment model
- `studio/app/common/models/premium_user.py` - PremiumUserAssignment model

### Terraform Configuration
- `free_manager.tf` or `premium_manager.tf` - Lambda infrastructure
- `infrastructure.tf` - VPC, subnets, security groups

### Related Lambdas
- `free_manager.py` - Free tier scaling (no longer handles inactivity)
- `premium_manager.py` - Premium tier scaling (no longer handles inactivity)
- `premium_cleanup.py` - Orphaned resource cleanup (different purpose)

### Test Files
- `test_common_user_manager.py` - Unit tests

## Key Environment Variables
- `RDS_HOST` - Database connection string (format: `host:port` or `host`, defaults to port 3306)
- `RDS_USER`, `RDS_PASSWORD`, `RDS_DATABASE` - Database credentials
- `FREE_IDLE_TIMEOUT_HOURS` - Free tier timeout (default: 2)
- `PREMIUM_IDLE_TIMEOUT_HOURS` - Premium tier timeout (default: 2)
- `AUTOSCALING_TARGET_GROUP_ARN` - Shared target group (optional)

## Database Implementation

The Lambda uses two approaches for database access:

### SQLAlchemy (Workflow Recovery)
- **Purpose**: Type-safe, maintainable queries for workflow recovery
- **Function**: `get_sqlalchemy_session()`
- **Used by**: `recover_stale_workflow_counts()`
- **Features**:
  - Type-safe table definitions with proper column types
  - Complex query conditions with proper NULL handling
  - Engine disposal after use (Lambda-compatible)
  - Automatic transaction management

### PyMySQL (Inactivity Checks)
- **Purpose**: Direct SQL queries for inactivity checks
- **Function**: `get_db_connection()`
- **Used by**: `check_free_user_inactivity()`, `check_premium_user_inactivity()`

Both approaches:
- Parse port from `RDS_HOST` (format: `host:port` or `host`)
- Default to port 3306 if not specified
- Use connection pooling with `pool_pre_ping=True`

## Division of Labor

### Common User Manager (Every 10 min)
- Workflow crash recovery
- Inactivity-based logout
- Activity timeout enforcement

### Premium/Free Managers (Every 5-15 min)
- Scaling decisions
- Load balancing
- Instance management

### Premium Cleanup (Hourly)
- Orphaned resource cleanup
- Data hygiene
- State reconciliation

## Workflow Recovery (Crash Recovery)

**Problem**: When workflows crash without cleanup, `active_workflow_count` gets stuck at non-zero values, preventing users from starting new workflows or being migrated.

**Solution**: Uses a hybrid approach that only resets workflow counts when **user is inactive (>2 hours)** AND one of these conditions is met:
1. **Workflow has completed**: `last_workflow_end >= last_workflow_start` (decrement failed)
2. **Workflow is very old**: `last_workflow_start > 4 hours ago` (likely crashed)

This ensures we don't reset counts for:
- Long-running workflows with active user sessions
- Recent workflows where user is still working
- Workflows that might legitimately take 2-4 hours with inactive user

**Implementation**: Uses SQLAlchemy for type-safe database operations:
```python
# Only reset when user inactive >2h AND (workflow completed OR very old)
update(FREE_USERS_TABLE)
  .where(active_workflow_count > 0)
  .where(last_activity < now - 2 hours)
  .where(
    (last_workflow_end >= last_workflow_start) OR  # Completed
    (last_workflow_start < now - 4 hours)           # Very old
  )
  .values(active_workflow_count=0)
```

**Configuration**:
- `WORKFLOW_USER_INACTIVITY_HOURS = 2` - User must be inactive for this long
- `WORKFLOW_VERY_OLD_HOURS = 4` - Workflows older than this are assumed crashed

**Runs**: Every 10 minutes via scheduled CloudWatch Event

**Impact**:
- Prevents users from being permanently blocked by crashed workflows
- Protects long-running legitimate workflows (2-4 hours) with active users
- Uses existing heartbeat system (`last_activity`) to determine user activity
- Allows Free Manager to migrate users with crashed workflows
- Allows Premium Manager to scale down instances with crashed workflows

## Inactivity Logout

### Free Tier
- Timeout: 2 hours (configurable)
- Action: Delete from `free_user_assignments`
- No AWS resource cleanup needed

### Premium Tier
- Timeout: 2 hours (configurable)
- Action: Delete ALB rules, target groups, database entry
- Prevents blocking user logout on errors

## Testing

### Run Unit Tests
```bash
cd infrastructure/terraform/common_user_manager_package
pip install -r requirements-test.txt
pytest test_common_user_manager.py -v
```

### Integration Testing
1. Create test user with old `last_activity`
2. Wait for next Lambda run (10 min)
3. Verify user is logged out
4. Check CloudWatch logs for confirmation

## Response Format

Success response:
```json
{
  "statusCode": 200,
  "body": {
    "message": "Common user manager completed",
    "workflow_recovery": {
      "recovered": 3,
      "free": 2,
      "premium": 1
    },
    "free_inactivity": {
      "logged_out": 5
    },
    "premium_inactivity": {
      "logged_out": 2,
      "failed": 0
    }
  }
}
```

## Known Limitations

1. **No Distributed Locking**: Multiple invocations could run simultaneously (safe but may duplicate logs)
2. **No Retry Logic**: ALB cleanup failures don't block logout (prevents stuck users)
3. **Fixed Timeouts**: Not configurable per-user
