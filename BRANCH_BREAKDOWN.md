# Feature Branch Breakdown for AWS Integration

**Base branch:** `develop-subscription`
**Total files changed:** 167

This document tracks how to split the AWS integration work into reviewable feature branches. Each branch represents a complete, working vertical slice of functionality.

---

## Branch 1: Core Infrastructure & Terraform & Docker

**Base AWS infrastructure (foundational)**

### Terraform (11 files)
- `studio/config/terraform/main.tf`
- `studio/config/terraform/infrastructure.tf`
- `studio/config/terraform/compute.tf`
- `studio/config/terraform/deployment.tf`
- `studio/config/terraform/monitoring.tf`
- `studio/config/terraform/security.tf`
- `studio/config/terraform/cost_tracker_package/cost_tracker.py`
- `studio/config/terraform/endofday_shutdown.py`
- `studio/config/terraform/container_access.sh`
- `studio/config/terraform/ecr_build_push.sh`
- `studio/config/terraform/tfvars_example.txt`

**Docker configuration and deployment**

### Docker (2 files)
- `studio/config/docker/Dockerfile`
- `studio/config/docker/Dockerfile.dev`

### Config (3 files)
- `cloud-startup.sh` (shared with storage branch)
- `studio/config/logging.multiuser.yaml` (shared with storage branch)
- `studio/config/logging.yaml` (shared with storage branch)

**Total: ~16 files**

---

## Branch 2: Auth & Session Sync Improvements & SPA Routing & Frontend Navigation

**Enhanced authentication, logout sync, and session management**

### Backend (12 files)
- `studio/app/common/core/auth/auth.py`
- `studio/app/common/core/auth/auth_dependencies.py`
- `studio/app/common/routers/auth.py`
- `studio/app/common/core/background/sync_job.py`
- `studio/app/common/core/background/scheduler.py`
- `studio/app/common/core/background/__init__.py` (shared with free user branch)
- `studio/alembic/versions/a5b9c8d7e6f5_add_sync_logout_and_versioning.py`
- `studio/app/common/core/users/crud_users.py` (shared with premium branch)
- `studio/app/common/db/config.py`
- `studio/app/common/db/database.py` (shared with premium branch)
- `studio/app/common/core/middleware/spa_routing_middleware.py`
- `studio/app/common/core/middleware/__init__.py` (shared with other branches)

### Frontend (9 files)
- `frontend/src/utils/auth/AuthUtils.ts`
- `frontend/src/utils/axios.ts`
- `frontend/src/pages/Login/index.tsx`
- `frontend/src/api/registration/Registration.ts`
- `frontend/src/App.tsx`
- `frontend/src/utils/routing/RoutingService.ts`
- `frontend/src/components/Layout/index.tsx` (shared with premium branch)
- `frontend/src/utils/index.ts`
- `frontend/src/components/PublicLayout/PublicHeader.tsx`
- `frontend/src`

### Tests (2 files)
- `studio/tests/app/common/core/background/test_sync_job.py`
- `studio/tests/app/common/routers/test_users_me_logout.py`

### Dependencies (3 files)
- `poetry.lock` (apscheduler for background scheduler)
- `pyproject.toml` (apscheduler = "3.10.4")
- `frontend/yarn.lock` (frontend dependency updates)
- `.gitignore`

**Total: ~26 files**

---

## Branch 3: Storage Migration (S3 to EBS)

**Complete storage architecture change**

### Backend (15 files)
- `studio/app/common/core/cloud/cloud_utils.py`
- `studio/app/common/core/cloud/s3_storage_monitor.py`
- `studio/app/common/core/cloud/__init__.py`
- `studio/app/common/core/experiment/experiment.py`
- `studio/app/common/dataclass/csv.py`
- `studio/app/common/dataclass/image.py`
- `studio/app/common/dataclass/timeseries.py`
- `studio/app/optinist/dataclass/microscope.py`
- `studio/app/dir_path.py`
- `studio/app/__main_unit__.py` (shared with free user branch)
- `studio/app/common/models/experiment.py`
- `studio/app/common/core/snakemake/snakemake_executor.py` (update_user_storage_after_workflow call)
- `studio/config/logging.multiuser.yaml`
- `studio/config/logging.yaml`
- `studio/test_data/logs/.__studio.lock`

### Tests (2 files)
- `studio/tests/app/common/core/cloud/test_cloud_utils.py`
- `studio/tests/app/common/core/cloud/test_s3_storage_monitor.py`

### Config (1 file)
- `cloud-startup.sh` (EBS mount logic)

### Dependencies (2 files)
- `poetry.lock` (boto3 upgrade)
- `pyproject.toml` (boto3 = "^1.38.27")
.
### Documentation (1 file)
- `studio/config/terraform/plan/EBS_IMPLEMENTATION_PLAN.md`

**Total: ~21 files**

**Note:** This branch includes the storage update call in `snakemake_executor.py`.

---

## Branch 4: Subscription & Billing Integration

**Stripe subscription enhancements**

### Backend (5 files)
- `studio/app/common/core/subscription/constants.py`
- `studio/app/common/core/subscription/webhook_service.py`
- `studio/app/common/schemas/subscriptions.py`
- `studio/alembic/versions/af8c4144cd54_add_stripe_integration_tables.py`
- `studio/app/common/routers/users_me.py` (shared with premium branch)

### Frontend (5 files)
- `frontend/src/api/subscriptions/Subscriptions.ts`
- `frontend/src/pages/AccountManager/index.tsx` (shared with premium branch)
- `frontend/src/pages/Invoice/index.tsx`
- `frontend/src/pages/Invoice/CardBrandIcon.tsx`
- `frontend/src/store/slice/Subscriptions/SubscriptionSlice.ts`

### Tests (1 file)
- `studio/tests/app/common/routers/test_subscription.py`

### Dependencies (2 files)
- `poetry.lock` (stripe dependency)
- `pyproject.toml` (stripe = "^12.5.0")
- `frontend/yarn.lock` (frontend dependency updates)

**Total: ~14 files**

---

## Branch 5: Premium Instance Management

**Complete premium user provisioning system (frontend + backend + infrastructure)**

### Backend (8 files)
- `studio/app/common/core/premium/premium_assignment_service.py`
- `studio/app/common/routers/users_me.py` (premium endpoints)
- `studio/app/common/schemas/users.py` (premium schemas)
- `studio/app/common/core/users/crud_users.py`
- `studio/app/common/models/__init__.py`
- `studio/app/common/db/database.py`
- `studio/alembic/versions/e701e7250019_create_premium_management_system.py`
- `studio/app/common/core/mode.py`

### Frontend (11 files)
- `frontend/src/api/premium/PremiumAssignmentApi.ts`
- `frontend/src/contexts/PremiumAssignmentContext.tsx`
- `frontend/src/components/Premium/PremiumAssignmentManager.tsx`
- `frontend/src/components/Premium/PremiumNotificationManager.tsx`
- `frontend/src/store/slice/User/UserSlice.ts` (premium state)
- `frontend/src/api/users/UsersApiDTO.ts` (premium types)
- `frontend/src/api/users/UsersMe.ts` (premium API calls)
- `frontend/src/components/Layout/index.tsx` (premium UI integration)
- `frontend/src/pages/AccountManager/index.tsx`
- `frontend/src/store/slice/index.ts`
- `frontend/src/store/store.ts`
- `frontend/src/components/Layout/index.tsx`

### Terraform (3 files)
- `studio/config/terraform/premium_manager.tf`
- `studio/config/terraform/premium_manager_package/premium_manager.py`
- `studio/config/terraform/premium_cleanup_package/premium_cleanup.py`

### Scripts (4 files)
- `studio/scripts/cleanup_premium_instances.py`
- `studio/scripts/test_premium_api_integration.py`
- `studio/scripts/test_premium_instance_provisioning.py`
- `studio/scripts/test_premium_lambda.py`
- `studio/scripts/test_premium_load.py`

### Dependencies (2 files)
- `frontend/yarn.lock` (frontend dependency updates)
- `node_modules/.yarn-integrity`

**Total: ~30 files**

---

## Branch 6: Free User Activity Tracking & Auto-Cleanup

**Complete free user lifecycle management (frontend + backend + infrastructure)**

### Backend (9 files)
- `studio/app/common/core/middleware/free_user_activity_middleware.py`
- `studio/app/common/core/middleware/__init__.py`
- `studio/app/common/models/free_user.py`
- `studio/app/common/core/background/cleanup_job.py`
- `studio/alembic/versions/f801f8250020_create_free_user_tracking_system.py`
- `studio/app/common/schemas/users.py` (free user parts - shared with premium branch)
- `studio/app/common/core/workflow/workflow_runner.py` (workflow count tracking)
- `studio/app/common/core/snakemake/snakemake_executor.py` (decrement workflow count)
- `studio/app/__main_unit__.py`

### Frontend (2 files)
- `frontend/src/components/Premium/InactivityWarning.tsx`
- `frontend/src/store/slice/User/UserSlice.ts` (activity tracking state - shared with premium)

### Terraform (4 files)
- `studio/config/terraform/free_manager.tf`
- `studio/config/terraform/free_manager_package/free_manager.py`
- `studio/config/terraform/free_manager_package/free_user_utils.py`
- `studio/config/terraform/free_cleanup_package/free_cleanup.py`

### Tests (2 files)
- `studio/tests/app/common/core/background/test_cleanup_job.py`
- `studio/scripts/test_free_manager.py`

### Dependencies (2 files)
- `poetry.lock` (for workflow tracking dependencies)
- `pyproject.toml` (for workflow tracking dependencies)
- `frontend/yarn.lock` (frontend dependency updates)

**Total: ~20 files**

**Note:** This branch includes workflow count tracking changes in `workflow_runner.py` and `snakemake_executor.py` because they're essential for free user load balancing.

---

## Branch 7: Workflow Tracking & Result Visualization

**Enhanced workflow monitoring, logging, and results display**

### Backend (13 files)
- `studio/app/common/core/workflow/workflow_tracking.py`
- `studio/app/common/core/workflow/workflow_result.py`
- `studio/app/common/core/workflow/workflow_runner.py` (workflow logging/timing)
- `studio/app/common/routers/workflow.py`
- `studio/app/common/routers/dataview.py`
- `studio/app/common/core/dataview/dataview_services.py`
- `studio/app/common/schemas/dataview.py`
- `studio/app/common/core/snakemake/smk_status_logger.py` (error extraction)
- `studio/app/common/core/snakemake/snakemake_executor.py` (shared with storage/free user)
- `studio/app/common/core/rules/runner.py` (directory creation for pid files)
- `studio/app/common/core/rules/file_writer.py`
- `studio/app/common/models/experiment.py` (shared with storage branch)
- `studio/app/Snakefile`

### Frontend (2 files)
- `frontend/src/components/Dataview/WorkflowDetailsView.tsx`
- `frontend/src/pages/Workspace/index.tsx`

### Tests (3 files)
- `studio/tests/app/common/core/workflow/test_workflow_result.py`
- `studio/tests/app/common/core/workflow/test_workflow_tracking.py`
- `studio/tests/app/common/routers/test_dataview_publish.py`

### Dependencies (2 files)
- `poetry.lock` (workflow tracking dependencies)
- `pyproject.toml`
- `frontend/yarn.lock` (frontend dependency updates)

**Total: ~21 files**

**Note:** This branch includes:
- `workflow_runner.py` for workflow timing/logging
- `smk_status_logger.py` for error extraction
- `runner.py` for defensive directory creation
- `snakemake_executor.py` is shared across multiple branches

---

## Branch 8: Test Scripts & Utilities

**Standalone test and utility scripts**

### Test Scripts (15 files)
- `studio/scripts/test_autoscaling_usage.py`
- `studio/scripts/test_autoscaling_user_number.py`
- `studio/scripts/test_database_schema.py`
- `studio/scripts/test_safe_environment_variables.py`
- `studio/scripts/test_standby_integration.py`
- `studio/scripts/test_user_config.py`
- `studio/scripts/test-workflow-tutorial1-post.sh`
- `studio/scripts/test-workflow-tutorial1-postdata.json`

### Utility Scripts (6 files)
- `studio/scripts/create_test_users.py`
- `studio/scripts/delete_test_users.py`
- `studio/scripts/get_jwt_tokens.py`
- `studio/scripts/run_sync_data_capacity.py`
- `studio/scripts/run_sync_data_capacity_cloud.py`
- `studio/scripts/regenerate_free_tokens.sh`

### Test Files (4 files)
- `studio/tests/app/common/core/background/__init__.py`
- `studio/tests/app/common/core/snakemake/test_snakemake_executor_lccd.py`
- `studio/tests/app/common/core/snakemake/test_snakemake_executor_suite2p.py`

**Total: ~25 files**

---

## Files Excluded (AWS Batch - Not Currently Working)

These files are batch-specific and should NOT be included in any branch since AWS Batch is disabled:

### Backend - cloud_batch module (11 files)
- `studio/app/common/core/cloud_batch/__init__.py`
- `studio/app/common/core/cloud_batch/batch_config.py`
- `studio/app/common/core/cloud_batch/batch_context.py`
- `studio/app/common/core/cloud_batch/batch_execution_handler.py`
- `studio/app/common/core/cloud_batch/batch_logging.py`
- `studio/app/common/core/cloud_batch/batch_observation.py`
- `studio/app/common/core/cloud_batch/batch_path_handler.py`
- `studio/app/common/core/cloud_batch/batch_snakemake_executor.py`
- `studio/app/common/core/cloud_batch/batch_utils.py`
- `studio/app/common/core/cloud_batch/config_handler.py`
- `studio/app/common/core/cloud_batch/debug_batch_jobs.py`
- `studio/app/common/core/cloud_batch/snakemake_storage.py`
- `studio/app/common/core/cloud_batch/storage_utils.py`

### Docker (2 files)
- `studio/config/docker/Dockerfile.batch`
- `studio/config/docker/batch-entrypoint.sh`

### Terraform (2 files)
- `studio/config/terraform/batch.tf`
- `studio/config/terraform/batch.tf.backup`

**Total excluded: ~15 files**

---

## Important Notes on Shared Files

Several files appear in multiple branches because they contain changes needed by multiple features:

### Backend Files Shared Across Branches:
- **`studio/app/common/core/workflow/workflow_runner.py`**
  - Used in: Free User (workflow count tracking) + Workflow Tracking (logging/timing)
  - **Recommendation:** Include in Workflow Tracking as primary, Free User can pick it up

- **`studio/app/common/core/snakemake/snakemake_executor.py`**
  - Used in: Storage (storage updates) + Free User (workflow count) + Workflow Tracking
  - **Recommendation:** Include in all three branches defensively

- **`studio/app/common/schemas/users.py`**
  - Used in: Premium + Free User
  - **Recommendation:** Include in both branches

- **`studio/app/common/routers/users_me.py`**
  - Used in: Premium + Subscription
  - **Recommendation:** Include in Premium as primary

- **`studio/app/common/core/middleware/__init__.py`**
  - Used in: Free User + Auth + SPA Routing
  - **Recommendation:** Include in all three

### Frontend Files Shared Across Branches:
- **`frontend/src/components/Layout/index.tsx`**
  - Used in: Premium + SPA Routing

- **`frontend/src/App.tsx`**
  - Used in: Auth + SPA Routing

- **`frontend/src/store/slice/User/UserSlice.ts`**
  - Used in: Premium + Free User

### Dependencies Shared:
- **`poetry.lock` / `pyproject.toml`**: Include in branches that add new Python dependencies
- **`frontend/yarn.lock`**: Include in all frontend-heavy branches

---

## Recommended Review Order

1. **Branch 1: Core Infrastructure & Terraform** (foundation)
2. **Branch 2: Auth & Session Sync SPA Routing & Navigation** (auth foundation)
3. **Branch 3: Storage Migration** (architecture change)
4. **Branch 4: Subscription & Billing** (billing setup)
5. **Branch 5: Premium Instance Management** (premium features)
6. **Branch 6: Free User Activity Tracking** (free tier features)
7. **Branch 7: Workflow Tracking & Visualization** (workflow improvements)
8. **Branch 8: Test Scripts & Utilities** (testing infrastructure)

---

## Summary

- **Total branches:** 8 working branches
- **Files covered:** ~152 files in branches
- **Files excluded:** ~15 batch-related files
- **Overlap strategy:** Defensive duplication of shared files across branches to avoid merge order dependencies
