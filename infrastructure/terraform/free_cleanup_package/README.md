# Free Cleanup Lambda

## Overview
Provides test data management and simulation utilities for Free Manager testing. Designed to be invoked by test scripts that run outside VPC.

## Primary Responsibilities
- **Test User Cleanup**: Remove test user sessions from database
- **Activity Simulation**: Insert/update user activity for testing
- **Workflow Simulation**: Set active workflow counts for testing
- **Distribution Queries**: Get current user distribution across instances
- **User Counting**: Count active users with configurable threshold

## Why This Lambda Exists

Test scripts run **outside VPC** and cannot access RDS directly. This Lambda:
- Runs **inside VPC** with RDS access
- Provides API for test scripts to manipulate test data
- Bypasses VPC restrictions for testing purposes

## Triggers
- **Manual Invocation**: Called by test scripts via Lambda API

## Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    FREE CLEANUP                             │
│              (Manual Test Invocation)                       │
└─────────────────────────────────────────────────────────────┘
                              │
                              │
              ┌───────────────┴───────────────┐
              │                               │
              │   Test Script (Outside VPC)   │
              │   Cannot Access RDS Directly  │
              │                               │
              └───────────────┬───────────────┘
                              │
                              │ Lambda Invoke
                              │
              ┌───────────────▼───────────────┐
              │                               │
              │  Lambda (Inside VPC)          │
              │  Has RDS Access               │
              │                               │
              └───────────────┬───────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        │                     │                     │
┌───────▼────────┐  ┌─────────▼────────┐  ┌────────▼────────┐
│ CLEANUP        │  │ SIMULATION       │  │ QUERIES         │
│                │  │                  │  │                 │
│ • Test Users   │  │ • User Activity  │  │ • Distribution  │
│ • All Test     │  │ • Workflows      │  │ • Active Count  │
│   (test_*)     │  │                  │  │                 │
└────────────────┘  └──────────────────┘  └─────────────────┘
```

## Supported Actions

### 1. Cleanup Test Users
Remove sessions for specific test users by email.

```python
lambda_client.invoke(
    FunctionName='subscr-free-cleanup',
    Payload=json.dumps({
        "action": "cleanup_test_users",
        "user_emails": ["test1@example.com", "test2@example.com"]
    })
)
```

### 2. Cleanup All Test Users
Remove all users with `test_` prefix in user_id.

```python
lambda_client.invoke(
    FunctionName='subscr-free-cleanup',
    Payload=json.dumps({
        "action": "cleanup_all_test_users"
    })
)
```

### 3. Simulate User Activity
Insert/update user activity for testing.

```python
lambda_client.invoke(
    FunctionName='subscr-free-cleanup',
    Payload=json.dumps({
        "action": "simulate_user_activity",
        "user_id": "test_user_1",
        "instance_id": "i-1234567890abcdef0",
        "minutes_ago": 0  # 0 = now, 5 = 5 minutes ago
    })
)
```

### 4. Simulate Workflow
Set active workflow count for a user.

```python
lambda_client.invoke(
    FunctionName='subscr-free-cleanup',
    Payload=json.dumps({
        "action": "simulate_workflow",
        "user_id": "test_user_1",
        "workflow_count": 1
    })
)
```

### 5. Get User Distribution
Query current user distribution across instances.

```python
lambda_client.invoke(
    FunctionName='subscr-free-cleanup',
    Payload=json.dumps({
        "action": "get_user_distribution"
    })
)
```

### 6. Count Active Users
Count users active in last N minutes.

```python
lambda_client.invoke(
    FunctionName='subscr-free-cleanup',
    Payload=json.dumps({
        "action": "count_active_users",
        "threshold_minutes": 10
    })
)
```

## Related Files

### Core Files
- `free_cleanup.py` - Main Lambda function

### Database
- `studio/app/common/models/free_user.py` - FreeUserAssignment model

### Terraform Configuration
- `free_manager.tf` - Lambda infrastructure (shared with free_manager)
- `infrastructure.tf` - VPC, subnets, security groups

### Related Lambdas
- `free_manager.py` - Free tier management (companion)
- `premium_cleanup.py` - Premium tier equivalent

### Test Scripts
- `infrastructure/scripts/test_free_manager.py` - Uses this Lambda for testing
- `infrastructure/scripts/test_autoscaling_usage.py` - Uses this Lambda for testing

## Key Environment Variables
- `RDS_HOST` - Database connection string
- `RDS_USER`, `RDS_PASSWORD`, `RDS_DATABASE` - Database credentials

## Testing Workflow

```
┌──────────────────┐
│  Test Script     │
│  (Outside VPC)   │
└────────┬─────────┘
         │
         │ 1. Cleanup old test data
         ▼
┌──────────────────┐
│  free_cleanup    │
│  Lambda          │
└────────┬─────────┘
         │
         │ 2. Simulate user activity
         ▼
┌──────────────────┐
│  free_cleanup    │
│  Lambda          │
└────────┬─────────┘
         │
         │ 3. Trigger free_manager
         ▼
┌──────────────────┐
│  free_manager    │
│  Lambda          │
└────────┬─────────┘
         │
         │ 4. Query distribution
         ▼
┌──────────────────┐
│  free_cleanup    │
│  Lambda          │
└────────┬─────────┘
         │
         │ 5. Verify results
         ▼
┌──────────────────┐
│  Test Script     │
│  Assertions      │
└──────────────────┘
```

## Response Format

All actions return JSON with:
- `statusCode`: HTTP status (200 = success, 400/500 = error)
- `body`: JSON string with result details

Example success response:
```json
{
  "statusCode": 200,
  "body": "{\"message\": \"Cleaned 3 test user sessions\", \"result\": {...}}"
}
```
