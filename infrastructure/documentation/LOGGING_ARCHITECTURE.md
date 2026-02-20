# Logging: System-Wide Log Configuration and Monitoring

## Executive Summary

- **Dual-output logging** routes application logs to both stdout (CloudWatch via awslogs driver) and local rotating files (`logs/studio.log`) with 365-day retention
- **Per-user log isolation** uses hashed `client_id` injected via ASGI middleware and propagated across process boundaries (subprocess, snakemake) for multiuser filtering
- **Six CloudWatch log groups** capture ECS container output (free/premium/background) and Lambda execution (free-manager, premium-manager/cleanup, common-user-manager), all with explicit retention policies
- **CloudWatch Agent** on EC2 instances publishes host-level metrics (CPU, memory, I/O wait, load average) to the `CWAgent` namespace for infrastructure monitoring
- **Centralized dashboard** (`subscr-optinist-monitoring`) aggregates 19 alarms across ECS, EC2, RDS, EFS, and ALB with threshold-based alerting

---

## Key Architectural Principles

1. **Log Once, Route Everywhere**
   - Application writes to Python logging; the ECS awslogs driver and file handler fan out to CloudWatch and local disk
   - No application code writes directly to CloudWatch

2. **User Identity in Every Log Line**
   - `ClientIdLoggingMiddleware` extracts the Firebase/JWT uid, hashes it to a 16-char `client_id`, and stores it in a `ContextVar`
   - `ClientIdFilter` injects `client_id` and `ecs_task_id` into every log record automatically
   - Snakemake scripts receive `client_id` via config dict; subprocesses receive it via kwargs

3. **Infrastructure Logs Are Separate from App Logs**
   - EC2 setup logs (`/var/log/ecs-setup.log`) and app setup logs (`/var/log/app-setup.log`) live on the host filesystem
   - Lambda functions log to their own `/aws/lambda/*` log groups
   - Application logs flow through the ECS container log driver

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│ Application (uvicorn workers)                            │
│                                                          │
│  ClientIdLoggingMiddleware                               │
│    → extracts uid → hashes to client_id                  │
│    → stores in ContextVar                                │
│                                                          │
│  AppLogger (Python logging)                              │
│    → ClientIdFilter injects client_id + ecs_task_id      │
│    → fmt: "%(asctime)s %(levelprefix)s [%(name)s]        │
│           (pid:...) (task:...) (client:...) ..."         │
└──────────────┬───────────────────────┬───────────────────┘
               │                       │
               ▼                       ▼
┌──────────────────────┐  ┌────────────────────────────────┐
│ StreamHandler        │  │ ConcurrentTimedRotatingFile     │
│ (stdout → awslogs)   │  │ Handler (logs/studio.log)       │
│                      │  │ rotation: midnight, keep 365    │
└──────────┬───────────┘  └────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────┐
│ CloudWatch Log Groups                                    │
│                                                          │
│  /ecs/subscr-optinist-cloud-taskdef         (365 days)   │
│  /ecs/subscr-premium-optinist-cloud-taskdef (365 days)   │
│  /ecs/subscr-background-optinist-cloud-taskdef (30 days) │
│                                                          │
│  /aws/lambda/subscr-free-manager            (30 days)    │
│  /aws/lambda/subscr-free-cleanup            (30 days)    │
│  /aws/lambda/subscr-premium-manager         (30 days)    │
│  /aws/lambda/subscr-premium-cleanup         (30 days)    │
│  /aws/lambda/subscr-common-user-manager     (30 days)    │
└──────────────────────────────────────────────────────────┘
```

### Responsibility Matrix

| Log Category              | Audience         | Destination                  |
|---------------------------|------------------|------------------------------|
| AWS Task Logs             | Developer        | CloudWatch (awslogs driver)  |
| Instance Startup Logs     | Developer        | EC2 host filesystem          |
| App Workflow Logs         | User + Developer | Local file + CloudWatch      |
| Backup Settings           | Developer        | RDS snapshots, S3 versioning |
| Operation Routine Logging | Developer        | Lambda CloudWatch log groups |
| CloudWatch Metrics        | Developer        | CWAgent + custom namespaces  |

---

## Logging Configuration

### YAML Config Files

The application selects a logging config at startup based on the mode:

- **Standalone mode** uses `studio/config/logging.yaml`
- **Multiuser mode** uses `studio/config/logging.multiuser.yaml`

**File:** `studio/app/common/core/logger.py` - `AppLogger.get_logging_config()`

### Log Format

All handlers share a single format string:

```
%(asctime)s %(levelprefix)s [%(name)s] (pid:%(process)d) (task:%(ecs_task_id)s) (client:%(client_id)s) %(funcName)s():%(lineno)d - %(message)s
```

Example output:

```
2026-02-20 10:15:32,456 INFO [optinist] (pid:42) (task:abc123def) (client:a1b2c3d4e5f67890) run_workflow():128 - Starting workflow execution
```

### Handlers

| Handler                    | Class                                  | Level | Output                | Rotation  |
|----------------------------|----------------------------------------|-------|-----------------------|-----------|
| `console`                  | `logging.StreamHandler`                | DEBUG | stdout (→ CloudWatch) | None      |
| `rotating_file`            | `TimedRotatingFileHandler`             | DEBUG | `logs/studio.log`     | Midnight  |
| `rotating_file_concurrency`| `ConcurrentTimedRotatingFileHandler`   | DEBUG | `logs/studio.log`     | Midnight  |

On non-Windows platforms, `rotating_file` is replaced by `rotating_file_concurrency` at runtime to support multi-process (multi-worker uvicorn) file locking.

**File:** `studio/app/common/core/logger.py` - `LoggingConfigHelper._apply_concurrent_handler_if_supported()`

### Logger Hierarchy

| Logger       | Level | Handlers                           | Propagate |
|--------------|-------|------------------------------------|-----------|
| `root`       | INFO  | `[console, rotating_file]`         | N/A       |
| `optinist`   | DEBUG | Inherited from root                | Yes       |
| `snakemake`  | DEBUG | Standalone: `[console, rotating_file]`; Multiuser: `[rotating_file]` | No |

### Standalone vs Multiuser Differences

| Aspect            | Standalone             | Multiuser                    |
|-------------------|------------------------|------------------------------|
| Config file       | `logging.yaml`         | `logging.multiuser.yaml`     |
| Snakemake handler | `[console, rotating_file]` | `[rotating_file]` only   |
| `client_id`       | Always `"default"`     | Hashed from Firebase uid     |
| Log API filtering | No client_id filter    | Filters by current user      |

---

## AWS Task Logs (Developer Only)

### CloudWatch Log Groups

| Log Group                                        | Source       | Retention | Defined In            |
|--------------------------------------------------|--------------|-----------|----------------------|
| `/ecs/subscr-optinist-cloud-taskdef`             | Free ECS     | 365 days  | `monitoring.tf`       |
| `/ecs/subscr-premium-optinist-cloud-taskdef`     | Premium ECS  | 365 days  | `monitoring.tf`       |
| `/ecs/subscr-background-optinist-cloud-taskdef`  | Background   | 14 days   | `background_service.tf` |
| `/aws/lambda/subscr-free-manager`                | Free Manager | 14 days   | `free_manager.tf`     |
| `/aws/lambda/subscr-free-cleanup`                | Free Cleanup | 14 days   | `free_manager.tf`     |
| `/aws/lambda/subscr-premium-manager`             | Premium Mgr  | 14 days   | `premium_manager.tf`  |
| `/aws/lambda/subscr-premium-cleanup`             | Premium Clnp | 14 days   | `premium_manager.tf`  |
| `/aws/lambda/subscr-common-user-manager`         | User Mgr     | 14 days   | `common_user_manager.tf` |

### ECS Log Driver Configuration

All ECS task definitions use the `awslogs` driver with non-blocking mode:

```json
{
  "logDriver": "awslogs",
  "options": {
    "awslogs-group": "/ecs/<task-family>",
    "awslogs-region": "ap-northeast-1",
    "awslogs-stream-prefix": "ecs",
    "awslogs-create-group": "true",
    "awslogs-multiline-pattern": "^\\[\\d{4}-\\d{2}-\\d{2}\\s\\d{2}:\\d{2}:\\d{2}",
    "mode": "non-blocking",
    "max-buffer-size": "25m"
  }
}
```

The multiline pattern groups stack traces with the preceding log entry.

### CloudWatch Metric Filters

| Filter Name           | Log Group                | Pattern                                        | Metric Namespace       |
|-----------------------|--------------------------|-------------------------------------------------|------------------------|
| `user-cpu-usage`      | Free ECS                 | `[timestamp, level, user_id, cpu_usage]`        | `OptiNiSt/Application` |
| `premium-assignments` | Premium Manager Lambda   | `"Successfully assigned premium user*"`         | `OptiNiSt/Premium`     |

### RDS CloudWatch Log Exports

RDS exports three log types to CloudWatch automatically:

| Log Type    | Content                               |
|-------------|---------------------------------------|
| `error`     | MySQL error log                       |
| `general`   | All SQL statements (verbose)          |
| `slowquery` | Queries exceeding the slow threshold  |

**File:** `infrastructure/terraform/infrastructure.tf` - `aws_db_instance.main`

---

## Instance Startup Logs (Developer Only)

### EC2 User Data

**File:** `infrastructure/scripts/ecs-user-data.sh`

All output redirected to `/var/log/ecs-setup.log`:

```bash
exec > /var/log/ecs-setup.log 2>&1
```

Logs ECS cluster registration, package installation, swap setup, Docker build, EFS mount, and DB connectivity check. Runs once at instance launch.

### Application Setup

**File:** `infrastructure/scripts/app_setup.sh`

Logs to `/var/log/app-setup.log` via tee:

```bash
exec > >(tee -a "$LOGFILE") 2>&1
```

Covers secrets retrieval, infrastructure discovery, config file creation, database initialization, and Firebase admin verification.

### Container Startup

**File:** `cloud-startup.sh`

Logs to stdout (captured by ECS awslogs driver). Covers:

- DB connectivity wait loop (30 attempts, 2s intervals)
- Alembic migration execution
- EC2 instance ID retrieval from ECS metadata
- Uvicorn startup with worker count

### CloudWatch Agent Metrics

Configured in `ecs-user-data.sh`, publishes to `CWAgent` namespace:

| Metric                | Type       | Collection Interval |
|-----------------------|------------|---------------------|
| `mem_used_percent`    | Memory     | Default (60s)       |
| `cpu_usage_idle`      | CPU        | Default (60s)       |
| `cpu_usage_iowait`    | CPU        | Default (60s)       |
| `procstat` (all)      | Per-process| 60s                 |

Also collects `/proc/loadavg` to log group `/aws/ec2/loadavg`.

---

## App Workflow Logs (User + Developer)

### Log API

**File:** `studio/app/common/routers/logs.py`

The `/logs` endpoint serves paginated log data with filtering:

| Parameter | Default | Description                                   |
|-----------|---------|-----------------------------------------------|
| `offset`  | `-1`    | Start position (`-1` = end of file)           |
| `limit`   | `50`    | Max log entries to return                      |
| `reverse` | `true`  | Read logs in reverse (newest first)            |
| `search`  | `null`  | Text search within log entries                 |
| `levels`  | `[ALL]` | Filter by log level (INFO, ERROR, DEBUG, etc.) |

In multiuser mode, the API automatically filters logs by the current user's `client_id`. Standalone mode shows all logs.

### client_id Context Propagation

The `client_id` is propagated across three execution boundaries:

**1. HTTP Requests (middleware)**

`ClientIdLoggingMiddleware` runs on every HTTP request:
- Extracts uid from Firebase/JWT token
- Generates `client_id` = first 16 chars of MD5(uid)
- Stores in `ContextVar` for the request lifetime

**File:** `studio/app/common/core/middleware/logging_middleware.py`

**2. Subprocess (ProcessPoolExecutor)**

Parent passes `client_id` as a kwarg; the `@with_client_id_context` decorator restores it:

```python
client_id = get_client_id_for_subprocess()
executor.submit(func, arg1, client_id=client_id)
```

**File:** `studio/app/common/core/logger_context_helpers.py`

**3. Snakemake Scripts**

`client_id` is passed through snakemake's config dict and restored at script entry:

```python
init_client_id_from_snakemake_config(snakemake.config)
```

Used in: `rules/data.py`, `rules/func.py`, `rules/post_process.py`, `rules/run_edit_ROI.py`

**File:** `studio/app/common/core/workflow/workflow_runner.py` - passes `client_id` into snakemake config

### Snakemake Error Logs

**File:** `studio/app/common/core/snakemake/smk_status_logger.py`

Per-workflow error logs written to `{OUTPUT_DIR}/{workspace_id}/{unique_id}/error.log`:

- Logger created per workflow run with a dedicated `FileHandler`
- Level: ERROR only
- Format: `%(asctime)s : %(levelname)s - %(filename)s - %(message)s`
- Previous error log deleted on new workflow run
- Retrieved via `SmkStatusLogger.get_error_content()` for UI display

### Log Reader

**File:** `studio/app/common/core/utils/log_reader.py`

`LogRecordReader` parses the structured log format using regex and supports:

- Multiline log entry detection (entries starting with timestamp pattern)
- Level-based filtering
- `client_id`-based filtering (multiuser mode only)
- Exclusion of polling endpoints (`GET /logs`, `OPTIONS /logs`)

### Frontend Logging

Frontend uses `console.log` and `console.error` in the browser. There is no server-side collection of frontend logs.

---

## Backup Settings

| Resource   | Mechanism              | Retention         | Config Location        |
|------------|------------------------|-------------------|------------------------|
| RDS        | Automated backups      | 35 days           | `infrastructure.tf`    |
| S3         | Bucket versioning      | Enabled (no lifecycle) | `infrastructure.tf` |
| EFS        | None                   | N/A               | N/A                    |
| App logs   | `TimedRotatingFileHandler` | 365 daily files | `logging.yaml`         |
| ALB logs   | S3 access logs         | 30 days             | `infrastructure.tf`   |

---

## Operation Routine Logging

### Lambda Schedules

| Lambda                      | Schedule       | Log Group                             |
|-----------------------------|----------------|---------------------------------------|
| `subscr-free-manager`       | Every 5 min    | `/aws/lambda/subscr-free-manager`     |
| `subscr-premium-manager`    | Every 15 min   | `/aws/lambda/subscr-premium-manager`  |
| `subscr-premium-cleanup`    | Every 1 hour   | `/aws/lambda/subscr-premium-cleanup`  |
| `subscr-common-user-manager`| Every 10 min   | `/aws/lambda/subscr-common-user-manager` |
| `subscr-cost-tracker`       | Every 1 hour   | (shares premium manager role)         |

The free manager also triggers on ASG lifecycle events (instance launch/terminate) via EventBridge.

### CloudWatch Alarms

| Alarm                              | Metric                    | Threshold    | Namespace              |
|------------------------------------|---------------------------|--------------|------------------------|
| `subscr-optinist-cpu-high`         | CPUUtilization            | > 60%        | AWS/ECS                |
| `subscr-optinist-memory-high`      | MemoryUtilization         | > 80%        | AWS/ECS                |
| `subscr-optinist-cpu-low`          | CPUUtilization            | < 20%        | AWS/ECS                |
| `subscr-optinist-memory-low`       | MemoryUtilization         | < 10%        | AWS/ECS                |
| `subscr-optinist-load-average-high`| CPUUtilization            | > 80%        | AWS/EC2                |
| `subscr-optinist-high-iowait`      | cpu_usage_iowait          | > 30%        | CWAgent                |
| `subscr-optinist-rds-cpu-high`     | CPUUtilization            | > 80%        | AWS/RDS                |
| `subscr-optinist-rds-connections-high` | DatabaseConnections   | > 80         | AWS/RDS                |
| `subscr-optinist-rds-storage-low`  | FreeStorageSpace          | < 10 GB      | AWS/RDS                |
| `subscr-optinist-efs-burst-credits-low` | BurstCreditBalance   | < 1 TB       | AWS/EFS                |
| `subscr-optinist-efs-throughput-high` | PercentIOLimit         | > 80%        | AWS/EFS                |
| `subscr-optinist-alb-5xx-errors`   | HTTPCode_Target_5XX_Count | > 10/min     | AWS/ApplicationELB     |
| `subscr-optinist-alb-response-time-high` | TargetResponseTime  | > 5s         | AWS/ApplicationELB     |
| `subscr-background-task-stopped`   | RunningTaskCount          | < 1          | ECS/ContainerInsights  |
| `subscr-background-cpu-high`       | CpuUtilized               | > 400 units  | ECS/ContainerInsights  |
| `subscr-background-memory-high`    | MemoryUtilized            | > 600 MB     | ECS/ContainerInsights  |
| `subscr-premium-monthly-cost-high` | TotalMonthlyCost          | > $500/day   | OptiNiSt/Cost          |
| `subscr-premium-cpu-high`          | CPUUtilization            | > 80%        | AWS/ECS                |
| `subscr-premium-memory-high`       | MemoryUtilization         | > 85%        | AWS/ECS                |

### CloudWatch Dashboard

**Dashboard:** `subscr-optinist-monitoring`

7 rows of widgets covering:

1. Free vs Premium CPU/Memory comparison + ASG capacity
2. ECS service metrics + cost tracking
3. ALB performance + user tier operations
4. EC2 load average/I/O wait + RDS/EFS health
5. ASG lifecycle + autoscaling triggers
6. Background jobs + Lambda operations
7. Alarm status overview (all 19 alarms)

---

## Configuration

### Environment Variables

| Variable                       | Purpose                                       | Default   |
|--------------------------------|-----------------------------------------------|-----------|
| `LOG_LEVEL`                    | Set in ECS task definitions (currently unused) | `"DEBUG"` |
| `UVICORN_ACCESS_LOG`           | Enable uvicorn access log (`1`/`0`)            | `"1"`     |
| `PYTHONUNBUFFERED`             | Disable Python output buffering                | `"1"`     |
| `DISABLE_BACKGROUND_SCHEDULER` | Disable APScheduler in API workers             | `"1"`     |
| `CLOUDWATCH_LOG_GROUP`         | Passed to container (informational only)       | Varies    |
| `ECS_CONTAINER_METADATA_URI_V4`| Auto-set by ECS; used to fetch task ID         | (ECS)     |

---

## Edge Case Handling

### 1. `LOG_LEVEL` Environment Variable Is Unused

**Problem:** All three ECS task definitions set `LOG_LEVEL=DEBUG`, but the Python logging configuration reads levels exclusively from the YAML config files. The env var has no effect.

**Solution:** Either remove the env var from task definitions or add code in `AppLogger.get_logging_config()` to read and apply it.

### 2. Frontend Logs Are Not Collected

**Problem:** Frontend `console.log`/`console.error` calls are browser-only. Client-side errors are invisible to backend monitoring.

**Solution:** Consider adding a frontend error reporting endpoint or integrating a browser error tracking service.

---

## Key Functions Reference

| Function | File | Purpose |
|----------|------|---------|
| `AppLogger.get_logging_config()` | `studio/app/common/core/logger.py` | Loads YAML config, applies concurrent handler, adds ClientIdFilter |
| `AppLogger.init_logger()` | `studio/app/common/core/logger.py` | One-time logging initialization via `dictConfig()` |
| `AppLogger.generate_client_id()` | `studio/app/common/core/logger.py` | MD5 hash of uid, truncated to 16 chars |
| `ClientIdLoggingMiddleware.__call__()` | `studio/app/common/core/middleware/logging_middleware.py` | Extracts uid from request, sets client_id in context |
| `with_client_id_context()` | `studio/app/common/core/logger_context_helpers.py` | Decorator for subprocess client_id propagation |
| `init_client_id_from_snakemake_config()` | `studio/app/common/core/logger_context_helpers.py` | Restores client_id in snakemake script processes |
| `get_log_data()` | `studio/app/common/routers/logs.py` | Log API endpoint with pagination and filtering |
| `LogRecordReader.validate()` | `studio/app/common/core/utils/log_reader.py` | Filters log entries by level and client_id |
| `SmkStatusLogger.get_logger()` | `studio/app/common/core/snakemake/smk_status_logger.py` | Creates per-workflow error logger with FileHandler |
| `LoggingConfigHelper.load_and_configure_logging_config()` | `studio/app/common/core/logger.py` | Unified config loading with path adjustment and concurrent handler setup |
