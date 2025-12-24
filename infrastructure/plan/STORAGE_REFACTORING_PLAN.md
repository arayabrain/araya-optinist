# Storage Operations Refactoring Plan

## Overview

Extract storage-related operations from `cloud_utils.py` into dedicated modules within the `storage/` directory. This refactoring will:
- Break the circular dependency between cloud_utils and s3_storage_monitor
- Improve code organization and maintainability
- Group related storage functionality together
- Follow existing codebase patterns (class-based services with static methods)

**Approach:** Breaking change - all imports will be updated in one PR.

## New Module Structure

```
studio/app/common/core/storage/
├── usage/                               # NEW directory
│   ├── __init__.py                      # Public API exports
│   ├── repository.py                    # DB operations layer
│   ├── calculator.py                    # Live storage calculation
│   ├── service.py                       # Business logic orchestration
│   └── reconciliation.py                # Scan trigger logic
├── s3_storage_monitor.py                # MOVED from cloud/
├── s3_storage_controller.py             # EXISTING (imports updated)
└── ... (other existing files)
```

## Module Responsibilities

### 1. `storage/usage/repository.py` (NEW)
**Purpose:** Database operations for UserStorageUsage table

**Class:** `StorageUsageRepository` with static methods:
- `get_storage_usage(user_id)` - Read storage usage from DB
- `update_storage_usage(user_id, new_usage_bytes)` - Update storage usage
- `increment_storage(user_id, bytes_added)` - Increment by delta
- `decrement_storage(user_id, bytes_removed)` - Decrement by delta
- `get_fallback_quota(user_id)` - Calculate default quota (private helper)

**Migrated from cloud_utils.py:**
- `get_user_storage_usage` → `StorageUsageRepository.get_storage_usage`
- `update_user_storage_usage` → `StorageUsageRepository.update_storage_usage`
- `increment_user_storage` → `StorageUsageRepository.increment_storage`
- `decrement_user_storage` → `StorageUsageRepository.decrement_storage`
- `_get_fallback_storage_quota` → `StorageUsageRepository.get_fallback_quota`

### 2. `storage/usage/calculator.py` (NEW)
**Purpose:** Calculate live storage usage from S3 or local filesystem

**Class:** `StorageUsageCalculator` with static methods:
- `calculate_live_usage(user_id)` - Calculate from S3 or local
- `calculate_local_storage(user_id)` - Local filesystem calculation
- `is_storage_data_fresh(storage_info, max_age_minutes)` - Check cache freshness

**Migrated from cloud_utils.py:**
- `_calculate_live_storage_usage` → `StorageUsageCalculator.calculate_live_usage`
- `_calculate_local_user_storage` → `StorageUsageCalculator.calculate_local_storage`
- `_is_storage_data_fresh` → `StorageUsageCalculator.is_storage_data_fresh`

### 3. `storage/usage/service.py` (NEW)
**Purpose:** High-level orchestration and caching logic

**Class:** `StorageUsageService` with static methods:
- `get_current_usage(user_id, force_live=False)` - Get cached or live usage
- `update_after_workflow(workspace_id)` - Post-workflow reconciliation

**Migrated from cloud_utils.py:**
- `get_current_user_storage_usage` → `StorageUsageService.get_current_usage`
- `update_user_storage_after_workflow` → `StorageUsageService.update_after_workflow`

### 4. `storage/usage/reconciliation.py` (NEW)
**Purpose:** Full S3 scan triggering and execution

**Class:** `StorageReconciliation` with static methods:
- `should_trigger_full_scan(user_id)` - Check if scan needed
- `perform_full_scan_and_reset(user_id)` - Execute full scan with lock

**Migrated from cloud_utils.py:**
- `_should_trigger_full_scan` → `StorageReconciliation.should_trigger_full_scan`
- `_perform_full_scan_and_reset_delta` → `StorageReconciliation.perform_full_scan_and_reset`

### 5. `storage/s3_storage_monitor.py` (MOVED)
**Current location:** `cloud/s3_storage_monitor.py`
**New location:** `storage/s3_storage_monitor.py`

**Changes:**
- Move file to storage/ directory
- Update imports to use `StorageUsageRepository` instead of cloud_utils functions
- Update function calls in methods

## Functions Remaining in cloud_utils.py

**Keep these functions (not storage-specific):**
- `get_user_context_with_warnings` - User context with subscription warnings
- `calculate_limit_warning` - Subscription/storage limit warnings
- `get_user_subscription_plan` - Subscription tier information
- `CloudDebug` class - Debug utilities

**Update `calculate_limit_warning` to use:**
- `StorageUsageRepository.get_storage_usage` (instead of `get_user_storage_usage`)
- `StorageUsageService.get_current_usage` (instead of `get_current_user_storage_usage`)

## Implementation Steps

### Step 1: Create Repository Layer
**File:** `studio/app/common/core/storage/usage/repository.py`

1. Create new file with `StorageUsageRepository` class
2. Copy and adapt functions from cloud_utils.py:
   - `get_user_storage_usage` (lines 144-208)
   - `update_user_storage_usage` (lines 210-281)
   - `increment_user_storage` (lines 283-356)
   - `decrement_user_storage` (lines 358-430)
   - `_get_fallback_storage_quota` (lines 29-95)
3. Convert to static methods with same logic
4. Keep all imports (sqlmodel, database, constants)

### Step 2: Create Calculator Layer
**File:** `studio/app/common/core/storage/usage/calculator.py`

1. Create new file with `StorageUsageCalculator` class
2. Copy and adapt functions from cloud_utils.py:
   - `_calculate_live_storage_usage` (lines 503-557)
   - `_calculate_local_user_storage` (lines 559-617)
   - `_is_storage_data_fresh` (lines 473-501)
3. Convert to static methods
4. Update imports to use `StorageUsageRepository`
5. Update internal calls (e.g., `S3StorageMonitor` import from storage/)

### Step 3: Create Reconciliation Layer
**File:** `studio/app/common/core/storage/usage/reconciliation.py`

1. Create new file with `StorageReconciliation` class
2. Copy and adapt functions from cloud_utils.py:
   - `_should_trigger_full_scan` (lines 1019-1093)
   - `_perform_full_scan_and_reset_delta` (lines 1095-1172)
3. Convert to static methods
4. Update imports to use `StorageUsageRepository` and `StorageUsageCalculator`

### Step 4: Create Service Layer
**File:** `studio/app/common/core/storage/usage/service.py`

1. Create new file with `StorageUsageService` class
2. Copy and adapt functions from cloud_utils.py:
   - `get_current_user_storage_usage` (lines 432-471)
   - `update_user_storage_after_workflow` (lines 1174-1230)
3. Convert to static methods
4. Update imports to use Repository, Calculator, and Reconciliation layers

### Step 5: Create Public API
**File:** `studio/app/common/core/storage/usage/__init__.py`

1. Create new file
2. Export all classes:
   - `StorageUsageRepository`
   - `StorageUsageCalculator`
   - `StorageUsageService`
   - `StorageReconciliation`

### Step 6: Move S3 Storage Monitor
**From:** `studio/app/common/core/cloud/s3_storage_monitor.py`
**To:** `studio/app/common/core/storage/s3_storage_monitor.py`

1. Move file using `git mv`
2. Update imports (lines 13-16):
   - FROM: `from studio.app.common.core.cloud.cloud_utils import ...`
   - TO: `from studio.app.common.core.storage.usage.repository import StorageUsageRepository`
3. Update function calls:
   - Line 336: `update_user_storage_usage(...)` → `StorageUsageRepository.update_storage_usage(...)`
   - Line 341: `get_user_storage_usage(...)` → `StorageUsageRepository.get_storage_usage(...)`
   - Line 531: `get_user_storage_usage(...)` → `StorageUsageRepository.get_storage_usage(...)`

### Step 7: Update S3 Storage Controller
**File:** `studio/app/common/core/storage/s3_storage_controller.py`

Update dynamic imports (lines 727 and 793):
- FROM: `from studio.app.common.core.cloud.cloud_utils import increment_user_storage`
- TO: `from studio.app.common.core.storage.usage.repository import StorageUsageRepository`
- Update calls:
  - Line 742: `increment_user_storage(...)` → `StorageUsageRepository.increment_storage(...)`
  - Line 808: `decrement_user_storage(...)` → `StorageUsageRepository.decrement_storage(...)`

### Step 8: Update cloud_utils.py
**File:** `studio/app/common/core/cloud/cloud_utils.py`

1. Remove migrated functions (12 functions, ~600 lines)
2. Add imports for new storage modules:
   ```python
   from studio.app.common.core.storage.usage.repository import StorageUsageRepository
   from studio.app.common.core.storage.usage.service import StorageUsageService
   ```
3. Update `calculate_limit_warning` function (lines 619-864):
   - Line 641: `get_user_storage_usage(user_id)` → `StorageUsageRepository.get_storage_usage(user_id)`
   - Line 652: `get_current_user_storage_usage(...)` → `StorageUsageService.get_current_usage(...)`
   - Line 661: `get_user_storage_usage(...)` → `StorageUsageRepository.get_storage_usage(...)`

### Step 9: Update All Importing Files

**9.1 Background Jobs**
- `studio/app/common/core/background/storage_reconciliation_job.py`
  - Lines 18-22: Update imports
  - Line 114: `_perform_full_scan_and_reset_delta` → `StorageReconciliation.perform_full_scan_and_reset`
  - Line 217, 228, 231: Update function calls

**9.2 Routers**
- `studio/app/common/routers/storage_limit_alerts.py`
  - Lines 48-51: Update imports
  - Update all function calls throughout file

- `studio/app/common/routers/run.py`
  - Lines 9-12: Update imports
  - Lines 58-74: Update function calls

- `studio/app/common/routers/files.py`
  - Line 20-22: Update imports

- `studio/app/common/routers/users_me.py`
  - Line 8: Update imports
  - Line 389: Update function call

- `studio/app/common/routers/auth.py`
  - Line 6: Update imports (CloudDebug stays in cloud_utils)
  - Line 43: `calculate_limit_warning` stays in cloud_utils

- `studio/app/common/routers/workspace.py`
  - Review line 530 (may not actually import, verify)

**9.3 Snakemake**
- `studio/app/common/core/snakemake/snakemake_executor.py`
  - Line 18: Update import
  - Line 75: `update_user_storage_after_workflow` → `StorageUsageService.update_after_workflow`

**9.4 Cloud Module**
- `studio/app/common/core/cloud/__init__.py`
  - Remove s3_storage_monitor import (moved to storage/)

### Step 10: Update Tests

**10.1 Storage Tests**
- `tests/app/common/core/cloud/test_s3_storage_monitor.py`
  - Update import from `cloud.s3_storage_monitor` to `storage.s3_storage_monitor`

**10.2 Subscription Tests**
- `tests/app/common/core/subscription/test_storage_tracking.py`
  - Update imports to use new storage.usage modules
  - Update function calls to class methods

- `tests/app/common/core/subscription/test_storage_integration.py`
  - Update imports to use new storage.usage modules

**10.3 Router Tests**
- `tests/app/common/routers/test_storage_limit_alerts.py`
  - Update imports to use new storage.usage modules

### Step 11: Update storage/__init__.py
**File:** `studio/app/common/core/storage/__init__.py`

Add exports for new storage usage module:
```python
from .usage import (
    StorageUsageRepository,
    StorageUsageCalculator,
    StorageUsageService,
    StorageReconciliation,
)
from .s3_storage_monitor import S3StorageMonitor

__all__ = [
    # ... existing exports
    "StorageUsageRepository",
    "StorageUsageCalculator",
    "StorageUsageService",
    "StorageReconciliation",
    "S3StorageMonitor",
]
```

## Critical Files

**Files to Create (5):**
1. `studio/app/common/core/storage/usage/__init__.py`
2. `studio/app/common/core/storage/usage/repository.py`
3. `studio/app/common/core/storage/usage/calculator.py`
4. `studio/app/common/core/storage/usage/service.py`
5. `studio/app/common/core/storage/usage/reconciliation.py`

**Files to Move (1):**
1. `studio/app/common/core/cloud/s3_storage_monitor.py` → `studio/app/common/core/storage/s3_storage_monitor.py`

**Files to Modify (14):**
1. `studio/app/common/core/cloud/cloud_utils.py` - Remove functions, update calculate_limit_warning
2. `studio/app/common/core/storage/s3_storage_controller.py` - Update imports
3. `studio/app/common/core/storage/__init__.py` - Add exports
4. `studio/app/common/core/background/storage_reconciliation_job.py` - Update imports
5. `studio/app/common/routers/storage_limit_alerts.py` - Update imports
6. `studio/app/common/routers/run.py` - Update imports
7. `studio/app/common/routers/files.py` - Update imports
8. `studio/app/common/routers/users_me.py` - Update imports
9. `studio/app/common/core/snakemake/snakemake_executor.py` - Update imports
10. `tests/app/common/core/cloud/test_s3_storage_monitor.py` - Update imports
11. `tests/app/common/core/subscription/test_storage_tracking.py` - Update imports
12. `tests/app/common/core/subscription/test_storage_integration.py` - Update imports
13. `tests/app/common/routers/test_storage_limit_alerts.py` - Update imports
14. `studio/app/common/core/cloud/__init__.py` - Remove s3_storage_monitor export

## Dependency Layer Architecture

```
┌─────────────────────────────────────────┐
│  Presentation Layer (Routers/APIs)     │
│  - run.py, storage_limit_alerts.py     │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│  Service Layer                          │
│  - StorageUsageService                  │
│  - calculate_limit_warning (cloud_utils)│
└─────────────────┬───────────────────────┘
                  │
        ┌─────────┴─────────┐
        │                   │
┌───────▼──────┐   ┌───────▼──────────────┐
│ Calculator   │   │ Reconciliation       │
│ Layer        │   │ Layer                │
└───────┬──────┘   └───────┬──────────────┘
        │                   │
        └─────────┬─────────┘
                  │
┌─────────────────▼───────────────────────┐
│  Repository Layer (DB Access)           │
│  - StorageUsageRepository               │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│  External Systems                       │
│  - S3 (S3StorageMonitor)                │
│  - Database (SQLModel)                  │
└─────────────────────────────────────────┘
```

**Circular Dependency Resolution:**
- OLD: cloud_utils → s3_storage_monitor → s3_storage_controller → cloud_utils (CIRCULAR)
- NEW: Repository (base) ← Calculator ← S3Monitor, S3Controller (NO CIRCULAR DEPENDENCY)

## Validation

After implementation:
1. Run full test suite: `pytest studio/tests/`
2. Verify no import errors: `python -m studio.app.common.core.storage.usage`
3. Check all routers start correctly
4. Verify background jobs can import new modules
5. Test storage increment/decrement operations
6. Test full S3 scan reconciliation

## Rollback Plan

If issues arise:
1. Revert all changes (single PR, easy rollback)
2. All tests should catch breaking changes before merge
3. No backward compatibility concerns (breaking change approach)
