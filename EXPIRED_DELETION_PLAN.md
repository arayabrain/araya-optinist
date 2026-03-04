# Expired Subscription Data Deletion — Implementation Plan

**Policy reference:** `docs/other/plan_expiration.md`

## Executive Summary

### Problem

When a premium user's subscription expires, their storage usage may exceed the 5 GB free-tier limit. There is currently no mechanism to automatically reclaim that storage. Without intervention, expired users would retain up to 200 GB indefinitely at no cost, creating unbounded storage liability.

### Goal

Implement a fully automated post-expiration lifecycle that:
1. Gives users **30 days** to download or manage their data before any deletion occurs
2. Notifies users at key milestones (Day 1, Day 20, post-deletion)
3. Automatically reduces storage to the free-tier limit on Day 30 using a configurable, predictable deletion order
4. Preserves workflow YAML files unconditionally so users can re-run analyses if they re-subscribe

### Current State

- **Grace period constants exist** (`GRACE_PERIOD_DAYS`, `WARNING_PERIOD_DAYS` in `constants.py`) but are only used for frontend warning banners — no backend enforcement.
- **Workspace deletion methods exist** (`delete_workspace_files()`, `delete_workspace_contents()`) but operate at whole-workspace granularity. The deletion policy requires per-data-type granularity (intermediates, inputs, outputs).
- **Background job infrastructure exists** (APScheduler in `scheduler.py`, registered in `__main_unit__.py`) with an existing cleanup job for free users. No equivalent job exists for expired premium users.
- **Email service exists** but is Firebase-based (auth flows only). Custom transactional emails require a new provider (AWS SES).
- **Storage tracking exists** (`UserStorageUsage`, `StorageReconciliationJob`) but does not break down usage by data type within a workspace.

### Design Principles

- **Safety first.** Deletion is irreversible. The executor supports dry-run mode, per-item error handling, and re-subscription checks at execution time. Partial failures do not abort the job.
- **User agency.** Users choose their deletion priority (preserve outputs vs. preserve inputs) and have 30 days plus email notifications to act before anything is removed.
- **Predictable ordering.** Deletion follows a strict tiered order: intermediates before primary data, unpublished before published, oldest first. No randomness.
- **Minimal new infrastructure.** Builds on the existing scheduler, workspace services, S3 storage layer, and subscription models. The only new external dependency is AWS SES (already available via `boto3`).
- **Auditability.** Every deletion is recorded in `ExpirationDeletionRecord` with before/after storage, priority used, and per-item details.

---

## Existing Infrastructure

| Component | Location | Notes |
|-----------|----------|-------|
| Background scheduler | `studio/app/common/core/background/scheduler.py` | APScheduler, jobs registered in `__main_unit__.py` |
| Data cleanup job | `studio/app/common/core/background/cleanup_job.py` | Cleans up logged-out free user data |
| Workspace deletion | `studio/app/common/core/workspace/workspace_services.py` | `delete_workspace_files()`, `delete_workspace_contents()` |
| Subscription models | `studio/app/common/models/subscription.py` | `UserSubscription`, `UserStorageUsage`, `StorageOperation` |
| Subscription constants | `studio/app/common/core/subscription/constants.py` | `GRACE_PERIOD_DAYS=30`, `WARNING_PERIOD_DAYS=30`, quotas |
| Published status | `studio/app/common/models/experiment.py` | `ExperimentRecord.publish_status` (0=private, 1=public) |
| Email service | `studio/app/common/core/auth/auth_email_service.py` | Firebase-based email sending |
| Limit warnings | `studio/app/common/core/cloud/cloud_utils.py` | `calculate_limit_warning()`, lifecycle state detection |
| Storage controller | `studio/app/common/core/storage/remote_storage_controller.py` | S3/local/mock abstraction |
| Background tasks | `studio/app/common/models/experiment.py` | `BackgroundTask` model with status tracking |

## Implementation Tasks

### 1. Add Deletion Priority User Setting

**Files to modify:**
- `studio/app/common/models/subscription.py` — Add `DeletionPriority` enum and field
- `studio/app/common/core/subscription/subscription_service.py` — Getter/setter for preference
- `studio/app/common/routers/users.py` (or subscription router) — API endpoint

**Details:**

Add a `deletion_priority` column to `UserSubscription` (or `User.attributes` JSON field):

```python
class DeletionPriority(str, Enum):
    PRESERVE_OUTPUTS = "preserve_outputs"  # default
    PRESERVE_INPUTS = "preserve_inputs"
```

API endpoints:
- `GET /api/user/deletion-priority` — returns current setting
- `PUT /api/user/deletion-priority` — updates setting

Frontend:
- Add a dropdown/radio in account settings page under subscription section
- Only visible to premium users or users in grace period

---

### 2. Extend Email Service for Subscription Notifications

**Files to modify:**
- `studio/app/common/core/auth/auth_email_service.py` — Add new email methods
- New file: `studio/app/common/core/auth/email_templates.py` — HTML templates

**Email provider:** AWS SES (already in the infrastructure via S3). Firebase's built-in email only supports auth flows (verification, password reset) and cannot send custom transactional emails. SES requires no new vendor — just an API call with a no-reply sender address.

Three new email types:

| Email | Trigger | Content |
|-------|---------|---------|
| `send_grace_period_start_email()` | Day 1 (subscription expires) | "30 days to download/manage data", links to storage page and download tool |
| `send_grace_period_reminder_email()` | Day 20 | Storage breakdown by workspace, deletion date, current priority setting |
| `send_deletion_summary_email()` | After auto-deletion | List of deleted workspace data, size freed, remaining usage |

**SES integration:**
- Add `boto3` SES client (already a dependency for S3)
- Sender: `noreply@{domain}`
- HTML templates stored in `email_templates.py`
- SES sandbox requires verified recipient emails for dev; production requires requesting production access

---

### 3. Expiration Lifecycle Job

**Files to create/modify:**
- New file: `studio/app/common/core/background/expiration_lifecycle_job.py`
- `studio/__main_unit__.py` — Register the new job

**Details:**

New background job `ExpirationLifecycleJob`, runs daily (every 1440 minutes). This job owns the full post-expiration lifecycle: notifications *and* deletion triggering.

```python
class ExpirationLifecycleJob:
    async def run(db_session):
        # 1. Find users where subscription expired today (Day 1)
        #    → Send grace_period_start notification

        # 2. Find users where subscription expired 20 days ago (Day 20)
        #    → Send grace_period_reminder notification

        # 3. Find users where subscription expired 30 days ago (Day 30)
        #    → Trigger auto-deletion (delegate to ExpirationDeletionJob)
        #    → Send deletion_summary notification after completion
```

Query logic (using existing `UserSubscription` model):
- Day 1: `expiration` between `now - 1 day` and `now`
- Day 20: `expiration` between `now - 21 days` and `now - 20 days`
- Day 30: `expiration <= now - 30 days` AND user storage > `FREE_QUOTA_BYTES` AND not already processed

> **Why Day 30 uses `<=` instead of a 1-day window:** This ensures catch-up if the job fails to run on exactly Day 30. Days 1 and 20 use narrow windows because duplicate notifications are merely annoying; a missed deletion must still execute.

**Safety checks:**
- Before executing deletion, the job must verify the user does **not** have an active subscription at execution time. If a user re-subscribes during or after the grace period, skip them entirely. This prevents race conditions between renewal and the daily deletion job.
- Email failures must not block deletion. Notifications are fire-and-forget — log failures, but proceed with the lifecycle. A user who cannot be reached still has their data governed by the policy.

Add a `deletion_processed_at` timestamp to `UserSubscription` to prevent re-processing.

Register in `__main_unit__.py`:
```python
BackgroundScheduler.add_job(
    ExpirationLifecycleJob.run,
    interval_minutes=1440,  # daily
    job_id="expiration_lifecycle"
)
```

---

### 4. Auto-Deletion Engine (Core Logic)

**Files to create/modify:**
- New file: `studio/app/common/core/background/expiration_deletion_job.py`
- `studio/app/common/core/workspace/workspace_services.py` — Add granular deletion methods

**Details:**

The deletion engine is the most complex component. It must:

1. Calculate how much data to delete: `current_usage - FREE_QUOTA_BYTES`
2. Build an ordered list of deletable data units
3. Delete in order until usage is at or below `FREE_QUOTA_BYTES`
4. Track what was deleted for the summary email

#### Data Unit Model

```python
@dataclass
class DeletableDataUnit:
    workspace_id: int
    workspace_name: str
    experiment_uid: Optional[str]
    data_type: str  # "intermediate" | "input" | "output"
    is_published: bool
    created_at: datetime
    size_bytes: int
```

#### Deletion Order Builder

```python
def build_deletion_order(
    workspaces: List[Workspace],
    priority: DeletionPriority
) -> List[DeletableDataUnit]:
    # Collect all data units across all workspaces
    # Sort by the policy-defined priority:
    #
    # PRESERVE_OUTPUTS (default):
    #   1. Intermediates (unpublished, oldest first)
    #   2. Intermediates (published, oldest first)
    #   3. Inputs (unpublished, oldest first)
    #   4. Outputs (unpublished, oldest first)
    #   5. Inputs (published, oldest first)
    #   6. Outputs (published, oldest first)
    #
    # PRESERVE_INPUTS:
    #   1. Intermediates (unpublished, oldest first)
    #   2. Intermediates (published, oldest first)
    #   3. Outputs (unpublished, oldest first)
    #   4. Inputs (unpublished, oldest first)
    #   5. Outputs (published, oldest first)
    #   6. Inputs (published, oldest first)
    #
    # YAMLs are NEVER included in the list
```

#### Deletion Executor

```python
async def execute_expiration_deletion(
    db, user_id: int, dry_run: bool = False
) -> DeletionReport:
    user_sub = get_user_subscription(db, user_id)
    priority = user_sub.deletion_priority or DeletionPriority.PRESERVE_OUTPUTS

    workspaces = get_user_workspaces(db, user_id)
    deletion_order = build_deletion_order(workspaces, priority)

    deleted_items = []
    failed_items = []
    freed_bytes = 0
    current_usage = get_storage_usage(db, user_id)

    for unit in deletion_order:
        if current_usage <= FREE_QUOTA_BYTES:
            break

        if dry_run:
            deleted_items.append(unit)
            current_usage -= unit.size_bytes
            continue

        try:
            delete_data_unit(db, unit)  # S3 + local + DB
            freed_bytes += unit.size_bytes
            current_usage -= unit.size_bytes
            deleted_items.append(unit)
        except Exception as e:
            logger.error(f"Failed to delete {unit}: {e}")
            failed_items.append((unit, str(e)))
            # Continue to next item — partial deletion is acceptable

    return DeletionReport(
        deleted_items, failed_items, freed_bytes, current_usage, dry_run
    )
```

**Operational safety:**
- **Dry-run mode:** Pass `dry_run=True` to log what *would* be deleted without acting. Use this for pre-deployment verification and debugging.
- **Per-item error handling:** Each deletion is independent. If one item fails (e.g., S3 timeout), the job logs the error and continues. The `DeletionReport` tracks both successes and failures. A subsequent job run can retry failed items since `deletion_processed_at` is only set after the job completes.
- **Size drift:** The pre-calculated `size_bytes` on each unit may drift if experiments write data between scan and delete. This is acceptable — the job may slightly under-delete, and the next run will catch the remainder.

#### New Workspace Service Methods

Add to `workspace_services.py`:
- `delete_workspace_intermediates(db, workspace_id)` — Delete node output folders only
- `delete_workspace_inputs(db, workspace_id)` — Delete input data only
- `delete_workspace_outputs(db, workspace_id)` — Delete output NWB files only
- `get_workspace_data_sizes(db, workspace_id)` — Return size breakdown by data type

These build on existing `delete_workspace_files()` but with finer granularity.

#### Size Calculation

Need a way to get per-data-type sizes. Options:
- **S3 prefix listing:** List objects under `{bucket}/input/{workspace_id}/` vs `{bucket}/output/{workspace_id}/` and sum sizes
- **DB tracking:** Add size columns to workspace or experiment models (more performant but requires migration)

**Recommendation:** S3 prefix listing for accuracy, with results cached in DB during the deletion job run. The existing `StorageReconciliationJob` already does similar S3 scanning.

---

### 5. Deletion Record Model

**Files to modify:**
- `studio/app/common/models/subscription.py` — Add `ExpirationDeletionRecord`

**Details:**

Track deletion history for audit and the summary email:

```python
class ExpirationDeletionRecord(Base):
    __tablename__ = "expiration_deletion_records"

    id: int  # PK
    user_id: int  # FK to users
    executed_at: datetime
    priority_used: str  # "preserve_outputs" or "preserve_inputs"
    total_freed_bytes: int
    storage_before_bytes: int
    storage_after_bytes: int
    details_json: dict  # List of deleted items with workspace names, types, sizes
```

---

### 6. Download Enhancements (Frontend)

**Files to modify:**
- Frontend storage/workspace management page
- `studio/app/common/core/storage/download_coordinator.py` — Backend download logic
- `studio/app/common/routers/outputs.py` — Download API endpoints

**Details:**

Add per-workspace download with selectable checkboxes:
- [ ] Input data
- [ ] Output data (NWB files)
- [ ] Workflow YAML files (always included by default since they are never deleted)

Intermediate/node outputs are **not offered for download**.

`download_coordinator.py` exists on branch `feature/download-coordinator` but may not be merged before this work begins. If merged, extend it to support selective data type downloads. If not yet merged, either pull it in as a dependency or inline the bundling logic directly:

```
POST /api/workspace/{workspace_id}/download
Body: { "include_inputs": true, "include_outputs": true, "include_yamls": true }
```

Returns a zip file or initiates an async download job with a download link.

---

### 7. Frontend — Grace Period UI

**Files to modify:**
- Frontend account/subscription page
- Frontend workspace list page
- Frontend notification/banner component

**Details:**

- **Banner:** Show a persistent warning banner during grace period with days remaining and link to manage data
- **Storage page:** Show per-workspace breakdown with download buttons and the checkbox selector
- **Settings:** Deletion priority radio buttons (Preserve Outputs / Preserve Inputs)
- **Workspace list:** Visual indicator for published workspaces (already partially exists via `publish_status`)

---

## Database Migrations

| Table | Change | Type |
|-------|--------|------|
| `user_subscriptions` | Add `deletion_priority` (varchar, default "preserve_outputs") | Column add |
| `user_subscriptions` | Add `deletion_processed_at` (datetime, nullable) | Column add |
| New: `expiration_deletion_records` | Full table creation | New table |

---

## Implementation Order

| Phase | Task | Dependencies |
|-------|------|-------------|
| **Phase 1 — Core** | Deletion priority setting (model + API) | None |
| **Phase 1 — Core** | Deletion order builder logic | None |
| **Phase 1 — Core** | Granular workspace deletion methods | None |
| **Phase 1 — Core** | Deletion record model + migration | None |
| **Phase 2 — Engine** | Auto-deletion executor | Phase 1 |
| **Phase 2 — Engine** | Expiration lifecycle background job | Phase 1, Phase 2 executor |
| **Phase 2 — Engine** | Register job in `__main_unit__.py` | Phase 2 job |
| **Phase 3 — Notifications** | In-app grace period banner | Phase 2 |
| **Phase 3 — Notifications** | SES email notifications (Day 1, Day 20, post-deletion) | Phase 2 |
| **Phase 4 — Frontend** | Deletion priority setting UI | Phase 1 API |
| **Phase 4 — Frontend** | Selective download checkboxes | Download coordinator |
| **Phase 4 — Frontend** | Storage breakdown page enhancements | Phase 1 |

---

## Additional Notes

1. **Email infrastructure:** AWS SES via `boto3` (already a dependency). Firebase only supports auth emails and cannot send custom transactional emails. No-reply sender address.

2. **Intermediate data identification:** Node subdirectories (`{experiment_uid}/{node_id}/`) contain all intermediate data (`.pkl`, per-node `.nwb`, `.json` visualizations). Root-level `whole.nwb` is the final output. `*.yaml` files (`experiment.yaml`, `workflow.yaml`, `snakemake.yaml`) are never deleted. This is a clean directory-level distinction — no ambiguity.

   ```
   output/{workspace_id}/{experiment_uid}/
   ├── whole.nwb              ← FINAL OUTPUT (delete in "output" tier)
   ├── experiment.yaml        ← YAML (never delete)
   ├── workflow.yaml          ← YAML (never delete)
   ├── snakemake.yaml         ← YAML (never delete)
   └── {node_id}/             ← INTERMEDIATE (delete in "intermediate" tier)
       ├── {algo}.pkl
       ├── {algo}.nwb
       └── *.json
   ```

3. **Shared workspaces:** Owner's subscription governs. No special protection for shared workspaces. **Trade-off:** Shared users are not notified — they will see the data is gone when they next access the workspace. This is a deliberate simplification; notifying collaborators would require tracking sharing relationships in the deletion job.

4. **Re-subscription during grace period:** Renewal cancels the grace period entirely. The deletion job checks subscription status at execution time — if the user has an active subscription when the job runs, skip them. This prevents race conditions between renewal and the daily deletion schedule.

## Documentation

Once implementation is complete, add and update infrastructure documentation:

**New file:**
- `infrastructure/documentation/EXPIRED_PLAN_DATA_DELETION_ARCHITECTURE.md` — Full architecture doc covering the deletion lifecycle, job scheduling, deletion order logic, safety checks, and email notifications.

**Existing files to update:**
- `SUBSCRIPTION_BILLING_ARCHITECTURE.md` — Heavily references the grace/warning/overdue timeline. Update to link to the new deletion architecture doc and reflect the final implementation (deletion priority, lifecycle job, dry-run mode).
- `BACKGROUND_JOB_ARCHITECTURE.md` — Add the new `ExpirationLifecycleJob` to the job registry documentation.
- `STORAGE_TRACKING_ARCHITECTURE.md` — Update limit warning section to reference post-grace deletion behavior and the `ExpirationDeletionRecord` audit trail.
- `EBS_STORAGE_ARCHITECTURE.md` — Clarify the relationship between EBS local cleanup (1-hour grace) and the subscription-level deletion (30-day grace). These are separate mechanisms.
- `AUTH_ROUTING_ARCHITECTURE.md` — Verify the subscription status calculation still aligns with the implemented grace period flow.

---

## Testing Strategy

Permanently deleting user data demands high test confidence. Minimum coverage:

| Layer | What to test | Approach |
|-------|-------------|----------|
| **Unit** | `build_deletion_order()` — both priority modes, mixed published/unpublished, YAMLs excluded | Pure logic, no I/O |
| **Unit** | Day-window query logic (Day 1, 20, 30) with edge cases (exactly on boundary, missed days) | Mock DB |
| **Integration** | `execute_expiration_deletion()` end-to-end with mock S3 and real DB | Test containers or SQLite |
| **Integration** | Dry-run mode produces correct report without side effects | Mock S3, assert no deletes |
| **Integration** | Partial failure — one item fails, rest still delete, report includes both | Mock S3 with selective errors |
| **Integration** | Re-subscription safety — user with active sub is skipped even if past Day 30 | Mock DB |
| **E2E** | Full lifecycle: expire → Day 1 email → Day 20 email → Day 30 deletion → summary email | Staging environment |
