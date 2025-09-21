# Premium Standby Pool Testing - UPDATED

This document explains how to test the premium standby pool system that provides fast instance assignment through pre-stopped instances.

## Overview

The premium tier implements **STANDBY POOL SCALING** where:

1. **Immediate Assignment**: Users get assigned to available running instances
2. **Standby Activation**: Stopped instances started in 15 seconds when needed
3. **Dynamic Scaling**: New instances created when standby pool depleted
4. **Cost Optimization**: Stopped instances cost 85% less than running instances

## Prerequisites

1. Terraform infrastructure deployed with premium tier components
2. Premium manager Lambda function (`subscr-premium-manager`)
3. Python 3.7+ with `requests` library installed
4. Database with `premium_user_assignments` table

## Test Script Usage

### Install Dependencies
```bash
pip install requests
```

### Run Tests

#### Full Test Suite (Recommended)
```bash
python test_lambda_integration.py
```

#### Specific Premium Tests
```bash
# Test premium assignment flow
python test_lambda_integration.py --test premium

# Test standby pool functionality
python test_lambda_integration.py --test standby

# Test environment variable safety
python test_safe_environment_variables.py
```

## Expected Behavior

### Test 1: Single User Assignment
- ✅ User gets assigned to existing running instance (immediate)
- ✅ Or standby instance started (15 seconds)
- ✅ Standby pool maintains minimum size
- ✅ Replacement standby instance created

### Test 2: Concurrent User Assignment (3 users)
- ✅ First user gets dedicated running instance
- ✅ Second user starts standby instance or shares temporarily
- ✅ Third user triggers new instance creation or sharing
- ✅ Dynamic capacity calculation based on subscriber count

### Test 3: Standby Pool Management
- ✅ Stopped instances maintain 85% cost savings
- ✅ Fast startup (5-15 seconds) vs new instances (60-90 seconds)
- ✅ Automatic replacement when standby used
- ✅ Pool size maintained according to configuration

### Test 4: Scale Down and Cost Optimization
- ✅ Idle instances converted to standby after user logout
- ✅ Excess instances terminated to maintain cost efficiency
- ✅ Minimum pool size preserved for quick response

## Monitoring During Tests

### CloudWatch Logs
Monitor this log group during testing:
- `/aws/lambda/subscr-premium-manager`

### Database Monitoring
Check the `premium_user_assignments` table:
```sql
SELECT user_id, instance_id, instance_state, is_standby, status, assigned_at
FROM premium_user_assignments
ORDER BY assigned_at DESC;
```

### EC2 Console
- Monitor premium instance states (running/stopped/terminated)
- Check instance tags (`Type=premium-standby`, `Service=optinist-premium`)
- Verify cost optimization through stopped instances

### ECS Console
- Monitor ECS tasks on premium instances
- Check task health and readiness
- Verify container startup times

## Test Commands

### Environment Variable Safety Testing
```bash
# Test safe environment variable access
python test_safe_environment_variables.py

# Expected output:
# ✅ All safe environment variable tests passed!
# ✅ Environment variables are accessed safely
# ✅ Missing env vars won't crash the Lambda
```

### Database Schema Testing
```bash
# Test database schema compatibility
python test_database_schema.py

# Expected output:
# ✅ All database schema tests passed!
# ✅ The enum fix prevents SQL runtime errors
# ✅ Critical 'stopped' state operations will work
```

### Premium API Integration Testing
```bash
# Test premium API endpoints
python test_premium_api_integration.py

# Expected output:
# ✅ All API integration tests passed!
# ✅ Heartbeat endpoint works correctly
# ✅ Assignment/release/status endpoints functional
```

## Troubleshooting

### Common Issues

#### Assignment Fails with 500 Error
- **Cause**: Environment variable configuration error
- **Solution**: Check Lambda environment variables are set correctly
- **Debug**: Look for "Missing required environment variable" in logs

#### Assignment Returns 503 (Service Unavailable)
- **Cause**: No available instances and scaling in progress
- **Solution**: Wait 60-90 seconds for new instances to launch
- **Expected**: Users fall back to free tier during scaling

#### Standby Instances Not Starting
- **Cause**: Launch template or IAM permissions issues
- **Solution**: Check `PREMIUM_LAUNCH_TEMPLATE_ID` and Lambda execution role
- **Debug**: Look for AWS API errors in CloudWatch logs

#### Users Not Getting Dedicated Instances
- **Cause**: Standby pool depleted or instance health check failures
- **Solution**: Check ECS task status and health checks
- **Debug**: Monitor instance readiness checks in logs

### Manual Verification

#### Check Lambda Function Status
```bash
aws lambda get-function --function-name subscr-premium-manager
```

#### Test Lambda Function Directly
```bash
# Test assignment
aws lambda invoke --function-name subscr-premium-manager \
  --payload '{"httpMethod":"POST","body":"{\"action\":\"assign\",\"user_id\":\"test-123\",\"tier\":\"premium\"}"}' response.json

cat response.json
```

#### Check Instance States
```bash
# Get premium instances
aws ec2 describe-instances --filters "Name=tag:Service,Values=optinist-premium"

# Check standby instances specifically
aws ec2 describe-instances --filters "Name=tag:Type,Values=premium-standby" "Name=instance-state-name,Values=stopped"
```

#### Verify Database State
```sql
-- Check standby pool status
SELECT
  instance_state,
  is_standby,
  COUNT(*) as count
FROM premium_user_assignments
WHERE status = 'active'
GROUP BY instance_state, is_standby;

-- Check active assignments
SELECT
  user_id,
  instance_id,
  instance_state,
  is_standby,
  assigned_at
FROM premium_user_assignments
WHERE status = 'active' AND is_standby = 0
ORDER BY assigned_at DESC;
```

## Success Criteria

The standby pool implementation is working correctly when:

1. ✅ **Fast Assignment**: Users get instances within 15 seconds from standby pool
2. ✅ **Cost Optimization**: Stopped instances provide 85% cost savings
3. ✅ **Dynamic Scaling**: Capacity adjusts based on premium subscriber count
4. ✅ **Graceful Fallback**: Users fall back to free tier during scaling events
5. ✅ **Pool Management**: Standby pool maintains configured size automatically
6. ✅ **Environment Safety**: No Lambda crashes due to missing configuration

## Performance Metrics

Monitor these key metrics:
- **Assignment Time**: < 15 seconds from standby pool, < 90 seconds for new instances
- **Startup Time**: 5-15 seconds for stopped→running, 60-90 seconds for new instances
- **Cost Savings**: 85% reduction for stopped instances vs running instances
- **Pool Utilization**: Standby pool usage and replacement rates
- **Error Rates**: Assignment failures and fallback frequency

## Architecture Changes from Original

### ❌ **REMOVED** (Original Design)
- Spot fleet scaling system
- Migration queue Lambda function
- State migration between tiers
- Complex migration testing utilities

### ✅ **IMPLEMENTED** (Current Design)
- Standby pool with stopped instances
- Single Lambda function architecture
- Immediate assignment with graceful fallback
- Context API for frontend state management
- Safe environment variable access
- Comprehensive test suite

### **Benefits of Current Architecture**
1. **Simpler**: Single Lambda vs multiple functions
2. **Faster**: 15-second assignment vs 6-minute migration
3. **Safer**: Environment variable safety and transaction locks
4. **Cost-Effective**: 85% savings with stopped instances
5. **Reliable**: Graceful fallback and error handling
