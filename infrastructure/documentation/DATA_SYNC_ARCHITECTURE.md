# Multi-Instance Data Sync: On-Demand Experiment Synchronization

## Executive Summary

This document describes the data synchronization system that ensures experiment data is available when users are migrated between instances in a multi-instance deployment (free tier shared instances, premium dedicated instances).

**Problem Solved:**
- When users are migrated between instances (e.g., scaling, subscription changes), their experiment data exists only in S3, not on the new local instance
- Without sync, users see missing experiments or errors when trying to access their data

**Solution:**
- **On-demand sync:** API endpoints download data transparently when accessed
- **Tiered sync:** Downloads only what's needed (metadata, visualization files, or full data)
- **Background sync:** Lambda triggers metadata sync after migration for faster initial listing

**Key Benefits:**
- Seamless user experience across instance migrations
- Minimal latency for common operations (viewing results)
- Efficient bandwidth usage (only sync what's needed)
- No data loss during scaling events

---

## Problem: Cross-Instance Data Availability

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

1. **Free tier rebalancing:** User moved between shared instances for load balancing
2. **Premium upgrade:** User upgraded and migrated to dedicated instance
3. **Premium downgrade:** User downgraded and migrated back to shared pool
4. **Instance replacement:** Old instance terminated, user moved to new one

### The Problem

After migration, the user's new instance has:
- **Database:** User record points to new instance
- **Local filesystem:** Empty (no experiment data)
- **S3:** All experiment data (source of truth)

Without sync, the user experiences:
- Empty experiment lists
- "File not found" errors when viewing results
- Failed reproduce/edit operations

---

## Solution: Three-Tier Synchronization

### Tier 1: On-Demand Visualization Sync (Primary)

**When:** User views experiment results (visualize tab)
**What:** JSON timeseries data, TIFF images
**Why:** Fast loading for viewing results, skips large PKL/NWB files

```
┌──────────────────────────────────────────────────────────────────────┐
│ 1. User clicks "Visualize" for an experiment                         │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 2. Frontend calls: POST /outputs/sync/{workspace_id}/{unique_id}     │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 3. Backend downloads visualization files from S3                     │
│    sync_mode="visualization"                                         │
│    - .json files (timeseries data, plot data)                        │
│    - .tif/.tiff files (images)                                       │
│    - Input data files (referenced images)                            │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 4. Background task starts full sync for Edit ROI preparation         │
│    sync_mode="all" (downloads PKL, NWB in background)                │
└──────────────────────────────────────────────────────────────────────┘
```

### Tier 2: Full Sync (Edit ROI / Reproduce)

**When:** User clicks Edit ROI or Reproduce
**What:** All experiment files including PKL, NWB
**Why:** Required for data manipulation operations

```
┌──────────────────────────────────────────────────────────────────────┐
│ 1. User clicks "Edit ROI" or "Reproduce"                             │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 2. Endpoint checks sync status                                       │
│    RemoteSyncStatusFileUtil.check_sync_status_unsynced()             │
└──────────────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┴─────────────────────┐
        │                                           │
        ▼                                           ▼
┌───────────────────────┐               ┌───────────────────────┐
│ Already synced        │               │ Needs sync            │
│ Proceed immediately   │               │ Download all files    │
└───────────────────────┘               │ from S3               │
                                        └───────────────────────┘
```

### Tier 3: Background Metadata Sync (Lambda-Triggered)

**When:** Immediately after user migration (optimization)
**What:** All experiment YAML files (experiment.yaml, workflow.yaml, snakemake_config.yaml)
**Why:** Pre-populates experiment list so it's available when user opens the app

```
┌──────────────────────────────────────────────────────────────────────┐
│ 1. Lambda migrates user to new instance                              │
│    - Updates database: user.current_instance_id = new_instance       │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 2. Lambda calls internal API to trigger sync                         │
│    POST /system-internal/sync-experiments/{user_id}                  │
│    Headers: X-Internal-Secret: <secret>                              │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 3. New instance downloads all experiment metadata from S3            │
│    - Runs as background task (non-blocking)                          │
│    - Downloads: experiment.yaml, workflow.yaml for all experiments   │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 4. User's experiment list is immediately available                   │
│    - Names, dates, status visible                                    │
│    - Full data synced on-demand (Tier 1/2)                           │
└──────────────────────────────────────────────────────────────────────┘
```

**Note:** This tier is an optimization. If it fails, Tier 1 and 2 still work - endpoints
will sync metadata on-demand when accessed via `ensure_synced_async()`.

---

## File Sync Modes

| Mode | Files Downloaded | Use Case |
|------|------------------|----------|
| `thumbnails_only` | `*_thumb.png` | Fast Dataview thumbnails |
| `essential_only` | `.yaml`, `.yml`, `.json` | Metadata sync for listing |
| `visualization` | `.json`, `.tif`, `.tiff`, `.yaml` | Viewing results |
| `all` | All files including `.pkl`, `.nwb` | Edit ROI, Reproduce |

### File Patterns (from `studio/app/const.py`)

```python
THUMBNAIL_FILE_PATTERNS = ("input_thumb.png", "roi_thumb.png", "_thumb.png")
ESSENTIAL_SYNC_PATTERNS = (".yaml", ".yml", ".json")
LARGE_FILE_PATTERNS = tuple(ACCEPT_FILE_EXT.ALL_EXT.value + [".pkl"])
VISUALIZATION_SYNC_PATTERNS = (".json", ".tif", ".tiff", ".yaml")
```

### Thumbnail Storage Structure

PNG thumbnails are stored alongside experiment data:

```
/output/{workspace_id}/{unique_id}/
├── thumbnails/
│   ├── input_thumb.png    # First frame of input TIFF (~50-100KB)
│   └── roi_thumb.png      # Rendered ROI overlay (~50-100KB)
├── experiment.yaml
├── workflow.yaml
└── ... (other output files)
```

**Why PNGs?**
- Original TIFFs can be 100MB+, PNGs are ~50-100KB
- Background sync can download 50+ thumbnails per run vs 10 TIFFs
- Immediate Dataview visibility after restart

---

## Implementation Details

### Internal API Endpoint

**File:** `studio/app/common/routers/internal.py`

```python
@router.post("/sync-experiments/{user_id}")
async def sync_user_experiments(
    user_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: None = Depends(verify_internal_secret),
):
    """
    Trigger experiment metadata sync for a user.
    Called by Lambda functions after user migrations.
    """
```

**Security:**
- Protected by `INTERNAL_API_SECRET` environment variable
- Rate-limited (10 seconds between requests per user)
- Constant-time secret comparison (prevents timing attacks)

### Lambda Integration

**Files:**
- `infrastructure/terraform/premium_manager_package/premium_manager.py`
- `infrastructure/terraform/free_manager_package/free_user_utils.py`

```python
def trigger_experiment_sync(user_id: int) -> bool:
    """Trigger experiment metadata sync for user on their new instance."""
    url = f"https://{alb_dns}/system-internal/sync-experiments/{user_id}"
    headers = {"X-Internal-Secret": internal_secret}
    response = requests.post(url, headers=headers, timeout=10.0)
```

**Behavior:**
- Fire-and-forget (doesn't block migration)
- Logs success/failure
- Graceful degradation if sync fails

### On-Demand Sync in Routers

**Pattern:** Before accessing experiment data, ensure it's synced

```python
# In experiment.py, run.py, workflow.py, roi.py
await ExptConfigReader.ensure_synced_async(
    workspace_id, unique_id, remote_bucket_name
)
```

**File:** `studio/app/common/core/experiment/experiment_reader.py`

```python
@classmethod
async def ensure_synced_async(cls, workspace_id, unique_id, remote_bucket_name):
    """Ensure experiment metadata exists locally, syncing from S3 if needed."""
    config_path = cls.get_config_yaml_path(workspace_id, unique_id)

    if os.path.exists(config_path):
        return True  # Already synced

    # Download from S3
    async with RemoteStorageSimpleReader(remote_bucket_name) as controller:
        await controller.download_experiment_meta(workspace_id, unique_id)
```

### Visualization Sync Endpoint

**File:** `studio/app/common/routers/outputs.py`

```python
@router.post("/sync/{workspace_id}/{unique_id}")
async def sync_visualization_files(
    workspace_id: str,
    unique_id: str,
    background_tasks: BackgroundTasks,
    remote_bucket_name: str = Depends(get_user_remote_bucket_name),
):
    """Lazy-load visualization files from S3."""
    # Sync visualization files (JSON, TIFF)
    await remote_storage_controller.download_experiment(
        workspace_id, unique_id, sync_mode="visualization"
    )

    # Trigger background full sync for Edit ROI
    background_tasks.add_task(
        _background_full_sync, remote_bucket_name, workspace_id, unique_id
    )
```

---

## Sync Status Tracking

### Status File

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

### Status Values

| Status | Meaning |
|--------|---------|
| `processing` | Sync in progress |
| `success` | Full sync completed |
| `error` | Sync failed |
| (no file) | Never synced / needs sync |

### Checking Sync Status

```python
# Check if needs sync
RemoteSyncStatusFileUtil.check_sync_status_unsynced(workspace_id, unique_id)

# Check if fully synced
RemoteSyncStatusFileUtil.check_sync_status_success(workspace_id, unique_id)
```

---

## Request Flow Examples

### Example 1: User Migrated, Views Experiment List

```
1. Lambda migrates user to instance #2
2. Lambda calls POST /system-internal/sync-experiments/123
3. Instance #2 downloads all experiment.yaml files in background
4. User opens app, sees full experiment list
5. No additional sync needed for listing
```

### Example 2: User Views Experiment Results

```
1. User clicks on experiment to view results
2. Frontend calls POST /outputs/sync/{workspace_id}/{unique_id}
3. Backend checks: sync status = unsynced
4. Backend downloads JSON + TIFF files (sync_mode="visualization")
5. Background task starts downloading PKL/NWB files
6. User sees results immediately
7. Edit ROI files ready when user needs them
```

### Example 3: User Clicks Edit ROI

```
1. User clicks "Edit ROI" button
2. POST /outputs/image/{filepath}/status called
3. ensure_experiment_synced_for_edit() checks sync status
4. If unsynced: downloads all files (sync_mode="all")
5. Edit ROI operation proceeds with all data available
```

---

## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `INTERNAL_API_SECRET` | Secret for internal API auth | Yes (for Lambda sync) |
| `ALB_DNS_NAME` | ALB DNS for internal calls | Yes (for Lambda sync) |
| `REMOTE_STORAGE_TYPE` | "2" for S3 | Yes |

### Terraform Configuration

**Files:**
- `infrastructure/terraform/premium_manager.tf`
- `infrastructure/terraform/free_manager.tf`
- `infrastructure/terraform/security.tf`

```hcl
# Lambda environment variables
environment {
  variables = {
    INTERNAL_API_SECRET = var.internal_api_secret
    ALB_DNS_NAME        = aws_lb.main.dns_name
  }
}
```

---

## Endpoints Modified

| Endpoint | Change |
|----------|--------|
| `POST /system-internal/sync-experiments/{user_id}` | New - triggers metadata sync |
| `POST /outputs/sync/{workspace_id}/{unique_id}` | New - triggers visualization sync |
| `GET /outputs/inittimedata/{dirpath}` | Added on-demand sync |
| `GET /outputs/timedata/{dirpath}` | Added on-demand sync |
| `GET /outputs/alltimedata/{dirpath}` | Added on-demand sync |
| `GET /outputs/data/{filepath}` | Added on-demand sync |
| `GET /outputs/image/{filepath}` | Added on-demand sync |
| `POST /outputs/image/{filepath}/status` | Added on-demand sync for Edit ROI |
| `PUT /experiments/{workspace_id}/{unique_id}/rename` | Added ensure_synced_async |
| `DELETE /experiments/{workspace_id}/{unique_id}` | Added ensure_synced_async |
| `POST /experiments/{workspace_id}/delete-list` | Added ensure_synced_async |
| `POST /experiments/{workspace_id}/copy-list` | Added ensure_synced_async |
| `GET /workflows/{workspace_id}` | Changed to check sync status only |
| `GET /workflows/{workspace_id}/{unique_id}` | Added ensure_synced_async |
| `POST /run/{workspace_id}/result/{uid}` | Added ensure_synced_async |

---

## Files Modified Summary

### New Files
1. `studio/app/common/routers/internal.py` - Internal API for Lambda sync
2. `studio/tests/app/common/routers/test_data_sync.py` - Test utilities
3. `infrastructure/scripts/test_data_sync.py` - Manual testing script

### Backend
1. `studio/app/common/core/experiment/experiment_reader.py` - Added ensure_synced_async
2. `studio/app/common/core/storage/remote_storage_controller.py` - Added sync_mode parameter
3. `studio/app/common/core/storage/s3_storage_controller.py` - Added download_experiment_meta, sync modes
4. `studio/app/common/core/storage/file_filter.py` - Added visualization sync mode
5. `studio/app/common/routers/outputs.py` - Added sync endpoints and on-demand sync
6. `studio/app/common/routers/experiment.py` - Added ensure_synced_async calls
7. `studio/app/common/routers/run.py` - Added ensure_synced_async calls
8. `studio/app/common/routers/workflow.py` - Changed to lazy sync approach
9. `studio/app/optinist/routers/roi.py` - Added ensure_experiment_synced_for_edit
10. `studio/app/const.py` - Added VISUALIZATION_SYNC_PATTERNS

### Lambda
11. `infrastructure/terraform/premium_manager_package/premium_manager.py` - Added trigger_experiment_sync
12. `infrastructure/terraform/free_manager_package/free_user_utils.py` - Added trigger_experiment_sync

### Terraform
13. `infrastructure/terraform/premium_manager.tf` - Added environment variables
14. `infrastructure/terraform/free_manager.tf` - Added environment variables
15. `infrastructure/terraform/security.tf` - Added security group rules for internal API

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

---

## Success Criteria

- User migration completes without visible experiment loss
- Experiment list loads within 2 seconds after migration
- Visualization data loads on-demand without errors
- Edit ROI operations work after full sync
- No duplicate downloads (proper sync status tracking)
- Graceful degradation if S3 unavailable
- Internal API protected from external access

---

## References

- Branch: `feature/proactive-sync`
- Parent branch: `feature/aws-autoscaling`
- Related: `ALB_SECURITY_ROUTING_SUMMARY.md`, `PREMIUM_MANAGER_SUMMARY.md`
