# Background Job Architecture: Dedicated ECS Service

## Overview

Background jobs run in a dedicated ECS service, separate from the API process. This provides clear separation of concerns, eliminates multi-worker coordination complexity, and improves reliability.

**Architecture:**
```
┌─────────────────────────────────────────────────────────────┐
│ ECS Cluster                                                 │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────┐    ┌─────────────────────┐        │
│  │ API Service         │    │ Background Service   │        │
│  │ (multi-worker)      │    │ (1 task, 1 worker)   │        │
│  │                     │    │                      │        │
│  │ - Serves HTTP       │    │ - Runs scheduler     │        │
│  │ - No scheduler      │    │ - No HTTP traffic    │        │
│  │ - DISABLE_BG=1      │    │ - Same Docker image  │        │
│  └─────────────────────┘    └─────────────────────┘        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Background Jobs

| Job | Interval | Purpose |
|-----|----------|---------|
| `PublishedExperimentSyncJob` | 5 min | Sync published experiments from S3 to local |
| `DataCleanupJob` | 60 min | Clean up data for logged-out free users |
| `StorageReconciliationJob` | 60 min | Reconcile incremental tracking with S3 |

All jobs are defined in `studio/app/common/core/background/`.

---

## Infrastructure

### Background Service (`background_service.tf`)

- **Task Definition**: 512 CPU, 1024 MB memory, single worker
- **ECS Service**: `desired_count=1` (only one instance needed)
- **Environment**: `DISABLE_BACKGROUND_SCHEDULER=0` (scheduler enabled)
- **No ALB**: Background service doesn't serve HTTP traffic
- **CloudWatch Alarms**: Task stopped, CPU high, memory high

### API Services (`compute.tf`)

- **Environment**: `DISABLE_BACKGROUND_SCHEDULER=1` (scheduler disabled)
- Both autoscaling and premium task definitions disable the scheduler

---

## Lambda Functions (Unchanged)

Infrastructure management Lambdas remain separate from background jobs:

| Lambda | Purpose | Why Lambda |
|--------|---------|------------|
| `free_manager` | Scale ECS/ASG for free tier | Event-driven (ASG lifecycle) |
| `premium_manager` | Manage premium instance routing | Event-driven (tier changes) |
| `common_user_manager` | User management operations | Event-driven |

These Lambdas respond to AWS infrastructure events and only need boto3, not the full Studio codebase.

---

## Monitoring

CloudWatch dashboard includes Background Service metrics:
- Running task count
- CPU and memory utilization
- Task stopped alarm (alerts if background jobs stop running)

View logs at: `/ecs/subscr-background-optinist-cloud-taskdef`

---

## Alternative Approaches Considered

### AWS Lambda for Background Jobs
Use Lambda functions triggered by CloudWatch Events schedules.

**Rejected:** Code duplication (Lambda reimplements job logic), 15-minute timeout limit, maintenance burden of two codebases, no ORM reuse.

### ECS Scheduled Tasks (External Cron)
Use CloudWatch Events to trigger one-off ECS tasks that run and terminate.

**Rejected:** Task startup latency (~30s), harder to monitor transient tasks, more complex IAM permissions.

### File-Based Locking (Previous Implementation)
Use atomic file creation to coordinate which API worker runs the scheduler.

**Rejected:** Fragile (stale locks on crashes), complex PID tracking, mixed concerns (API manages jobs).

### Redis/Database Distributed Lock
Use Redis `SET NX EX` or MySQL `GET_LOCK()` for coordination.

**Rejected:** Adds infrastructure dependency, still mixes concerns, complex cleanup on crashes.

### Celery/Task Queue
Use a dedicated task queue system like Celery or RQ.

**Rejected:** Overkill for 3 simple periodic jobs, significant added complexity.

---

## Files

### Created
- `infrastructure/terraform/background_service.tf` - ECS task definition, service, and alarms

### Modified
- `infrastructure/terraform/compute.tf` - Added `DISABLE_BACKGROUND_SCHEDULER=1` to API tasks
- `infrastructure/terraform/monitoring.tf` - Added Background Service metrics to dashboard
- `studio/app/common/core/background/scheduler.py` - Removed file-locking code

### Deleted
- `infrastructure/terraform/storage_reconciliation.tf` - Lambda duplicated Studio job logic
- `infrastructure/terraform/storage_reconciliation_package/` - Lambda dependencies

---

## Verification

1. **API logs**: "Background scheduler disabled by DISABLE_BACKGROUND_SCHEDULER env var"
2. **Background service logs**: "Background scheduler initialized" and job execution logs
3. **CloudWatch**: Background service running with task count = 1
4. **No duplicates**: Jobs execute once per interval (not per API worker)
