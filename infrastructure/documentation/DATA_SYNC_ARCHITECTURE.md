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
| `essential_only` | `.yaml`, `.yml`, `.json` | Metadata sync for listing |
| `visualization` | `.json`, `.tif`, `.tiff`, `.yaml` | Viewing results |
| `all` | All files including `.pkl`, `.nwb` | Edit ROI, Reproduce |

### File Patterns (from `studio/app/const.py`)

```python
ESSENTIAL_SYNC_PATTERNS = (".yaml", ".yml", ".json")
LARGE_FILE_PATTERNS = tuple(ACCEPT_FILE_EXT.ALL_EXT.value + [".pkl"])
# YAML included for snakemake.yaml (needed to look up input file references)
VISUALIZATION_SYNC_PATTERNS = (".json", ".tif", ".tiff", ".yaml")
```

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

---

---

# Input Data Sync for Multi-Instance Migration

## Problem

When a user is migrated to a new instance, their input data exists in S3 but not locally. When they try to create a workflow, the file selection dialog shows no files because `GET /files/{workspace_id}` only scans the local filesystem.

## Solution Overview

1. **File listing**: Show all files (local + S3) in the file dialog using S3 metadata - no download needed
2. **File selection**: Allow selecting any file including remote-only ones for workflow configuration
3. **Download timing**: Download remote input files only when workflow is submitted/run
4. **Metadata caching**: Sync metadata files to S3 so they're available for remote-only files:
   - `.image_shape.json` - TIFF shape dimensions
   - `.hdf5_structure.json` - HDF5 file structure trees
   - `.mat_structure.json` - MATLAB file structure trees

**UX**: User sees all their files immediately with full metadata (including TIFF shapes and HDF5/MATLAB structures). Download happens at workflow run time.

**Config dialogs**:
- **CSV Settings**: Requires local file - downloads first (with progress indicator)
- **HDF5/MATLAB Structure**: Uses cached structure from S3 - no full file download needed

---

## Architecture Flow

### 1. File Listing (No Errors - Metadata Only)

When a user opens the file selection dialog, the system merges local and S3 file listings without downloading any files:

```
┌─────────────────────────────────────────────────────────────┐
│ 1. User opens file selection dialog                         │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. Frontend calls: GET /files/{workspace_id}/merged         │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. Backend downloads .image_shape.json from S3              │
│    (for TIFF shape data - gracefully handles if missing)    │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. Backend lists LOCAL files from filesystem                │
│    → Returns: [file1.tif, file2.csv] (files on disk)        │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 5. Backend calls S3: list_objects_v2(prefix=workspace_id)   │
│    → Returns: METADATA ONLY (name, size, modified)          │
│    → NO FILE CONTENTS DOWNLOADED                            │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 6. Backend merges lists and determines which files are      │
│    remote-only (exist in S3 but not locally)                │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 7. Frontend displays files:                                 │
│    • Normal files: no indicator (ready to use)              │
│    • Remote-only files: cloud icon (will download when      │
│      workflow runs or config dialog opens)                  │
│    User can select ANY file for workflow configuration      │
└─────────────────────────────────────────────────────────────┘

WHY NO ERROR: S3 list_objects_v2 returns metadata without downloading files.
              Local files are listed from filesystem. No file contents read.
```

---

### 2a. CSV Settings Dialog (Requires File Download)

When a user clicks "Settings" for a remote-only CSV file, the file is downloaded **BEFORE** the dialog opens:

```
┌─────────────────────────────────────────────────────────────┐
│ 1. User clicks "Settings" button for remote CSV file        │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. Frontend checks syncStatus from Redux store              │
│    syncStatus === "remote" ?                                │
└─────────────────────────────────────────────────────────────┘
                    ↓ YES                      ↓ NO
┌───────────────────────────────────┐    ┌────────────────────┐
│ 3a. Show loading progress bar     │    │ 3b. Skip to step 6 │
│     (LinearProgress under button) │    └────────────────────┘
└───────────────────────────────────┘              ↓
                    ↓                              │
┌───────────────────────────────────┐              │
│ 4. Call: POST /files/{id}/sync/{f}│              │
│    → Backend downloads from S3    │              │
│    → Saves file to local disk     │              │
│    → Returns success              │              │
└───────────────────────────────────┘              │
                    ↓                              │
┌───────────────────────────────────┐              │
│ 5. Refresh file tree              │              │
│    syncStatus now = "synced"      │              │
│    Hide loading spinner           │              │
└───────────────────────────────────┘              │
                    ↓                              │
                    └──────────────┬───────────────┘
                                   ↓
┌─────────────────────────────────────────────────────────────┐
│ 6. NOW open the dialog                                      │
│    File is GUARANTEED to exist locally                      │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 7. Backend reads CSV from LOCAL filesystem                  │
│    → Returns data for preview                               │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 8. Dialog displays CSV contents successfully                │
└─────────────────────────────────────────────────────────────┘

WHY NO ERROR: syncInputFileApi() BLOCKS until download completes.
              Dialog only opens AFTER file exists locally.
```

---

### 2b. HDF5/MATLAB Structure Dialog (Uses Cached Structure)

HDF5 and MATLAB files can be very large (GBs). Instead of downloading the entire file just to show its structure, we cache the structure tree when the file is uploaded. This cached structure is fetched from S3:

```
┌─────────────────────────────────────────────────────────────┐
│ 1. User clicks "Structure" button for remote HDF5/MATLAB    │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. Frontend opens dialog immediately (no file sync needed)  │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. Dialog calls: GET /hdf5/{file_path} or /mat/{file_path}  │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. Backend downloads .hdf5_structure.json or                │
│    .mat_structure.json from S3 (small metadata file)        │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 5. Backend checks for cached structure:                     │
│    ┌─────────────────────────────────────────────────────┐  │
│    │ IF file_path in cached_structure:                   │  │
│    │   → Return cached structure (fast!)                 │  │
│    │ ELSE:                                               │  │
│    │   → Fall back to reading from file (if local)       │  │
│    │   → Or return error (file not available)            │  │
│    └─────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 6. Dialog displays file structure successfully              │
│    NO FULL FILE DOWNLOAD REQUIRED!                          │
└─────────────────────────────────────────────────────────────┘

WHY NO ERROR: Structure is cached when file is uploaded.
              Only the small structure JSON is downloaded.
              Full HDF5/MATLAB file NOT downloaded.
```

**Structure Caching Flow (at upload time):**
```
┌─────────────────────────────────────────────────────────────┐
│ User uploads HDF5 or MATLAB file                            │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ Backend extracts structure tree from file                   │
│ • HDF5: Uses h5py.visititems() to traverse datasets         │
│ • MATLAB: Uses pymatreader to read structure                │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ Backend saves structure to cache file:                      │
│ • HDF5: .hdf5_structure.json                                │
│ • MATLAB: .mat_structure.json                               │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ Backend uploads cache file to S3                            │
│ Structure available for remote-only files!                  │
└─────────────────────────────────────────────────────────────┘
```

---

### 3. Workflow Run

When a user runs a workflow with remote-only input files, ALL files are downloaded **BEFORE** Snakemake execution:

```
┌─────────────────────────────────────────────────────────────┐
│ 1. User clicks "Run" workflow button                        │
│    Workflow configured with: [remote_file.tif, local.csv]   │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. Frontend calls: POST /run/{workspace_id}                 │
│    Body contains nodeDict with input file references        │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. Backend creates WorkflowRunner                           │
│    Writes workflow config files                             │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. WorkflowRunner._extract_input_files()                    │
│    Scans nodeDict for all DATA nodes (IMAGE, CSV, HDF5...)  │
│    → Returns: ["remote_file.tif", "local.csv"]              │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 5. await runner.ensure_input_data_local()                   │
│    ┌─────────────────────────────────────────────────────┐  │
│    │ FOR EACH input file:                                │  │
│    │   • Check: os.path.exists(local_path)?              │  │
│    │   • If NO: download_input_data(workspace, filename) │  │
│    │   • If YES: skip (already local)                    │  │
│    └─────────────────────────────────────────────────────┘  │
│    Downloads: remote_file.tif                               │
│    Skips: local.csv (already exists)                        │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 6. ALL INPUT FILES NOW EXIST LOCALLY                        │
│    ✓ remote_file.tif - downloaded from S3                   │
│    ✓ local.csv - already existed                            │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 7. WorkflowRunner.run_workflow()                            │
│    → Starts Snakemake execution in background               │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 8. Snakemake reads input files from LOCAL filesystem        │
│    All files guaranteed to exist → Workflow succeeds        │
└─────────────────────────────────────────────────────────────┘

WHY NO ERROR: ensure_input_data_local() runs BEFORE run_workflow().
              Downloads complete BEFORE Snakemake starts.
              Snakemake only reads from local filesystem.
```

---

### 4. Sample Data Import

When importing sample data, metadata files are generated and uploaded to S3:

```
┌─────────────────────────────────────────────────────────────┐
│ 1. User clicks "Import Sample Data"                         │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. GET /workflow/sample_data/{workspace_id}/{category}      │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. Copy sample files from server to workspace               │
│    • /sample_data/{category}/input/* → workspace/input/     │
│    • /sample_data/{category}/output/* → workspace/output/   │
│    Files now exist LOCALLY                                  │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. Upload input files to S3                                 │
│    FOR EACH file: upload_input_data(workspace, filename)    │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 5. Generate metadata for each file type:                    │
│    • TIFF files → .image_shape.json (shape dimensions)      │
│    • HDF5 files → .hdf5_structure.json (structure tree)     │
│    • MATLAB files → .mat_structure.json (structure tree)    │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 6. Upload metadata files to S3                              │
│    → Metadata available when user accesses from             │
│       different instance                                    │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 7. Return success to frontend                               │
│    Sample data ready for use                                │
└─────────────────────────────────────────────────────────────┘

WHY NO ERROR: Files copied from local sample_data directory.
              Metadata files uploaded so shape/structure data
              available when user later accesses from different instance.
```

---

## Error Prevention Summary

```
┌────────────────────────┬─────────────────────────────┬─────────────────────────────────────┐
│ Operation              │ When Data is Downloaded     │ Why No Error                        │
├────────────────────────┼─────────────────────────────┼─────────────────────────────────────┤
│ File listing           │ Never (metadata only)       │ S3 list_objects_v2 returns metadata │
│                        │                             │ without downloading file contents   │
├────────────────────────┼─────────────────────────────┼─────────────────────────────────────┤
│ TIFF shapes            │ At listing time             │ .image_shape.json downloaded before │
│                        │                             │ file tree is built                  │
├────────────────────────┼─────────────────────────────┼─────────────────────────────────────┤
│ CSV Settings dialog    │ Before dialog opens         │ syncInputFileApi() blocks until     │
│                        │                             │ download completes                  │
├────────────────────────┼─────────────────────────────┼─────────────────────────────────────┤
│ HDF5/MATLAB Structure  │ Never (uses cached          │ Structure cached at upload time in  │
│ dialog                 │ structure metadata)         │ .hdf5_structure.json/.mat_structure │
│                        │                             │ .json - no full file download       │
├────────────────────────┼─────────────────────────────┼─────────────────────────────────────┤
│ Workflow run           │ Before Snakemake starts     │ ensure_input_data_local() downloads │
│                        │                             │ all input files first               │
├────────────────────────┼─────────────────────────────┼─────────────────────────────────────┤
│ Sample data import     │ N/A (files copied locally)  │ Files copied from local sample_data │
│                        │                             │ directory, not from S3              │
└────────────────────────┴─────────────────────────────┴─────────────────────────────────────┘
```

**Key Principles:**
1. **Data is always downloaded BEFORE it is needed** - for operations that require full files
2. **Metadata caching avoids unnecessary downloads** - for structure viewing operations

The system never attempts to read file contents without first ensuring the file exists locally (or using cached metadata).

---

## Metadata Backfill for Legacy Files

Files uploaded before structure caching was implemented won't have metadata files (`.hdf5_structure.json`, `.mat_structure.json`, `.image_shape.json`) in S3. To handle this gracefully, the system automatically generates and uploads metadata when these files are accessed:

### When Metadata is Backfilled

| Trigger | Files | Location |
|---------|-------|----------|
| Workflow run | All input files used in workflow | `workflow_runner._ensure_input_data_local()` |
| CSV settings dialog | CSV file being configured | `sync_input_file()` endpoint |
| On-demand sync | Any file synced via API | `sync_input_file()` endpoint |

### How it Works

```
┌─────────────────────────────────────────────────────────────┐
│ 1. File is downloaded from S3 (on-demand sync or workflow)  │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. Check file type (TIFF/HDF5/MATLAB)                       │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. Generate metadata:                                       │
│    • TIFF → update_image_shape() → .image_shape.json        │
│    • HDF5 → update_hdf5_structure() → .hdf5_structure.json  │
│    • MATLAB → update_mat_structure() → .mat_structure.json  │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. Upload metadata file to S3 (background task)             │
│    → Future access from any instance uses cached metadata   │
└─────────────────────────────────────────────────────────────┘
```

### Implementation

**`studio/app/common/routers/files.py`** - `_update_and_upload_metadata()`:
- Called as background task after `sync_input_file()` endpoint
- Generates structure/shape metadata and uploads to S3

**`studio/app/common/core/workflow/workflow_runner.py`** - `_ensure_input_data_local()`:
- When downloading input files before workflow run
- Tracks which files need metadata update
- Calls `_update_and_upload_metadata()` after downloads complete

### Benefits

1. **Gradual migration**: Legacy files get metadata as they're used
2. **No manual intervention**: Happens automatically during normal usage
3. **Non-blocking**: Metadata upload runs in background
4. **Fault-tolerant**: Failures logged but don't block the operation

---

## Input Data Sync Implementation Details

### Backend Changes

#### Storage Controllers

**`studio/app/common/core/storage/s3_storage_controller.py`**
```python
async def list_input_data_objects(self, workspace_id: str) -> List[Dict]:
    """List all input data objects in S3 for a workspace."""
    prefix = f"app/studio_data/{self.S3_INPUT_DIR}/{workspace_id}/"

    async with self.__get_s3_client() as s3_client:
        s3_list = await s3_client.list_objects_v2(
            Bucket=self.bucket_name, Prefix=prefix
        )

        if not s3_list or s3_list.get("KeyCount", 0) == 0:
            return []

        objects = []
        for obj in s3_list.get("Contents", []):
            key = obj["Key"]
            filename = key.replace(prefix, "")
            if filename and not filename.endswith("/"):
                objects.append({
                    "filename": filename,
                    "size": obj["Size"],
                    "last_modified": obj["LastModified"].isoformat(),
                })
        return objects
```

Also implemented in:
- `remote_storage_controller.py` - Abstract method and wrapper
- `mock_storage_controller.py` - Mock implementation for testing

#### Schemas

**`studio/app/common/schemas/files.py`**
```python
class SyncStatus(str, Enum):
    LOCAL = "local"      # Only on local disk (internal use)
    SYNCED = "synced"    # Exists both locally and in S3 (normal state after upload)
    REMOTE = "remote"    # Only in S3 (shows cloud icon - needs download)

@pydantic_dataclass
class TreeNodeWithSync:
    path: str
    name: str
    isdir: bool
    nodes: List["TreeNodeWithSync"]
    shape: Optional[List] = None
    sync_status: SyncStatus = SyncStatus.SYNCED
    size: Optional[int] = None
```

**Note**: From the user's perspective, only `REMOTE` status matters - it shows a cloud icon indicating the file will be downloaded when needed. `LOCAL` and `SYNCED` files appear the same to the user (no indicator).

#### Router Endpoints

**`studio/app/common/routers/files.py`**

New endpoints:
- `GET /{workspace_id}/merged` - Returns merged local + S3 file list with sync status
- `POST /{workspace_id}/sync/{filename}` - Downloads a specific file from S3

Key behavior:
- Downloads `.image_shape.json` from S3 before listing (for TIFF shape data)
- Uploads metadata files to S3 on file upload:
  - `.image_shape.json` for TIFF files
  - `.hdf5_structure.json` for HDF5 files
  - `.mat_structure.json` for MATLAB files

Structure caching functions:
```python
def update_hdf5_structure(workspace_id: str, relative_file_path: str) -> List[dict]:
    """Extract and cache HDF5 structure to .hdf5_structure.json"""
    structure = HDF5Getter.get(filepath)
    # Save to JSON for remote-only file access

def update_mat_structure(workspace_id: str, relative_file_path: str) -> List[dict]:
    """Extract and cache MATLAB structure to .mat_structure.json"""
    structure = MatGetter.get(file_path, workspace_id)
    # Save to JSON for remote-only file access
```

**`studio/app/optinist/routers/hdf5.py`**

Enhanced to check cached structure first:
```python
@router.get("/hdf5/{file_path:path}")
async def get_files(file_path, workspace_id, remote_bucket_name):
    # 1. Download .hdf5_structure.json from S3 if available
    # 2. Check cached structure - return if found
    # 3. Fall back to extracting from file directly
```

**`studio/app/optinist/routers/mat.py`**

Enhanced to check cached structure first:
```python
@router.get("/mat/{file_path:path}")
async def get_matfiles(file_path, workspace_id, remote_bucket_name):
    # Similar pattern to HDF5
```

**`studio/app/common/routers/run.py`**

Before workflow execution:
```python
# Download any remote-only input files before workflow runs
if RemoteStorageController.is_available():
    await runner.ensure_input_data_local()
```

#### Workflow Runner

**`studio/app/common/core/workflow/workflow_runner.py`**

```python
def _extract_input_files(self) -> List[str]:
    """Extract input file paths from workflow nodes."""
    input_files = []
    data_node_types = {NodeType.IMAGE, NodeType.CSV, NodeType.HDF5, ...}
    for node in self.nodeDict.values():
        if node.type in data_node_types:
            if node.data and node.data.path:
                paths = node.data.path if isinstance(node.data.path, list) else [node.data.path]
                input_files.extend(paths)
    return input_files

async def _ensure_input_data_local(self) -> None:
    """Download any remote-only input files before workflow runs."""
    for filename in self._extract_input_files():
        local_path = join_filepath([DIRPATH.INPUT_DIR, self.workspace_id, filename])
        if not os.path.exists(local_path):
            await remote_storage_controller.download_input_data(self.workspace_id, filename)
```

### Frontend Changes

#### API Functions

**`frontend/src/api/files/Files.ts`**
```typescript
export type SyncStatus = "local" | "synced" | "remote"

export async function getFilesTreeMergedApi(
  workspaceId: number,
  fileType: FILE_TREE_TYPE,
): Promise<TreeNodeWithSyncDTO[]>

export async function syncInputFileApi(
  workspaceId: number,
  fileName: string,
): Promise<{ file_path: string }>
```

#### Redux Store

**`frontend/src/store/slice/FilesTree/FilesTreeType.ts`**
```typescript
export type SyncStatus = "local" | "synced" | "remote"

export interface NodeBase {
  path: string
  name: string
  isDir: boolean
  shape: []
  syncStatus?: SyncStatus  // Only "remote" shows UI indicator (cloud icon)
  size?: number
}
```

**`frontend/src/store/slice/FilesTree/FilesTreeAction.ts`**
- Changed to use `getFilesTreeMergedApi` instead of `getFilesTreeApi`

**`frontend/src/store/slice/FilesTree/FilesTreeSelectors.ts`**
```typescript
export const selectFileSyncStatus =
  (fileType: FILE_TREE_TYPE, filePath: string) =>
  (state: RootState): SyncStatus | undefined
```

#### UI Components

**`frontend/src/components/Workspace/FlowChart/Dialog/FileSelectDialog.tsx`**
- Added cloud icon (`CloudOutlinedIcon`) **only** for files with `syncStatus === "remote"`
- Tooltip: "File in cloud - will download when workflow runs"
- Files with `syncStatus === "local"` or `"synced"` show no indicator (normal appearance)

**`frontend/src/components/Workspace/FlowChart/Dialog/CsvParamSettingDialog.tsx`**
- Added sync-before-open: calls `syncInputFileApi()` before opening dialog
- Shows `LinearProgress` bar under button during sync (consistent with upload progress style)

**`frontend/src/components/Workspace/FlowChart/FlowChartNode/FileSelect.tsx`**
- HDF5/MATLAB Structure button opens dialog directly (no file sync required)
- Structure is fetched from cached metadata via the `/hdf5/` or `/mat/` endpoints
- No `LinearProgress` needed - backend handles fetching cached structure from S3

---

## Input Data Sync Files Modified

| File | Changes |
|------|---------|
| `studio/app/common/core/storage/s3_storage_controller.py` | Added `list_input_data_objects()` |
| `studio/app/common/core/storage/remote_storage_controller.py` | Added abstract method and wrapper |
| `studio/app/common/core/storage/mock_storage_controller.py` | Added mock implementation |
| `studio/app/common/schemas/files.py` | Added `SyncStatus` enum, `TreeNodeWithSync` class |
| `studio/app/common/routers/files.py` | Added `/merged` and `/sync/{filename}` endpoints, structure caching |
| `studio/app/common/routers/run.py` | Added `ensure_input_data_local()` call before workflow |
| `studio/app/common/routers/workflow.py` | Added structure caching during sample data import |
| `studio/app/common/core/workflow/workflow_runner.py` | Added `_extract_input_files()` and `ensure_input_data_local()` |
| `studio/app/optinist/routers/hdf5.py` | Enhanced to use cached structure from `.hdf5_structure.json` |
| `studio/app/optinist/routers/mat.py` | Enhanced to use cached structure from `.mat_structure.json` |
| `frontend/src/api/files/Files.ts` | Added `SyncStatus`, `getFilesTreeMergedApi`, `syncInputFileApi` |
| `frontend/src/store/slice/FilesTree/FilesTreeType.ts` | Added `syncStatus`, `size` to `NodeBase` |
| `frontend/src/store/slice/FilesTree/FilesTreeAction.ts` | Changed to use merged endpoint |
| `frontend/src/store/slice/FilesTree/FilesTreeSelectors.ts` | Added `selectFileSyncStatus` |
| `frontend/src/store/slice/FilesTree/FilesTreeUtils.ts` | Updated to handle sync status conversion |
| `frontend/src/components/Workspace/FlowChart/Dialog/FileSelectDialog.tsx` | Added cloud icon for remote files |
| `frontend/src/components/Workspace/FlowChart/Dialog/CsvParamSettingDialog.tsx` | Added sync-before-open |
| `frontend/src/components/Workspace/FlowChart/FlowChartNode/FileSelect.tsx` | Removed sync requirement for structure dialog |

---

## Input Data Sync Verification Checklist

1. **Unit test S3 listing**: Verify `list_input_data_objects` returns correct format
2. **API test merged endpoint**: Verify `/files/{workspace_id}/merged` returns combined local + S3 files with correct `sync_status`
3. **Integration test - file listing**:
   - Upload file to workspace
   - Clear local input directory (simulate migration)
   - Call merged endpoint → file shows as `remote`
4. **Integration test - workflow run**:
   - Configure workflow with remote-only input file
   - Run workflow → input file is downloaded before execution starts
   - Workflow completes successfully
5. **Integration test - CSV settings dialog**:
   - Select remote-only CSV file
   - Click Settings button → file downloads with spinner
   - Dialog opens and shows CSV preview correctly
6. **Integration test - HDF5/MATLAB structure dialog**:
   - Upload HDF5/MATLAB file → verify `.hdf5_structure.json`/`.mat_structure.json` created
   - Clear local input directory (simulate migration)
   - Click Structure button → dialog opens immediately (no file download)
   - Structure tree displays correctly from cached metadata
7. **Manual E2E test**:
   - Upload files (TIFF, CSV, HDF5, MATLAB), clear local storage
   - Open file dialog → see files with cloud icons
   - Select remote HDF5 file, click Structure → shows structure (no download)
   - Select remote CSV file, click Settings → downloads then shows preview
   - Configure and run workflow → remaining files download, workflow runs

---

## Tests

### Automated Tests

**File:** `studio/tests/app/common/routers/test_data_sync.py`

- Test Case 8: Input Data Sync (11 tests covering merged endpoint, sync status, structure caching, etc.)

```bash
pytest studio/tests/app/common/routers/test_data_sync.py -v
```

### Manual Testing Script

**File:** `infrastructure/scripts/test_data_sync.py`

```bash
# From inside ECS container
python test_data_sync.py test-lazy <email>           # Test lazy sync
python test_data_sync.py test-proactive <email>      # Test background metadata sync
python test_data_sync.py test-input-data <email>     # Test input data sync
python test_data_sync.py status <user_id>            # Check system status
```

---

## References

- Branch: `feature/proactive-sync`, `feature/aws-autoscaling`
- Parent branch: `develop-main`
- Related: `ALB_SECURITY_ROUTING_SUMMARY.md`, `PREMIUM_MANAGER_SUMMARY.md`
