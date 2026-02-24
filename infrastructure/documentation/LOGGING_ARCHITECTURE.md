# Logging Architecture: Runtime Log Level Control

## Executive Summary

- **`LOG_LEVEL` environment variable** controls log verbosity at runtime without modifying YAML config files
- **`--log-level` CLI argument** provides local developer override (takes precedence over env var)
- **Single-knob model** overrides all loggers and handlers uniformly when set
- **YAML defaults preserved** when `LOG_LEVEL` is unset (local development uses existing YAML values)
- **ECS production default** is `INFO` to reduce CloudWatch noise and cost

---

## Key Architectural Principles

1. **Closest to Developer Wins**
   - CLI argument overrides env var, env var overrides YAML
   - Precedence: `--log-level` > `LOG_LEVEL` env var > YAML config

2. **Non-Breaking Defaults**
   - When `LOG_LEVEL` is unset, behavior is identical to before this feature
   - YAML files remain the source of truth for local standalone development
   - Invalid `LOG_LEVEL` values log a warning and fall through to YAML defaults

3. **Uniform Override**
   - When `LOG_LEVEL` is set, all loggers (root, optinist, snakemake) and all handlers (console, rotating_file) receive the same level
   - Simple mental model: one knob controls everything

4. **Child Process Consistency**
   - Env vars are inherited by child processes (snakemake workers, ProcessPoolExecutor)
   - Each child calls `AppLogger.init_logger()` which re-reads `LOG_LEVEL` from the environment

---

## Architecture Overview

```
ECS Task Definition (LOG_LEVEL=INFO)
  -> cloud-startup.sh
    -> main.py --host --port --workers [--log-level DEBUG]
      -> __main_unit__.main()
        -> AppLogger.get_logging_config()
          -> LoggingConfigHelper.load_and_configure_logging_config()
            -> Reads YAML file (base config)
          -> Adds ClientIdFilter
          -> Reads LOG_LEVEL env var -> _apply_log_level_override()
        -> If --log-level CLI arg set:
          -> _apply_log_level_override() (overrides env var)
        -> uvicorn.run(log_config=logging_config)
```

### Level Override Behavior

| YAML Key | Without `LOG_LEVEL` | With `LOG_LEVEL=WARNING` |
|----------|---------------------|--------------------------|
| `root.level` | `INFO` | `WARNING` |
| `loggers.optinist.level` | `DEBUG` | `WARNING` |
| `loggers.snakemake.level` | `DEBUG` | `WARNING` |
| `handlers.console.level` | `DEBUG` | `WARNING` |
| `handlers.rotating_file.level` | `DEBUG` | `WARNING` |

---

## Implementation Details

### Config Loading Chain

The override is applied in `AppLogger.get_logging_config()` after YAML loading and filter setup:

```python
def get_logging_config():
    # 1. Load YAML config (base defaults)
    logging_config = LoggingConfigHelper.load_and_configure_logging_config(...)

    # 2. Add ClientIdFilter to all handlers
    # ...

    # 3. Apply LOG_LEVEL env var override if set
    log_level = os.environ.get("LOG_LEVEL")
    if log_level:
        logging_config = LoggingConfigHelper._apply_log_level_override(
            logging_config, log_level
        )

    return logging_config
```

The CLI argument is applied in `__main_unit__.main()` after `get_logging_config()` returns, ensuring it takes precedence over the env var.

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
| `LOG_LEVEL` | Override log verbosity at runtime | Unset (uses YAML) | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |

| CLI Argument | Purpose | Default |
|--------------|---------|---------|
| `--log-level` | Override log verbosity (local dev) | `None` (uses env var or YAML) |

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
| `LoggingConfigHelper._apply_log_level_override()` | Apply a level string to all loggers and handlers in a config dict |
| `AppLogger.get_logging_config()` | Load YAML config, add filters, apply `LOG_LEVEL` env var |
| `__main_unit__.main()` | Apply `--log-level` CLI override after config load |

---

## AWS Resources

| Resource | Configuration |
|----------|--------------|
| ECS Task Definitions (Free, Premium, Background) | `LOG_LEVEL=INFO` environment variable |
