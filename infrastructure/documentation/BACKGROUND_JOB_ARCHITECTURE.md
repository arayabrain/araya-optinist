# Background Job Architecture: Separation from API Process

## Executive Summary

This document addresses the architectural concern raised in PR #247: background jobs should run separately from the FastAPI API process for clarity, maintainability, and reliability.

**Recommendation: Dedicated ECS Task**
- Deploy background jobs as a separate ECS service
- Reuses existing codebase and Docker image
- No code duplication or complex coordination
- Clear separation of concerns

---

## Current Problem

### Multi-Worker Conflict

When FastAPI runs with multiple workers (`--workers 2+`), each worker initializes its own scheduler:

```
┌─────────────────────────────────────────────────────────────┐
│ FastAPI with --workers=4                                    │
├─────────────────────────────────────────────────────────────┤
│  Worker 1: APScheduler → Runs sync job every 5 min         │
│  Worker 2: APScheduler → Runs sync job every 5 min         │
│  Worker 3: APScheduler → Runs sync job every 5 min         │
│  Worker 4: APScheduler → Runs sync job every 5 min         │
└─────────────────────────────────────────────────────────────┘
                         ↓
        Jobs run 4x per interval (unintended)
```

### Current Workaround: File-Based Lock

The current implementation uses atomic file creation (`scheduler.py:96-182`) to coordinate:

```python
# Only first worker to create this file runs the scheduler
_SCHEDULER_LOCK_FILE = "/tmp/optinist_scheduler.lock"
fd = os.open(_SCHEDULER_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
```

**Problems with this approach:**
1. **Fragile**: File locks can become stale on container crashes
2. **Complexity**: Requires PID tracking and stale lock detection
3. **Mixed concerns**: API process also manages background jobs
4. **Container restarts**: Lock files may persist across container replacements

---

## Background Jobs Overview

| Job | Interval | Purpose | Lock Required |
|-----|----------|---------|---------------|
| `PublishedExperimentSyncJob` | 5 min | Sync published experiments from S3 to local | Yes (FileLock) |
| `DataCleanupJob` | 60 min | Clean up data for logged-out free users | No |
| `StorageReconciliationJob` | 60 min | Reconcile incremental tracking with S3 | No (MySQL lock) |

All jobs are defined in `studio/app/common/core/background/` and have CLI wrappers in `studio/scripts/`.

---

## Existing Lambda Functions

Several Lambda functions already exist in `infrastructure/terraform/`. This section clarifies which are affected by this architectural change.

### Lambdas to DEPRECATE (Duplicate Code)

| Lambda | File | Why Deprecate |
|--------|------|---------------|
| `storage_reconciliation` | `storage_reconciliation.tf` | Duplicates `StorageReconciliationJob` in Studio codebase |

The `storage_reconciliation` Lambda reimplements the same logic that exists in `studio/app/common/core/background/storage_reconciliation_job.py`. This creates a maintenance burden (two codebases) and uses raw pymysql instead of SQLAlchemy.

**Action:** Delete after ECS background service is running. No conversion needed—the job logic already exists in the Studio codebase.

### Lambdas to KEEP (Event-Driven Infrastructure Management)

| Lambda | File | Purpose | Triggers |
|--------|------|---------|----------|
| `free_manager` | `free_manager.tf` | Scale ECS/ASG for free tier users | Schedule + ASG lifecycle events |
| `premium_manager` | `premium_manager.tf` | Manage premium instance ALB routing | User tier change events |
| `common_user_manager` | `common_user_manager.tf` | User management operations | Various events |

These Lambdas serve a fundamentally different purpose:

```
Background Jobs (Studio App)          Infrastructure Lambdas
─────────────────────────────         ──────────────────────────
├── Sync experiments from S3          ├── React to ASG events
├── Clean up user data                ├── Manage ECS task placement
├── Reconcile storage tracking        ├── Update ALB routing rules
│                                     │
├── Need full Studio codebase         ├── Only need AWS SDK (boto3)
├── Use SQLAlchemy ORM                ├── Use raw pymysql (simple queries)
└── Run on schedule only              └── React to AWS events + schedule
```

**Why these stay as Lambda:**
1. **Event-driven**: Respond to AWS infrastructure events (ASG instance launch/terminate), not just schedules
2. **AWS-native operations**: Manage ECS, ASG, ALB—perfect fit for Lambda + boto3
3. **No Studio dependencies**: Don't need the full application codebase
4. **Stateless**: Each invocation is independent

**Action:** No changes needed. These continue to run as Lambda functions.

### Summary: What Happens to Each Lambda

| Lambda | With Option A (ECS Task) | With Option C (ECS Scheduled) |
|--------|--------------------------|-------------------------------|
| `storage_reconciliation` | **Delete** - ECS service runs `StorageReconciliationJob` | **Delete** - ECS scheduled task runs CLI script |
| `free_manager` | **Keep** - Event-driven AWS management | **Keep** - Event-driven AWS management |
| `premium_manager` | **Keep** - Event-driven AWS management | **Keep** - Event-driven AWS management |
| `common_user_manager` | **Keep** - Event-driven AWS management | **Keep** - Event-driven AWS management |

---

## Architecture Options Evaluated

### Option A: Dedicated ECS Task (Recommended)

Deploy a separate ECS service that runs only background jobs.

```
┌─────────────────────────────────────────────────────────────┐
│ ECS Cluster                                                 │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────┐    ┌─────────────────────┐        │
│  │ API Service         │    │ Background Service   │        │
│  │ (2-4 workers)       │    │ (1 task, 1 worker)   │        │
│  │                     │    │                      │        │
│  │ - Serves HTTP       │    │ - Runs scheduler     │        │
│  │ - No scheduler      │    │ - No HTTP serving    │        │
│  │ - DISABLE_BG=1      │    │ - Same Docker image  │        │
│  └─────────────────────┘    └─────────────────────┘        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Implementation:**
- Same Docker image, different entrypoint
- API service: `DISABLE_BACKGROUND_SCHEDULER=1`
- Background service: Runs with `--workers=1`, scheduler enabled
- ECS desired count: 1 (only one instance needed)

**Pros:**
- Clean separation of concerns
- No code duplication (reuses existing job classes)
- Uses existing infrastructure (ECS, VPC, IAM)
- Simple: one task = one scheduler = no coordination needed
- Easy to monitor separately (CloudWatch metrics per service)
- Can scale API independently from background jobs

**Cons:**
- Slightly more infrastructure (one additional ECS service)
- Requires updating Terraform

---

### Option B: AWS Lambda + CloudWatch Events

Use Lambda functions triggered by CloudWatch Events schedules.

**Current State:** Partially implemented
- `storage_reconciliation.tf` - Already deployed, duplicates job logic
- `free_manager.tf` - Already deployed for user management

**Pros:**
- Serverless, no containers to manage
- Pay per execution
- Built-in scheduling via CloudWatch Events

**Cons:**
- **Code duplication**: Lambda implementations (`storage_reconciliation_package/`) duplicate logic from `studio/app/common/core/background/`
- **15-minute timeout**: May not be enough for large datasets
- **Cold starts**: Adds latency
- **No ORM reuse**: Lambda uses raw pymysql, API uses SQLAlchemy
- **Maintenance burden**: Two codebases to maintain
- **VPC complexity**: Each Lambda needs VPC config for RDS access

---

### Option C: External Cron (ECS Scheduled Tasks)

Use CloudWatch Events to trigger one-off ECS tasks.

```
CloudWatch Events → ECS RunTask → Runs CLI script → Exits
```

**Pros:**
- Reuses existing codebase (CLI scripts already exist)
- True separation (task runs and terminates)
- No long-running container

**Cons:**
- More AWS infrastructure (EventBridge rules, task definitions)
- Task startup latency (~30s for ECS)
- Harder to monitor (tasks come and go)
- Complex IAM permissions

---

### Option D: Keep Current APScheduler with File Locks

Continue with the current implementation.

**Pros:**
- Already implemented
- No infrastructure changes

**Cons:**
- File locks are fragile (race conditions, stale locks)
- Mixed concerns (API process manages jobs)
- Complexity in scheduler.py (PID tracking, stale detection)
- Harder to reason about (which worker runs the scheduler?)

---

## Recommendation: Option A (Dedicated ECS Task)

Option A is the clear winner because:

| Criteria | Option A | Option B | Option C | Option D |
|----------|----------|----------|----------|----------|
| Separation of concerns | ✅ | ✅ | ✅ | ❌ |
| No code duplication | ✅ | ❌ | ✅ | ✅ |
| Simple coordination | ✅ | ✅ | ✅ | ❌ |
| Uses existing infra | ✅ | Partial | ❌ | ✅ |
| Easy monitoring | ✅ | ✅ | ❌ | ❌ |
| No timeout limits | ✅ | ❌ | ✅ | ✅ |
| Maintainability | ✅ | ❌ | ⚠️ | ❌ |

---

## Implementation Plan

### Phase 1: Create Background Service Task Definition

Add to `infrastructure/terraform/`:

```hcl
# background_service.tf

resource "aws_ecs_task_definition" "background" {
  family                   = "subscr-optinist-background"
  network_mode             = "awsvpc"
  requires_compatibilities = ["EC2"]

  container_definitions = jsonencode([{
    name  = "background-worker"
    image = "${aws_ecr_repository.app.repository_url}:latest"

    # Single worker, scheduler enabled
    command = ["python", "-m", "studio", "--workers=1"]

    environment = [
      # Explicitly enable scheduler (opposite of API)
      { name = "DISABLE_BACKGROUND_SCHEDULER", value = "0" },
      # ... other env vars inherited from main task
    ]

    # Lower resources than API (background jobs are less demanding)
    cpu    = 512
    memory = 1024
  }])
}

resource "aws_ecs_service" "background" {
  name            = "subscr-background-worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.background.arn
  desired_count   = 1  # Only one instance needed

  # Don't attach to ALB (no HTTP traffic)
  # Uses same VPC/subnets as main service
}
```

### Phase 2: Update API Service

Modify existing API task definition:

```hcl
# In main.tf or ecs.tf

environment = [
  # Disable scheduler in API workers
  { name = "DISABLE_BACKGROUND_SCHEDULER", value = "1" },
  # ... other env vars
]
```

### Phase 3: Deprecate Lambda Implementations

Once the dedicated ECS service is running:

1. Disable CloudWatch Events for `storage_reconciliation` Lambda
2. Remove `storage_reconciliation.tf` (or keep disabled for reference)
3. Remove `storage_reconciliation_package/` directory
4. Update documentation

### Phase 4: Simplify scheduler.py

Remove file-based locking code (no longer needed with single-instance deployment):

```python
# Before: Complex lock acquisition
def _acquire_scheduler_lock(cls, _retry_count: int = 0) -> bool:
    # 80+ lines of lock management...

# After: Simple initialization
def initialize(cls):
    if cls._scheduler is not None:
        return
    if MODE.IS_STANDALONE:
        return
    cls._scheduler = AsyncIOScheduler()
    logger.info("Background scheduler initialized")
```

---

### Alerts

```hcl
resource "aws_cloudwatch_metric_alarm" "background_task_stopped" {
  alarm_name          = "subscr-background-task-stopped"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "RunningTaskCount"
  namespace           = "ECS/ContainerInsights"
  period              = "300"
  statistic           = "Average"
  threshold           = "1"
  alarm_description   = "Background worker is not running"

  dimensions = {
    ServiceName = aws_ecs_service.background.name
    ClusterName = aws_ecs_cluster.main.name
  }
}
```

---

## Alternative Approaches Considered

### Redis-Based Distributed Lock

Instead of file locks, use Redis with `SET NX EX`:

```python
# Acquire lock with 5-minute expiry
redis.set("scheduler_lock", worker_id, nx=True, ex=300)
```

**Rejected because:**
- Adds Redis dependency
- Still mixing concerns (API process manages jobs)
- More infrastructure to maintain

### Database-Based Lock (MySQL GET_LOCK)

Use MySQL advisory locks:

```python
cursor.execute("SELECT GET_LOCK('scheduler', 0)")
```

**Rejected because:**
- Still mixing concerns
- Lock contention on database
- Complex cleanup on worker crash

### Celery/RQ Task Queue

Use a dedicated task queue system:

**Rejected because:**
- Overkill for 3 simple periodic jobs
- Adds significant complexity (broker, workers, monitoring)
- Existing jobs don't need distributed execution

---

## Files Affected

### To Create
- `infrastructure/terraform/background_service.tf` - New ECS service

### To Modify
- `infrastructure/terraform/ecs.tf` - Add `DISABLE_BACKGROUND_SCHEDULER=1` to API
- `studio/app/common/core/background/scheduler.py` - Remove file-locking code

### To Remove (After Verification)
- `infrastructure/terraform/storage_reconciliation.tf` - Lambda that duplicates Studio job
- `infrastructure/terraform/storage_reconciliation_package/` - Lambda code (duplicates `StorageReconciliationJob`)

### Unchanged (Keep as Lambda)
- `infrastructure/terraform/free_manager.tf` - Event-driven, manages ECS/ASG scaling
- `infrastructure/terraform/free_manager_package/` - Lambda code for free tier management
- `infrastructure/terraform/premium_manager.tf` - Event-driven, manages ALB routing
- `infrastructure/terraform/premium_manager_package/` - Lambda code for premium instances
- `infrastructure/terraform/common_user_manager.tf` - Event-driven user management

---

## Success Criteria

- [ ] Background service runs with `desired_count=1`
- [ ] API service has `DISABLE_BACKGROUND_SCHEDULER=1`
- [ ] All three jobs execute on schedule (verify via CloudWatch logs)
- [ ] No duplicate job execution
- [ ] File-locking code removed from scheduler.py
- [ ] `storage_reconciliation` Lambda deprecated (duplicates Studio code)
- [ ] Other Lambdas unchanged (`free_manager`, `premium_manager`, `common_user_manager`)

---

## References

- PR #247: Original discussion about multi-worker scheduler issue
- [12-Factor App: Concurrency](https://12factor.net/concurrency) - Run each process type separately
- [AWS ECS Best Practices](https://docs.aws.amazon.com/AmazonECS/latest/bestpracticesguide/application.html)
- Existing CLI scripts: `studio/scripts/run_*.py`
