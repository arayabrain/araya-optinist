# End-to-End Testing: Proactive Experiment Sync

This document describes the manual testing procedure to verify that experiment metadata syncs correctly when users are migrated between instances.

## Prerequisites

- Access to AWS Console (ECS, CloudWatch Logs, Secrets Manager)
- Access to the application as a test user
- SSH access to EC2 instances (optional, for filesystem verification)

### Objective
Verify that after a user migration, their experiments are automatically synced to the new instance and accessible without 404 errors.

---

### Step 1: Setup - Create Test Experiment

1. **Log in** to the application as a free tier test user
2. **Create a new experiment**:
   - Go to the experiment creation page
   - Run a simple workflow (e.g., basic image processing)
   - Wait for the experiment to complete
3. **Record the experiment details**:
   - Note the `workspace_id` and `unique_id` from the URL
   - Example: `/experiments/ws_abc123/exp_xyz789`
4. **Verify experiment is accessible**:
   - Navigate to the experiment results page
   - Confirm all outputs load correctly

---

### Step 2: Identify Current Instance

1. **Check AWS ECS Console**:
   - Go to ECS > Clusters > `optinist-free-cluster`
   - Note which EC2 instance is running the user's task
2. **Verify in database** (optional):
   ```sql
   SELECT user_id, instance_id, last_activity
   FROM free_user_assignments
   WHERE user_id = <test_user_id>;
   ```

---

### Step 3: Trigger User Migration

Use the test script at `scripts/test_proactive_sync.py`:

1. **Navigate to Terraform directory** (for auto-config):
   ```bash
   cd infrastructure/terraform
   ```

2. **Find your test user's ID**:
   ```bash
   python ../../scripts/test_proactive_sync.py --from-terraform find-user user@email.com
   ```

3. **Migrate user** (auto-scales if needed):
   ```bash
   python ../../scripts/test_proactive_sync.py --from-terraform migrate <user_id>
   ```
   This will automatically:
   - Scale ASG to 2 instances if only 1 exists (waits ~3-5 min)
   - Select a different instance as target
   - Update the database assignment
   - Trigger experiment sync on the new instance


**Expected output**:
```
Loading configuration from Terraform outputs...
Fetching secrets from AWS Secrets Manager...
  ALB DNS: internal-subscr-alb-123456.us-west-2.elb.amazonaws.com
  DB Host: subscr-rds.abc123.us-west-2.rds.amazonaws.com
  DB User: optinist
  Secrets loaded successfully

=== Migrating user 42 to (auto-select) ===
Only 1 instance available. Scaling up...
Scaling ASG 'subscr-optinist-free-asg' to 2 instances...
Current capacity: 1, Instances: ['i-0old111222333']
Requested scale to 2 instances
Waiting for instances to become healthy...
  1/2 instances healthy (10s elapsed)
  1/2 instances healthy (20s elapsed)
  2/2 instances healthy (180s elapsed)
Auto-selected target instance: i-0abc123def456
Status: migrated
From: i-0old111222333
To: i-0abc123def456
Sync Result: {'status_code': 200, 'response': {'status': 'sync_initiated', 'user_id': 42}}
```

---

### Step 4: Verify Sync Trigger

1. **Check CloudWatch Logs** for the target instance:
   - Filter: `sync_user_experiments`
   - Expected log: `Initiating experiment sync for user X`
   - Expected log: `Experiment sync completed for user X`

2. **Check for rate limiting** (if applicable):
   - If you see `Rate limited sync request`, wait 10 seconds and retry

3. **Verify Internal API Call**:
   - In Lambda logs, look for: `Experiment sync initiated for user`
   - HTTP 200 response confirms sync was triggered

---

### Step 5: Verify Experiment Accessibility

1. **Clear browser cache** (important!)
2. **Log in** to the application again
3. **Navigate to the experiment** created in Step 1
4. **Verify**:
   - [ ] Experiment list shows the experiment
   - [ ] Experiment details page loads without 404
   - [ ] Experiment results/outputs are accessible
   - [ ] Workflow can be reproduced (`/reproduce` endpoint works)

---

### Step 6: Verify Lazy Sync (On-Demand)

This tests the fallback sync mechanism when proactive sync didn't complete:

1. **SSH to the new instance** (or check via logs)
2. **Delete the local experiment metadata**:
   ```bash
   rm -rf /data/output/<workspace_id>/<unique_id>/experiment.yaml
   ```
3. **Access the experiment via the UI**
4. **Check application logs**:
   - Expected: `Experiment config not found locally, syncing from S3`
   - Expected: File should be re-downloaded from S3
5. **Verify** the experiment loads correctly after lazy sync

---

## Troubleshooting

### Issue: Sync API returns 503
**Cause**: `INTERNAL_API_SECRET` not configured
**Fix**: Check Secrets Manager and ECS task definition environment variables

### Issue: Sync API returns 403
**Cause**: Secret mismatch between Lambda and ECS task
**Fix**: Verify both services use the same secret from Secrets Manager

### Issue: Sync API returns 429
**Cause**: Rate limiting (multiple syncs within 10 seconds)
**Fix**: Wait 10 seconds and retry

### Issue: Experiment still shows 404 after migration
**Cause**:
1. Sync failed (check CloudWatch logs)
2. S3 bucket doesn't have the experiment data
3. Wrong workspace_id in database

**Debug steps**:
1. Check Lambda logs for sync trigger status
2. Check ECS task logs for sync execution
3. Verify S3 bucket contains experiment metadata
4. Check database for correct workspace assignment

### Issue: Lazy sync doesn't trigger
**Cause**: Remote storage not configured or unavailable
**Fix**: Check `REMOTE_STORAGE_BUCKET` environment variable and S3 permissions

---
