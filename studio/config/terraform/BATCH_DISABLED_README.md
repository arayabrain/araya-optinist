# Batch Resources - Disabled

All AWS Batch resources have been commented out to reduce costs.

## What Was Changed

### Files Modified:
1. **batch.tf** - Entire file commented out (backup: batch.tf.backup)
2. **monitoring.tf** - Lines 247-268 (Batch Processing Metrics dashboard widget)
3. **security.tf** - Multiple sections:
   - Lines 221-223 (S3 batch bucket in ECS instance policy)
   - Lines 304-321 (AllowBatchJobAccess S3 bucket policy)
   - Lines 427-440 (AWS Batch API permissions in IAM user policy)

## How to Re-enable Batch Resources

### Step 1: Uncomment batch.tf
```bash
# Remove leading "# " from all lines in batch.tf
sed 's/^# //' batch.tf > batch.tf.tmp && mv batch.tf.tmp batch.tf
# Or restore from backup
cp batch.tf.backup batch.tf
```

### Step 2: Uncomment references in other files

Search for `# COMMENTED OUT - Batch resources disabled` in:
- monitoring.tf (around lines 247-268)
- security.tf (around lines 221-223, 304-321, 427-440)

Remove the comment markers from those sections.

### Step 3: Apply Terraform changes
```bash
terraform plan
terraform apply
```

## Backup Location
Original batch.tf saved as: `batch.tf.backup`

## Date Disabled
Created: $(date)
