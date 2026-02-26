# Logging Architecture: Runtime Log Level Control

## Executive Summary

- **Python logging** always uses YAML config defaults (optinist=DEBUG, snakemake=DEBUG) so all log levels reach CloudWatch
- **`LOG_LEVEL` environment variable** controls which levels appear in the **frontend log viewer**, not Python logging
- **`--log-level` CLI argument** provides local developer override for Python logging (useful for reducing console noise locally)
- **ECS production default** is `LOG_LEVEL=INFO`, meaning the frontend hides DEBUG logs while CloudWatch captures everything
- **Frontend "ALL" filter** excludes DEBUG logs; the DEBUG filter option is hidden when `LOG_LEVEL` >= INFO

---

## Key Architectural Principles

1. **Two Separate Concerns**
   - Python logging level (controlled by YAML config) determines what is **written** to logs/CloudWatch
   - `LOG_LEVEL` env var determines what the **frontend log viewer** displays to users
   - CloudWatch always has the full picture; the frontend filters for readability

2. **Non-Breaking Defaults**
   - When `LOG_LEVEL` is unset, the frontend shows all levels including DEBUG
   - YAML files remain the source of truth for Python logging levels
   - Invalid `LOG_LEVEL` values fall through to showing all levels

3. **Frontend Filtering**
   - `GET /logs/level` endpoint returns available filter levels based on `LOG_LEVEL`
   - The "ALL" filter in the log viewer excludes DEBUG (use CloudWatch directly for DEBUG)
   - The DEBUG filter option is hidden from the UI when `LOG_LEVEL` >= INFO

4. **Child Process Consistency**
   - Env vars are inherited by child processes (snakemake workers, ProcessPoolExecutor)
   - Each child calls `AppLogger.init_logger()` which uses YAML defaults for logging
   - `--log-level` CLI arg still overrides Python logging for local development

---

## Logging Level Policy

This section defines **when to use each log level**. All new logging calls
must follow these rules. Existing calls that violate these rules should be
corrected when the surrounding code is modified.

### Level Definitions

#### CRITICAL

System is unusable or data integrity is compromised.

- Partial operations that leave the system in an inconsistent state
  (e.g., Firebase account deleted but DB record remains)
- Unrecoverable startup failures

Expected frequency: near-zero in healthy systems.

#### ERROR

An operation **failed and cannot proceed**. The caller will receive an error
response, or a job item will be skipped/retried.

Use ERROR when:
- A database write/read fails unexpectedly
- An external API call fails (S3, Stripe, Firebase) and no fallback exists
- A workflow execution fails
- A business-logic invariant is violated

Do NOT use ERROR when:
- The system handles the failure gracefully (use WARNING)
- The failure is expected in normal operation (use WARNING or DEBUG)

#### WARNING

Something **unexpected happened but the system recovered** or degraded
gracefully. A human should review these periodically but no immediate action
is required.

Use WARNING when:
- A resource is missing but the code returns early or uses a fallback
  (e.g., missing workflow directory, missing experiment config)
- A non-critical cleanup operation fails (e.g., temp file deletion after
  successful computation)
- A caught exception represents a client-side issue, not a server bug
  (e.g., validation error -> 400/401, expired auth token)
- A platform detection or optional feature probe fails
- A caught exception that was handled but may indicate a deeper issue

Do NOT use WARNING when:
- The condition is the normal/happy path (use DEBUG or INFO)
- The operation succeeded (use INFO)

#### INFO

A **significant business event or operational milestone** occurred. INFO is
the default production-visible level in the frontend log viewer.

Use INFO when:
- A high-level operation starts or completes (workflow run, background job,
  experiment copy/delete)
- A business event occurs (user login, subscription created/renewed/cancelled,
  email sent)
- Infrastructure initializes (cloud services connected, S3 bucket created,
  DB connection verified)
- A job-level summary is produced ("Processed N items", "Cleanup complete")

Do NOT use INFO when:
- Logging per-item details in a batch operation (use DEBUG)
- Logging internal function parameters or return values (use DEBUG)
- Logging step-by-step traces through a multi-step process (use DEBUG)
- Logging sensitive data: auth tokens, verification links, webhook secrets
  (use DEBUG -- these should only appear when explicitly requested)

Rule of thumb: **one INFO message per high-level operation**. If a loop
body contains `logger.info()`, it almost certainly should be `logger.debug()`.

#### DEBUG

**Diagnostic detail** for developers investigating issues. Always captured
by CloudWatch but hidden from the frontend log viewer when `LOG_LEVEL=INFO`.

Use DEBUG when:
- Logging step-by-step progress through a multi-step process (webhook
  processing, checkout flow, S3 sync)
- Logging per-item details in batch operations (per-file download,
  per-experiment cleanup, per-user reconciliation)
- Logging function parameters, return values, or intermediate state
- Logging cache hits/misses, skip decisions, no-op conditions
- Logging sensitive data that aids debugging (verification links, password
  reset links, webhook signatures, Lambda response bodies)
- Logging the happy/normal path of a check ("no limit warning" = user is
  within limits = nothing to report at higher levels)

### Decision Flowchart

```
Is the system in an inconsistent/unusable state?
  YES -> CRITICAL

Did the operation fail and the caller will get an error?
  YES -> ERROR

Did something unexpected happen but the system handled it?
  YES -> WARNING

Is this a significant business event or operational milestone?
  YES -> INFO

Everything else (traces, per-item details, parameters, sensitive data)
  -> DEBUG
```

### Anti-Patterns

These are the most common mistakes found during the log-level audit. Avoid
them in new code and fix them when modifying existing code.

| Anti-Pattern | Correct Level | Example |
|---|---|---|
| Caught exception logged as INFO | WARNING | `"Failed to detect Apple Silicon: {e}"` |
| Happy-path / no-problem-found logged as WARNING | DEBUG | `"No limit warning for user {id}"` |
| Successful recovery logged as WARNING | INFO | `"Bucket recovery on login: {bucket}"` |
| Per-item batch detail logged as INFO | DEBUG | `"Deleting input directory: {dir}"` |
| Internal state dump logged as INFO | DEBUG | `"Lambda response body: {body}"` |
| Sensitive credentials logged as INFO | DEBUG | `"Verification link: {url}"` |
| Graceful early-return logged as ERROR | WARNING | `"'{path}' does not exist"` (returns early) |
| Non-critical cleanup failure logged as ERROR | WARNING | `"Failed to cleanup memmap files"` |

### Logger Usage

All modules must use `AppLogger.get_logger()` from
`studio.app.common.core.logger`. Do not use `logging.getLogger()` directly.

```python
# Correct
from studio.app.common.core.logger import AppLogger
logger = AppLogger.get_logger()

# Incorrect -- do not use
import logging
logging.getLogger().info(...)
```

This ensures all log output passes through the centralized configuration
(YAML-based levels, `ClientIdFilter`, concurrent file handler, `LOG_LEVEL`
frontend filtering).

---

## Architecture Overview

```
ECS Task Definition (LOG_LEVEL=INFO)
  -> cloud-startup.sh
    -> main.py --host --port --workers [--log-level DEBUG]
      -> __main_unit__.main()
        -> AppLogger.get_logging_config()
          -> LoggingConfigHelper.load_and_configure_logging_config()
            -> Reads YAML file (base config: optinist=DEBUG, root=INFO)
          -> Adds ClientIdFilter
          -> NOTE: LOG_LEVEL env var does NOT override Python logger levels
        -> If --log-level CLI arg set:
          -> _apply_log_level_override() (local dev only)
        -> uvicorn.run(log_config=logging_config)

Frontend log viewer:
  -> GET /logs/level (reads LOG_LEVEL env var -> returns available filter levels)
  -> GET /logs?levels=... (log reader excludes DEBUG from "ALL" filter)
```

### Python Logging vs Frontend Display

| Concern | What controls it | Production default |
|---------|------------------|--------------------|
| Python logging level | YAML config (`logging.multiuser.yaml`) | optinist=DEBUG, root=INFO |
| CloudWatch capture | Python logging level | Everything including DEBUG |
| Frontend "ALL" filter | Backend log reader | Excludes DEBUG |
| Frontend filter options | `LOG_LEVEL` env var via `/logs/level` | INFO, WARNING, ERROR, CRITICAL |

---

## Implementation Details

### Config Loading Chain

`AppLogger.get_logging_config()` loads YAML config and adds filters. `LOG_LEVEL` is **not** applied to Python loggers -- it only controls the frontend:

```python
def get_logging_config():
    # 1. Load YAML config (base defaults: optinist=DEBUG, root=INFO)
    logging_config = LoggingConfigHelper.load_and_configure_logging_config(...)

    # 2. Add ClientIdFilter to all handlers
    # ...

    # NOTE: LOG_LEVEL env var controls the frontend log viewer filter,
    # not the Python logging level.
    return logging_config
```

The CLI `--log-level` argument is still applied in `__main_unit__.main()` for local development to override Python logging levels.

### Frontend Level Endpoint

`GET /logs/level` reads `LOG_LEVEL` env var at startup and returns levels >= that threshold:

```python
# With LOG_LEVEL=INFO -> returns ["INFO", "WARNING", "ERROR", "CRITICAL"]
# With LOG_LEVEL=DEBUG or unset -> returns all five levels
```

### Log Reader ALL Filter

When the frontend sends `levels=ALL`, the backend log reader returns everything **except** DEBUG. Users who need DEBUG logs should use CloudWatch directly.

### Valid Values

Standard Python logging levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. Input is case-insensitive, normalized to uppercase.

### Components Not Affected

| Component | Reason |
|-----------|--------|
| `SmkStatusLogger` | Independent per-workflow error logger, always captures `ERROR` |
| Lambda functions | Use `print()`, not Python logging |
| YAML config files | Remain as fallback defaults when `LOG_LEVEL` is unset |

---

## How to Change Log Level

The log level is read once at process startup. There is no hot-reload
mechanism -- changing the level always requires restarting the process.

### Local Development

```bash
# Option A: CLI argument (recommended, fastest feedback loop)
poetry run python main.py --log-level DEBUG

# Option B: Env var (useful for matching deployed behavior)
LOG_LEVEL=WARNING poetry run python main.py

# Option C: No override (uses YAML defaults: root=INFO, optinist=DEBUG)
poetry run python main.py
```

To change the level, Ctrl-C the server and re-run with the new value.
Uvicorn `--reload` watches file changes only, not env var changes.

### Deployed ECS Containers

ECS containers cannot be SSHed into to export an env var. The `LOG_LEVEL`
value is baked into the ECS task definition at deploy time. To change it:

**Option A: Terraform redeploy (persistent change)**

1. Edit `LOG_LEVEL` in the task definition (`compute.tf` or
   `background_service.tf`)
2. `terraform apply` -- ECS rolls out new tasks with the new value
3. Revert the change and re-apply when done debugging

**Option B: One-off ECS task with override (no Terraform change)**

```bash
aws ecs run-task \
  --cluster subscr-optinist-cluster \
  --task-definition subscr-optinist-cloud-taskdef \
  --overrides '{
    "containerOverrides": [{
      "name": "optinist",
      "environment": [{"name": "LOG_LEVEL", "value": "DEBUG"}]
    }]
  }'
```

This launches a single task with `DEBUG` without touching the running
service. Useful for reproducing an issue in an isolated container.

**Option C: ECS service env var override (temporary service-wide)**

Update the running service's task definition override via the AWS Console:
1. Go to ECS > Clusters > Service > Update
2. Edit the container environment variables
3. Set `LOG_LEVEL=DEBUG`
4. Deploy -- ECS performs a rolling replacement
5. Revert when done

In all cases, the startup log line `Starting Optinist server on ...
(log_level=...)` confirms the active level in CloudWatch.

---

## Edge Case Handling

### 1. Invalid LOG_LEVEL Value

**Problem:** `LOG_LEVEL=VERBOSE` or other invalid string.

**Solution:** `_apply_log_level_override()` validates against the allowed set. Invalid values trigger `warnings.warn()` and YAML defaults are used unchanged.

**Guarantee:** Application never crashes due to invalid log level.

### 2. Empty LOG_LEVEL String

**Problem:** `LOG_LEVEL=""` set in environment.

**Solution:** `os.environ.get("LOG_LEVEL")` returns `""` which is falsy. The override is skipped entirely.

**Guarantee:** Empty string behaves identically to unset.

### 3. CLI Conflicts with Env Var

**Problem:** `LOG_LEVEL=WARNING` in env, `--log-level DEBUG` on CLI.

**Solution:** CLI wins by design. `get_logging_config()` applies env var first. `main()` applies CLI arg on top.

**Guarantee:** Developer intent (CLI) always overrides deployment config (env var).

### 4. No Hot Reload

**Problem:** Developer changes `LOG_LEVEL` while server is running.

**Solution:** The level is read once at startup. A full process restart is required. See "How to Change Log Level" above for the workflows.

**Guarantee:** Log level is consistent for the lifetime of a server process.

### 5. Multiple Uvicorn Workers

**Problem:** Production runs multiple workers, each forks independently.

**Solution:** Each worker inherits the same environment and calls `get_logging_config()` during fork initialization.

**Guarantee:** All workers get the same log level.

---

## Configuration

| Variable | Purpose | Default | Valid Values |
|----------|---------|---------|--------------|
| `LOG_LEVEL` | Control frontend log viewer filter levels | Unset (shows all) | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |

| CLI Argument | Purpose | Default |
|--------------|---------|---------|
| `--log-level` | Override Python logging level (local dev) | `None` (uses YAML defaults) |

---

## Testing

### Unit Tests

**File:** `studio/tests/app/common/core/test_logger_log_level.py`

| Test | Purpose |
|------|---------|
| `test_sets_all_levels` | Valid level overrides root, loggers, and handlers |
| `test_invalid_value_warns_and_keeps_defaults` | Invalid level emits warning, config unchanged |
| `test_case_insensitive` | Lowercase input normalized to uppercase |
| `test_handler_without_level_key_is_skipped` | Handlers without `level` key are not modified |
| `test_all_valid_levels_accepted` | Parametric test for all 5 valid levels |
| `test_env_var_overrides_config` | `LOG_LEVEL` env var applies override |
| `test_empty_string_env_var_skipped` | Empty string treated as unset |
| `test_unset_env_var_skipped` | Unset env var preserves YAML defaults |

### Manual Verification

```bash
# Verify WARNING+ only in console
LOG_LEVEL=WARNING poetry run python main.py

# Verify DEBUG lines appear
poetry run python main.py --log-level DEBUG

# Verify startup log includes effective level
# Look for: "Starting Optinist server on ... (log_level=...)"
```

---

## Key Functions Reference

| Function | Purpose |
|----------|---------|
| `LoggingConfigHelper._apply_log_level_override()` | Apply a level string to all loggers and handlers (used by `--log-level` CLI only) |
| `AppLogger.get_logging_config()` | Load YAML config and add filters (does not apply `LOG_LEVEL` env var) |
| `__main_unit__.main()` | Apply `--log-level` CLI override after config load |
| `GET /logs/level` | Return available frontend filter levels based on `LOG_LEVEL` env var |

---

## AWS Resources

| Resource | Configuration |
|----------|--------------|
| ECS Task Definitions (Free, Premium, Background) | `LOG_LEVEL=INFO` environment variable |
