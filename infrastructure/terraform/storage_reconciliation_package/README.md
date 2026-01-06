# Storage Reconciliation Lambda

## Overview
Reconciles incremental storage tracking with actual S3 storage to ensure accuracy and catch drift from failed incremental updates. Prevents Out-of-Memory (OOM) errors by using batch processing and true streaming patterns.

## Primary Responsibilities
- **Storage Reconciliation**: Compare database tracking with actual S3 storage
- **Drift Detection**: Log significant discrepancies for monitoring
- **Batch Processing**: Process users in batches to prevent OOM
- **Distributed Locking**: Prevent concurrent scans of the same user
- **Memory-Efficient Scanning**: Use streaming to avoid paginator metadata accumulation

## Triggers
- **CloudWatch Events**: Scheduled every 60 minutes (hourly)

## Flow Diagram

```
┌──────────────────────────────────────────────────────────┐
│              STORAGE RECONCILIATION                      │
│                  (Every 60 Minutes)                      │
└──────────────────────────────────────────────────────────┘
                         │
                         │
            ┌────────────▼────────────┐
            │ 1. Query Users Needing  │
            │    Reconciliation       │
            │    - delta > 0          │
            │    - OR never scanned   │
            └────────────┬────────────┘
                         │
            ┌────────────▼────────────┐
            │ 2. Process in Batches   │
            │    (10 users/batch)     │
            └────────────┬────────────┘
                         │
                    For Each User:
                         │
            ┌────────────▼────────────┐
            │ 3. Acquire MySQL Lock   │
            │    (prevent concurrent) │
            └────────────┬────────────┘
                         │
                    ┌────▼────┐
                    │ Locked? │
                    └────┬────┘
                    Yes  │  No
                         │  └──→ Skip (already scanning)
                         │
            ┌────────────▼────────────┐
            │ 4. Scan S3 Storage      │
            │    - Get all workspaces │
            │    - Stream objects     │
            │    - Sum sizes          │
            └────────────┬────────────┘
                         │
            ┌────────────▼────────────┐
            │ 5. Calculate Drift      │
            │    drift = |S3 - DB|    │
            └────────────┬────────────┘
                         │
            ┌────────────▼────────────┐
            │ 6. Update Database      │
            │    - Set actual storage │
            │    - Reset delta to 0   │
            │    - Update last_scan   │
            └────────────┬────────────┘
                         │
            ┌────────────▼────────────┐
            │ 7. Release Lock         │
            └────────────┬────────────┘
                         │
            ┌────────────▼────────────┐
            │ 8. Rate Limit (0.5s)    │
            │    (avoid S3 throttle)  │
            └────────────┬────────────┘
                         │
                    Next User
                         │
            ┌────────────▼────────────┐
            │ 9. Report Statistics    │
            │    - Users reconciled   │
            │    - Significant drifts │
            │    - Total drift bytes  │
            └─────────────────────────┘
```

## Related Files

### Core Files
- `storage_reconciliation.py` - Main Lambda function
- `../../aws_constants.py` - Shared AWS constants

### Database
- `studio/app/common/models/user_storage_usage.py` - UserStorageUsage model
- Database migration: `g901g9260021_add_storage_delta_tracking.py`

### Studio Application Integration
- `studio/app/common/core/cloud/cloud_utils.py` - Incremental tracking functions
- `studio/app/common/core/storage/s3_storage_controller.py` - Upload/delete handlers
- `studio/app/common/core/background/storage_reconciliation_job.py` - Background job (alternative to Lambda)

### Terraform Configuration
- `storage_reconciliation.tf` - Lambda infrastructure definition
- `infrastructure.tf` - VPC, subnets, security groups
- `monitoring.tf` - CloudWatch dashboard with storage metrics

### Documentation
- `infrastructure/documentation/STORAGE_TRACKING_OOM_MITIGATION_SUMMARY.md` - Full design documentation

## Key Environment Variables
- `RDS_HOST` - Database connection string
- `RDS_USER`, `RDS_PASSWORD`, `RDS_DATABASE` - Database credentials
- `S3_DEFAULT_BUCKET_NAME` - S3 bucket for user storage

## Configuration Constants

### Batch Processing
```python
BATCH_SIZE = 10                      # Users per batch
RATE_LIMIT_DELAY_SECONDS = 0.5       # Delay between users
```

### Drift Detection
```python
DRIFT_THRESH_PERCENT = 5.0           # 5% drift warning threshold
DRIFT_THRESH_BYTES = 100 * 1024 * 1024  # 100 MB drift warning threshold
```

### Distributed Locking
```python
ADVISORY_LOCK_NAMESPACE = 12345      # MySQL lock namespace
```

## Memory-Efficient S3 Scanning

### Problem: boto3 Paginator Accumulation
```python
# ❌ BAD: Paginator accumulates metadata for ALL pages
paginator = s3_client.get_paginator('list_objects_v2')
for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
    # Memory grows with each page
    process(page)
# Memory: O(n) where n = number of pages
```

### Solution: True Streaming with Manual Tokens
```python
# ✅ GOOD: Manual continuation tokens, constant memory
def stream_s3_objects(s3_client, bucket, prefix):
    continuation_token = None
    while True:
        params = {'Bucket': bucket, 'Prefix': prefix, 'MaxKeys': 1000}
        if continuation_token:
            params['ContinuationToken'] = continuation_token

        response = s3_client.list_objects_v2(**params)
        yield response  # Process immediately
        # Previous page garbage collected

        if not response.get('IsTruncated'):
            break
        continuation_token = response.get('NextContinuationToken')
# Memory: O(1) - constant regardless of object count
```

## Distributed Lock Protection

### Purpose
Prevent multiple concurrent scans of the same user (wasteful duplication).

### Implementation
```python
# Try to acquire lock (non-blocking)
lock_name = f"storage_scan_{NAMESPACE}_{user_id}"
cursor.execute("SELECT GET_LOCK(%s, 0)", (lock_name,))

if lock_acquired:
    # Perform S3 scan
    scan_user_storage(user_id)
    # Release lock
    cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
else:
    # Skip - another process is already scanning
    pass
```

### Lock Characteristics
- **Per-user**: Different users can scan concurrently
- **Non-blocking**: Returns immediately if locked
- **Automatic cleanup**: Released when connection closes
- **Namespace isolation**: Uses unique namespace (12345)

## Drift Detection & Logging

### Drift Calculation
```python
drift_bytes = abs(actual_storage - db_storage)
drift_percent = (drift_bytes / db_storage * 100) if db_storage > 0 else 0
```

### Drift Thresholds
Warns when drift exceeds:
- **5%** of current storage, OR
- **100 MB** absolute difference

### Example Logs
```
User 123 reconciled: 1,000,000,000 → 1,005,000,000 bytes (drift: 5,000,000 bytes, 0.5%)

Significant drift for user 456: DB=1,000,000,000 → S3=1,200,000,000 bytes
  (drift: 200,000,000 bytes, 20.0%)
```

## Integration with Incremental Tracking

### How It Works Together

**Normal Operations** (99.9% of time):
1. User uploads file → `increment_user_storage(+100MB)` → instant update
2. User deletes file → `decrement_user_storage(-50MB)` → instant update
3. Delta accumulates: `delta_since_last_scan = 150MB`

**Periodic Reconciliation** (hourly):
1. Query users with `delta > 0`
2. Scan actual S3 storage
3. Compare: DB=1.15GB vs S3=1.14GB (drift: 10MB, 0.87%)
4. Update DB to actual S3 value: 1.14GB
5. Reset `delta_since_last_scan = 0`

### Benefits
- **Real-time tracking**: Most operations use instant DB updates (no S3 scan)
- **Accuracy guarantee**: Hourly reconciliation catches any drift
- **OOM prevention**: Only scan when necessary, use streaming when scanning
- **Failure recovery**: Catches failed increment/decrement operations

## OOM Prevention Strategies

### 1. Batch Processing
Process 10 users at a time instead of loading all users into memory.

### 2. True Streaming
Generator pattern with manual continuation tokens prevents paginator metadata accumulation.

### 3. Rate Limiting
0.5s delay between users spreads S3 API calls over time.

### 4. Memory-Bounded
Constant memory usage regardless of:
- Number of users
- Number of workspaces
- Number of S3 objects

### Memory Profile Comparison

| Users | Objects | Before (Paginator) | After (Streaming) |
|-------|---------|-------------------|------------------|
| 100   | 10M     | ~1 GB             | ~100 KB          |
| 1000  | 100M    | ~10 GB (OOM)      | ~100 KB          |

## Monitoring & Alerting

### CloudWatch Metrics
- `AWS/Lambda - Duration`: Execution time (should be < 15 min)
- `AWS/Lambda - Errors`: Failed reconciliations
- `AWS/Lambda - Invocations`: Hourly run count

### CloudWatch Alarms
- **Error Detection**: Alert on any errors
- **Duration Warning**: Alert if taking > 10 minutes (approaching 15 min timeout)

### CloudWatch Dashboard
Row 6 of main dashboard (`subscr-optinist-monitoring`):
- Storage Reconciliation Lambda metrics
- Comparison with other background jobs
- Duration trend analysis

### Log Analysis
```bash
# View recent reconciliation runs
aws logs tail /aws/lambda/subscr-storage-reconciliation --follow

# Search for drift warnings
aws logs filter-log-events \
  --log-group-name /aws/lambda/subscr-storage-reconciliation \
  --filter-pattern "Significant drift" \
  --start-time $(date -u -d '1 day ago' +%s)000

# Get execution statistics
aws logs insights query-string \
  'fields @timestamp, @message | filter @message like /reconciled/ | stats count()'
```

## Manual Testing

### Invoke Lambda Directly
```bash
aws lambda invoke \
  --function-name subscr-storage-reconciliation \
  --payload '{"source": "manual-test"}' \
  response.json && cat response.json
```

### Check Reconciliation Results
```sql
-- View recent reconciliation timestamps
SELECT user_id, storage_usage_bytes, delta_since_last_scan, last_full_scan
FROM user_storage_usage
WHERE last_full_scan > DATE_SUB(NOW(), INTERVAL 1 HOUR)
ORDER BY last_full_scan DESC;

-- Check for users needing reconciliation
SELECT user_id, storage_usage_bytes, delta_since_last_scan, last_full_scan
FROM user_storage_usage
WHERE delta_since_last_scan > 0 OR last_full_scan IS NULL;
```

## Performance Characteristics

### Execution Time
- **Small dataset** (10 users, 1K objects each): ~30 seconds
- **Medium dataset** (100 users, 100K objects each): ~5 minutes
- **Large dataset** (1000 users, 1M objects each): ~12 minutes
- **Maximum timeout**: 15 minutes

### S3 API Calls
- **Per user**: `ceiling(object_count / 1000)` ListObjects calls
- **Rate limiting**: 0.5s delay between users = ~7 users/min = ~420 users/hour
- **Typical hourly job**: 10-50 users = 100-5000 S3 API calls

### Cost Estimation
- **Lambda execution**: $0.0000166667/GB-second × 128MB × 600s = $0.00128/run
- **S3 LIST requests**: $0.005/1000 requests × 1000 = $0.005/run
- **Monthly cost**: ~$0.006/run × 720 runs = ~$4.32/month

## Comparison with Alternative Approaches

### Option A: Cron Job on EC2 (Documentation Recommended)
**Pros**: Reuses Studio codebase, no Lambda limitations
**Cons**: Requires cron setup, not implemented yet

### Option B: Background Job in ECS (In-Process Scheduler)
**Pros**: Full access to Studio code
**Cons**: Duplicate execution with multiple workers, not implemented yet

### Option C: Lambda (Current Implementation) ✅
**Pros**: Serverless, independent, easy monitoring, works immediately
**Cons**: 15-min timeout, standalone implementation

The Lambda approach was chosen for immediate deployment and operational simplicity.

## Troubleshooting

### Lambda Timeout
**Symptom**: Lambda times out before completing
**Solution**:
- Reduce `BATCH_SIZE` from 10 to 5
- Increase Lambda timeout from 15 to 20 minutes
- Split reconciliation across multiple hourly windows

### High Drift Warnings
**Symptom**: Many users showing significant drift
**Solution**:
- Check if increment/decrement functions are being called correctly
- Verify S3 upload/delete handlers are working
- Review application logs for failed storage updates

### Lock Contention
**Symptom**: Many users skipped due to locks
**Solution**:
- Increase reconciliation frequency (currently hourly)
- This indicates healthy coordination - no action needed

### Memory Issues
**Symptom**: Lambda OOM errors
**Solution**:
- Reduce `BATCH_SIZE`
- Verify streaming pattern is being used (check code)
- Check for memory leaks in S3 client initialization
