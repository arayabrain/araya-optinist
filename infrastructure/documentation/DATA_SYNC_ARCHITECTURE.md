# Multi-Instance Data Sync: On-Demand Experiment Synchronization

## Executive Summary

- **Data sync system** ensures experiment data is available when users migrate between instances (free tier shared, premium dedicated)
- **S3 as source of truth** for all experiment data; local filesystems are treated as caches
- **Three-tier sync** downloads only what each operation needs (metadata, visualization, or full data)
- **Background pre-sync** via Lambda triggers metadata download after migration for instant experiment listing
- **Graceful degradation** ensures on-demand sync works even if background pre-sync fails

---

## Key Architectural Principles

1. **S3 as Source of Truth**
   - All experiment data persists in S3; local instance filesystems are ephemeral caches
   - After migration, the new instance has an empty filesystem but all data is recoverable from S3
   - Sync never writes to S3, only reads from it

2. **Tiered Sync (Minimize Bandwidth)**
   - Downloads only the files needed for the current operation
   - Viewing results: JSON + TIFF only (~MBs); Edit ROI: full PKL/NWB files (~100s of MBs)
   - Background task pre-fetches heavier files while the user views lighter data

3. **Graceful Degradation**
   - Lambda-triggered background sync is an optimization, not a requirement
   - If background sync fails, on-demand sync transparently fetches data when endpoints are accessed
   - Every data-access endpoint calls `ensure_synced_async()` as a safety net

4. **Fire-and-Forget Lambda Integration**
   - Lambda triggers sync via internal API but does not block on the result
   - Sync failures are logged but do not fail the migration
   - Internal API is rate-limited and secret-protected

---

## Architecture Overview

### Multi-Instance Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          AWS Cloud                                  │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────┐     ┌─────────────────────────────────────────┐    │
│  │   ALB       │────▶│  Free Tier Shared Instances              │    │
│  │  (Routing)  │     │  ┌─────────┐ ┌─────────┐ ┌─────────┐   │    │
│  └─────────────┘     │  │ ECS #1  │ │ ECS #2  │ │ ECS #3  │   │    │
│         │            │  │ Users   │ │ Users   │ │ Users   │   │    │
│         │            │  │ 1,2,3   │ │ 4,5,6   │ │ 7,8,9   │   │    │
│         │            │  └─────────┘ └─────────┘ └─────────┘   │    │
│         │            └─────────────────────────────────────────┘    │
│         │                                                           │
│         │            ┌─────────────────────────────────────────┐    │
│         └───────────▶│  Premium Dedicated Instances            │    │
│                      │  ┌─────────┐ ┌─────────┐                │    │
│                      │  │ Premium │ │ Premium │                │    │
│                      │  │ User A  │ │ User B  │                │    │
│                      │  └─────────┘ └─────────┘                │    │
│                      └─────────────────────────────────────────┘    │
│                                                                     │
│                      ┌─────────────────────────────────────────┐    │
│                      │              S3 Storage                  │    │
│                      │  (Source of truth for all experiments)  │    │
│                      └─────────────────────────────────────────┘    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Migration Scenarios

| Scenario | Description |
|----------|-------------|
| Free tier rebalancing | User moved between shared instances for load balancing |
| Premium upgrade | User migrated to dedicated instance |
| Premium downgrade | User migrated back to shared pool |
| Instance replacement | Old instance terminated, user moved to new one |

### The Problem

After migration, the user's new instance has:
- **Database:** User record points to new instance
- **Local filesystem:** Empty (no experiment data)
- **S3:** All experiment data (source of truth)

Without sync, the user experiences empty experiment lists, "file not found" errors, and failed reproduce/edit operations.

---

## Implementation Details

### Three-Tier Synchronization

#### Tier 1: On-Demand Visualization Sync

**When:** User views experiment results (visualize tab)
**What:** JSON timeseries data, TIFF images
**Why:** Fast loading for viewing results, skips large PKL/NWB files

```
┌──────────────────────────────────────────────────────────┐
│ 1. User clicks "Visualize" for an experiment              │
└──────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────┐
│ 2. Frontend calls:                                        │
│    → POST /outputs/sync/{workspace_id}/{unique_id}        │
└──────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────┐
│ 3. Backend downloads visualization files from S3          │
│    → sync_mode="visualization"                            │
│    → .json, .tif/.tiff, .yaml files                       │
│    → Input data files (referenced images)                 │
└──────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────┐
│ 4. Background task starts full sync for Edit ROI          │
│    → sync_mode="all" (downloads PKL, NWB in background)   │
└──────────────────────────────────────────────────────────┘
```

#### Tier 2: Full Sync (Edit ROI / Reproduce)

**When:** User clicks Edit ROI or Reproduce
**What:** All experiment files including PKL, NWB
**Why:** Required for data manipulation operations

```
┌──────────────────────────────────────────────────────────┐
│ 1. User clicks "Edit ROI" or "Reproduce"                  │
└──────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────┐
│ 2. Endpoint checks sync status                            │
│    → RemoteSyncStatusFileUtil.check_sync_status_unsynced()│
└──────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────┐
│ 3a. Already synced → Proceed immediately                  │
│ 3b. Needs sync → Download all files from S3               │
└──────────────────────────────────────────────────────────┘
```

#### Tier 3: Background Metadata Sync (Lambda-Triggered)

**When:** Immediately after user migration (optimization)
**What:** All experiment YAML files (experiment.yaml, workflow.yaml)
**Why:** Pre-populates experiment list so it's available when user opens the app

```
┌──────────────────────────────────────────────────────────┐
│ 1. Lambda migrates user to new instance                   │
│    → Updates database assignment                          │
└──────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────┐
│ 2. Lambda calls internal API to trigger sync              │
│    → POST /system-internal/sync-experiments/{user_id}     │
│    → Headers: X-Internal-Secret: <secret>                 │
└──────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────┐
│ 3. New instance downloads experiment metadata from S3     │
│    → Runs as background task (non-blocking)               │
│    → Downloads experiment.yaml, workflow.yaml              │
└──────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────┐
│ 4. User's experiment list is immediately available        │
│    → Full data synced on-demand (Tier 1/2)                │
└──────────────────────────────────────────────────────────┘
```

This tier is an optimization. If it fails, Tier 1 and 2 still work -- endpoints sync metadata on-demand when accessed via `ensure_synced_async()`.

### File Sync Modes

| Mode | Files Downloaded | Use Case |
|------|------------------|----------|
| `thumbnails_only` | `*_thumb.png` | Fast Dataview thumbnails |
| `essential_only` | `.yaml`, `.yml`, `.json` | Metadata sync for listing |
| `visualization` | `.json`, `.tif`, `.tiff`, `.yaml` | Viewing results |
| `all` | All files including `.pkl`, `.nwb` | Edit ROI, Reproduce |

File patterns defined in `studio/app/const.py`:

```python
ThumbnailConst.FILE_PATTERNS = ("_thumb.png",)
ESSENTIAL_SYNC_PATTERNS = (".yaml", ".yml", ".json")
LARGE_FILE_PATTERNS = tuple(ACCEPT_FILE_EXT.ALL_EXT.value + [".pkl"])
VISUALIZATION_SYNC_PATTERNS = (".json", ".tif", ".tiff", ".yaml")
```

### Internal API Endpoint

#### sync_user_experiments()

**File:** `studio/app/common/routers/internal.py`
**Purpose:** Trigger experiment metadata sync for a user after Lambda migration
**Input:** `user_id` (path param), protected by `verify_internal_secret` dependency
**Output:** Background task that downloads all experiment metadata from S3
**Security:** `INTERNAL_API_SECRET` env var, rate-limited (10s per user), constant-time secret comparison

### Lambda Integration

#### trigger_experiment_sync()

**File:** `infrastructure/terraform/premium_manager_package/premium_manager.py`
**File:** `infrastructure/terraform/free_manager_package/free_user_utils.py`
**Purpose:** Fire-and-forget call to internal API after user migration
**Input:** `user_id`
**Output:** `bool` indicating if the request was sent successfully
**Behavior:** Does not block migration; logs success/failure; degrades gracefully

### On-Demand Sync Pattern

#### ExptConfigReader.ensure_synced_async()

**File:** `studio/app/common/core/experiment/experiment_reader.py`
**Purpose:** Ensure experiment metadata exists locally, syncing from S3 if needed
**Input:** `workspace_id`, `unique_id`, `remote_bucket_name`
**Output:** `True` if config exists (or was synced), `False` if sync failed
**Calls:** `RemoteStorageSimpleReader` -> `download_experiment_meta()`

Used in routers before accessing experiment data:

```python
await ExptConfigReader.ensure_synced_async(
    workspace_id, unique_id, remote_bucket_name
)
```

### Visualization Sync Endpoint

#### sync_visualization_files()

**File:** `studio/app/common/routers/outputs.py`
**Purpose:** Lazy-load visualization files from S3, then trigger background full sync
**Input:** `workspace_id`, `unique_id`, `remote_bucket_name` via `get_outputs_remote_bucket_name`
**Output:** `bool` indicating sync success
**Calls:** `RemoteStorageSimpleReader.download_experiment()` -> `_background_full_sync()`

### Sync Status Tracking

**Path:** `{output_dir}/{workspace_id}/{unique_id}/remote_sync_stat.json`

```json
{
  "remote_bucket_name": "optinist-user-123-abc123",
  "remote_storage_type": "2",
  "action": "download",
  "status": "success",
  "timestamp": "2024-01-15T10:30:00"
}
```

| Status | Meaning |
|--------|---------|
| `processing` | Sync in progress |
| `success` | Full sync completed |
| `error` | Sync failed |
| (no file) | Never synced / needs sync |

```python
-- Key constraint: only sync when status file is missing or failed
RemoteSyncStatusFileUtil.check_sync_status_unsynced(workspace_id, unique_id)
RemoteSyncStatusFileUtil.check_sync_status_success(workspace_id, unique_id)
```

**Public tier note:** The public tier's raw-input cache lives on shared EFS and is wiped nightly (see `PUBLIC_INSTANCE_ARCHITECTURE.md`). Input re-fetch is therefore keyed on the input file via `RemoteStorageDownloadUtils.ensure_input_file_synced()`, independent of this output-sync status, so a wiped input is re-pulled even when the status reads `success`.

---

## Edge Case Handling

### 1. Background Metadata Sync Fails After Migration

**Problem:** Lambda calls the internal sync API but it fails (instance not ready, network error, timeout).

**Solution:** Graceful degradation to on-demand sync:
- Lambda logs the failure but does not retry or block migration
- When the user accesses any experiment endpoint, `ensure_synced_async()` detects missing metadata and downloads it transparently
- User experience: experiment list may take slightly longer on first load

### 2. Concurrent Sync Requests for Same Experiment

**Problem:** Multiple requests trigger sync for the same experiment simultaneously (e.g., user rapidly clicks between experiments).

**Solution:** Sync status file acts as a guard:
- `RemoteSyncStatusFileUtil.check_sync_status_unsynced()` returns `False` if sync is already `processing` or `success`
- Second request skips sync and proceeds with available data
- Worst case: duplicate S3 downloads (idempotent, no data corruption)

### 3. S3 Unavailable During On-Demand Sync

**Problem:** S3 is temporarily unavailable when a user accesses experiment data.

**Solution:** Fail-open with logging:
- `ensure_synced_async()` catches exceptions and returns `False`
- Endpoint continues with whatever local data is available
- Error is logged for monitoring

### 4. Partial Sync (Visualization Succeeds, Full Sync Fails)

**Problem:** Visualization files download successfully but the background full sync for Edit ROI fails.

**Solution:** Status tracking prevents false "synced" state:
- Visualization sync uses `RemoteStorageSimpleReader` which does not write a `success` status
- Only full sync writes `success` to the status file
- Next Edit ROI attempt will retry the full sync

---

## Monitoring and Metrics

### Log Events

Data sync operates within existing ECS services and does not publish custom CloudWatch metrics. Key log events to monitor:

| Log Pattern | Meaning | Severity |
|-------------|---------|----------|
| `Syncing visualization files for {workspace_id}/{unique_id}` | On-demand visualization sync started | Info |
| `Experiment config not found locally, syncing from S3` | On-demand metadata sync started | Info |
| `Failed to sync experiment from S3` | Sync failure (graceful degradation active) | Warning |
| `trigger_experiment_sync` success/failure | Lambda-triggered sync result | Info/Warning |

### Log Groups

| Component | Log Group |
|-----------|-----------|
| Free tier ECS instances | `/ecs/subscr-free-optinist-cloud-taskdef` |
| Premium tier EC2 instances | Application logs on instance |
| Free Manager Lambda | `/aws/lambda/subscr-free-manager` |
| Premium Manager Lambda | `/aws/lambda/subscr-premium-manager` |

---

## Configuration

### Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `INTERNAL_API_SECRET` | Secret for internal sync API auth | None (required for Lambda sync) |
| `ALB_DNS_NAME` | ALB DNS name for internal API calls | None (required for Lambda sync) |
| `REMOTE_STORAGE_TYPE` | Storage backend ("2" for S3) | None (required) |

### Terraform Configuration

**Files:**
- `infrastructure/terraform/premium_manager.tf`
- `infrastructure/terraform/free_manager.tf`
- `infrastructure/terraform/security.tf`

```hcl
-- Key constraint: Lambda needs both values to trigger sync
environment {
  variables = {
    INTERNAL_API_SECRET = random_password.internal_api_secret.result
    ALB_DNS_NAME        = aws_lb.autoscaling.dns_name
  }
}
```

---

## Sync-Enabled Endpoints

| Endpoint | Sync Behavior |
|----------|---------------|
| `POST /system-internal/sync-experiments/{user_id}` | Triggers background metadata sync |
| `POST /system-internal/sync-experiment/{workspace_id}/{unique_id}` | Proactive single-experiment sync (background job) |
| `POST /outputs/sync/{workspace_id}/{unique_id}` | Triggers visualization sync + background full sync |
| `GET /outputs/inittimedata/{dirpath}` | On-demand sync before data access |
| `GET /outputs/timedata/{dirpath}` | On-demand sync before data access |
| `GET /outputs/alltimedata/{dirpath}` | On-demand sync before data access |
| `GET /outputs/data/{filepath}` | On-demand sync before data access |
| `GET /outputs/image/{filepath}` | On-demand sync before data access |
| `POST /outputs/image/{filepath}/status` | On-demand sync for Edit ROI |
| `GET /outputs/structured/{workspace_id}/{unique_id}/{node_id}` | On-demand input-file sync before data access |
| `PATCH /experiments/{workspace_id}/{unique_id}/rename` | `ensure_synced_async()` before rename |
| `DELETE /experiments/{workspace_id}/{unique_id}` | `ensure_synced_async()` before delete |
| `POST /experiments/delete/{workspace_id}` | `ensure_synced_async()` before batch delete |
| `POST /experiments/copy/{workspace_id}` | `ensure_synced_async()` before batch copy |
| `GET /workflow/fetch/{workspace_id}` | Checks sync status only |
| `GET /workflow/reproduce/{workspace_id}/{unique_id}` | `ensure_synced_async()` before reproduce |
| `POST /run/{workspace_id}/result/{uid}` | `ensure_synced_async()` before result access |

---

## Key Functions Reference

| Function | File | Purpose |
|----------|------|---------|
| `sync_user_experiments()` | `studio/app/common/routers/internal.py` | Internal API endpoint for Lambda-triggered sync |
| `trigger_experiment_sync()` | `infrastructure/terraform/premium_manager_package/premium_manager.py` | Fire-and-forget sync call from Premium Manager Lambda |
| `trigger_experiment_sync()` | `infrastructure/terraform/free_manager_package/free_user_utils.py` | Fire-and-forget sync call from Free Manager Lambda |
| `ExptConfigReader.ensure_synced_async()` | `studio/app/common/core/experiment/experiment_reader.py` | On-demand metadata sync before experiment access |
| `sync_visualization_files()` | `studio/app/common/routers/outputs.py` | Visualization sync endpoint with background full sync |
| `_background_full_sync()` | `studio/app/common/routers/outputs.py` | Background task for full PKL/NWB download |
| `ensure_experiment_synced_for_edit()` | `studio/app/optinist/routers/roi.py` | Full sync guard for Edit ROI operations |
| `RemoteSyncStatusFileUtil.check_sync_status_unsynced()` | `studio/app/common/core/storage/remote_storage_controller.py` | Check if experiment needs sync |
| `RemoteSyncStatusFileUtil.check_sync_status_success()` | `studio/app/common/core/storage/remote_storage_controller.py` | Check if experiment is fully synced |
| `RemoteStorageSimpleReader` | `studio/app/common/core/storage/remote_storage_controller.py` | Async context manager for S3 operations without status updates |
| `download_experiment_meta()` | `studio/app/common/core/storage/remote_storage_controller.py` | Download only experiment metadata (YAML files) |
| `download_experiment()` | `studio/app/common/core/storage/remote_storage_controller.py` | Download experiment files with sync_mode filter |
| `verify_internal_secret()` | `studio/app/common/routers/internal.py` | Dependency for internal API authentication |

---

## Testing

### Automated Tests

**File:** `studio/tests/app/common/routers/test_data_sync.py`

```bash
pytest studio/tests/app/common/routers/test_data_sync.py -v
```

### Manual Testing Script

**File:** `infrastructure/scripts/test_data_sync.py`

```bash
# From inside ECS container
python test_data_sync.py test-lazy <email>      # Test lazy sync
python test_data_sync.py test-proactive <email> # Test background metadata sync
python test_data_sync.py status <user_id>       # Check system status
```
