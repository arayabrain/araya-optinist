# Premium Manager Lambda

## Overview
Manages compute resources and capacity for premium tier users. Handles real-time user assignments, instance scaling, and load balancing.

## Primary Responsibilities
- **User Assignment**: Assign premium users to dedicated EC2 instances via API calls
- **User Release**: Release users from instances when they logout
- **Instance Scaling**: Scale instances up/down based on active user count
- **Standby Pool**: Maintain stopped instances for fast startup
- **Load Balancing**: Migrate users from shared to dedicated instances
- **ALB Routing**: Create/delete Application Load Balancer rules for user routing

## Triggers
- **API Gateway**: Real-time user login/logout events
- **CloudWatch Events**: Scheduled monitoring every 15 minutes

## Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    PREMIUM MANAGER                          │
└─────────────────────────────────────────────────────────────┘
                              │
                ┌─────────────┴─────────────┐
                │                           │
         ┌──────▼──────┐           ┌───────▼────────┐
         │  API Events │           │ Scheduled      │
         │  (Assign/   │           │ Monitoring     │
         │   Release)  │           │ (Every 15 min) │
         └──────┬──────┘           └───────┬────────┘
                │                           │
                │                           │
    ┌───────────▼───────────┐      ┌───────▼────────────┐
    │ 1. Check Available    │      │ 1. Count Active    │
    │    Instances          │      │    Users           │
    │                       │      │                    │
    │ 2. Priority Logic:    │      │ 2. Check Instance  │
    │    - Dedicated        │      │    States          │
    │    - Shared           │      │                    │
    │    - Standby          │      │ 3. Scale Decision  │
    │    - Autoscaling Pool │      │    (Up/Down)       │
    │                       │      │                    │
    │ 3. Create ALB Rule    │      │ 4. Migrate Users   │
    │                       │      │    (Shared→Dedicated)│
    │ 4. Store Assignment   │      │                    │
    │    in RDS             │      │ 5. Update ECS      │
    └───────────────────────┘      └────────────────────┘
```

## Related Files

### Core Files
- `premium_manager.py` - Main Lambda function
- `premium_user_utils.py` - Utility functions for user management
- `../../aws_constants.py` - Shared AWS constants

### Database
- `studio/app/common/models/premium_user.py` - PremiumUserAssignment model

### Terraform Configuration
- `premium_manager.tf` - Lambda infrastructure definition
- `infrastructure.tf` - VPC, subnets, security groups
- `compute.tf` - EC2 launch templates

### Related Lambdas
- `premium_cleanup.py` - Hourly cleanup of stale data (companion)
- `free_manager.py` - Free tier equivalent

## Key Environment Variables
- `RDS_HOST` - Database connection string
- `RDS_USER`, `RDS_PASSWORD`, `RDS_DATABASE` - Database credentials
- `VPC_ID` - VPC for target group creation
- `ALB_LISTENER_ARN` - ALB listener for routing rules
- `CLUSTER_NAME` - ECS cluster name
- `PREMIUM_EXTRA_CAPACITY` - Buffer instances (default: 2)
- `PREMIUM_IDLE_TIMEOUT_HOURS` - Idle timeout (default: 2)
- `PREMIUM_STANDBY_POOL_SIZE` - Standby instances (default: 1)

## Assignment Priority Logic

1. **Dedicated Running Instance** (fastest) - Assign to available running instance
2. **Shared Instance** (immediate) - Share with least loaded instance
3. **Autoscaling Pool** (temporary) - Use free tier pool, migrate later
4. **Standby Instance** (5-15s) - Start stopped instance
5. **Scale Up** (2-3 min) - Create new instance

## Scaling Strategy
- **Conservative**: Keeps `active_users + 1` instances running
- **Dynamic Capacity**: Based on premium subscriber count
- **Coordinates with**: `premium_cleanup` for data hygiene
