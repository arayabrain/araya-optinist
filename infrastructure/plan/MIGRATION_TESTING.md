# Premium Assignment System Testing Guide - UPDATED

## Overview

This guide explains how to test the premium assignment system that provides immediate access to dedicated instances through a standby pool architecture.

**ARCHITECTURE CHANGE**: The system no longer uses migration. Instead, premium users get immediate assignment to dedicated instances with graceful fallback to free tier during scaling.

## Browser Console Testing

### Premium Assignment Context Testing

```javascript
// Access the premium assignment context (available in components)
// Note: Context is only available within React component tree

// Check current assignment status
// (Access through component state or Redux store)
console.log("Check Redux store for premium assignment state")

// Monitor assignment in browser network tab
// Look for requests to /users/me/premium/assign
// Check for X-User-Tier and X-User-ID headers in requests
```

### Direct API Testing

```javascript
// Test premium status endpoint
fetch('/users/me/premium/status')
  .then(response => response.json())
  .then(data => console.log('Premium status:', data))

// Test assignment endpoint
fetch('/users/me/premium/assign', { method: 'POST' })
  .then(response => response.json())
  .then(data => console.log('Assignment result:', data))

// Test routing info endpoint
fetch('/users/me/routing-info')
  .then(response => response.json())
  .then(data => console.log('Routing info:', data))
```

## Expected Assignment Flow

### 1. Premium User Login
- User authenticates with premium subscription
- `PremiumAssignmentContext` automatically detects premium user
- Assignment attempt starts immediately
- User continues using current instance (no interruption)

### 2. Assignment Process (15-90 seconds)
- **Priority 1**: Use available running instance (immediate)
- **Priority 2**: Start standby instance (15 seconds)
- **Priority 3**: Share existing instance temporarily
- **Priority 4**: Scale up new instances (60-90 seconds)

### 3. Assignment Completion
- Premium instance ready and passes health checks
- Routing headers activated (`X-User-Tier: premium`, `X-User-ID: {id}`)
- User seamlessly continues on dedicated instance
- Background: Replacement standby instance created if needed

## Testing Checklist

### ✅ Immediate Assignment
- [ ] Premium users get instant assignment attempt on login
- [ ] No 6-minute wait period (old migration system)
- [ ] Graceful fallback to free tier during scaling
- [ ] Assignment status reflected in Redux store

### ✅ Standby Pool Functionality
- [ ] Stopped instances start within 15 seconds
- [ ] Cost optimization: 85% savings vs running instances
- [ ] Automatic replacement of used standby instances
- [ ] Pool size maintained according to configuration

### ✅ Routing Integration
- [ ] Premium headers activated after assignment
- [ ] Requests route to dedicated instance
- [ ] Fallback to free tier works during failures
- [ ] No headers for free tier users

### ✅ Error Handling & Safety
- [ ] Environment variable safety prevents Lambda crashes
- [ ] Database transaction locks prevent race conditions
- [ ] Assignment failures return proper error codes
- [ ] Users always have access (fallback to free tier)

## Console Log Messages

### Normal Operation
```
🚀 === PREMIUM USER ASSIGNMENT START ===
📊 Assignment context: running=2, standby_available=1, active_users=3
✅ Starting standby instance i-1234567890abcdef0
Creating replacement standby instance
✅ === ASSIGNMENT SUCCESS ===
📋 Assignment details: instance_id=i-1234567890abcdef0, source=standby
```

### During Scaling
```
📊 Enhanced premium instance analysis: need_capacity=1, available=0
🔧 Scaling up: creating 1 new instances
⚠️ Scale up in progress, user may share instance temporarily
```

### Error Handling
```
❌ Assignment failed - environment configuration error: Missing required environment variable: VPC_ID
❌ Instance readiness check failed - environment configuration error
⚠️ Standby conversion failed but continuing: [error details]
```

## Manual Testing Scenarios

### Scenario 1: Normal Premium Login
1. Login with premium account
2. Verify immediate assignment attempt
3. Check network tab for API calls to `/users/me/premium/assign`
4. Verify premium headers in subsequent requests
5. Confirm dedicated instance access

### Scenario 2: Standby Pool Assignment
1. Login when no running instances available
2. Monitor assignment for 15-second standby startup
3. Verify new standby instance created as replacement
4. Check cost optimization (stopped vs running instance costs)

### Scenario 3: Scaling Scenario
1. Login multiple premium users simultaneously
2. Verify graceful sharing during scale-up
3. Monitor new instance creation (60-90 seconds)
4. Check eventual migration to dedicated instances

### Scenario 4: Error Handling
1. Simulate configuration errors (if possible in dev)
2. Verify graceful fallback to free tier
3. Check error logging and user notifications
4. Ensure no service disruption

## API Endpoints for Testing

### Premium Status Endpoint
```bash
GET /users/me/premium/status
```

Expected response:
```json
{
  "user_id": 123,
  "subscription_type": "premium",
  "is_premium": true,
  "assignment": {
    "instance_id": "i-1234567890abcdef0",
    "status": "active",
    "assigned_at": "2023-01-01T12:00:00Z",
    "is_shared": false
  }
}
```

### Premium Assignment Endpoints
```bash
POST /users/me/premium/assign    # Start assignment
DELETE /users/me/premium/assign  # Release assignment
GET /users/me/routing-info       # Get routing headers
```

### Heartbeat Endpoint (NEW)
```bash
POST /users/me/premium/heartbeat # Update user activity
```

Expected response:
```json
{
  "updated": true,
  "message": "Activity updated successfully",
  "user_id": 123,
  "user_tier": "premium",
  "assignment_active": true
}
```

## Test Scripts Available

### Environment Variable Safety
```bash
python /studio/scripts/test_safe_environment_variables.py
```

### Database Schema Validation
```bash
python /studio/scripts/test_database_schema.py
```

### Premium API Integration
```bash
python /studio/scripts/test_premium_api_integration.py
```

### Lambda Function Testing
```bash
python /studio/scripts/test_lambda_integration.py
```

## Troubleshooting

### Assignment Not Starting
- Check user has premium subscription in `/users/me`
- Verify `PremiumAssignmentManager` is included in Layout component
- Check Redux store for premium assignment state
- Look for console errors in browser developer tools

### Assignment Failing
- Check Lambda logs in CloudWatch (`/aws/lambda/subscr-premium-manager`)
- Verify environment variables are set correctly
- Check database connection and table schema
- Monitor EC2 instances for capacity issues

### Headers Not Applied
- Check network tab for `X-User-Tier` and `X-User-ID` headers
- Verify routing service is working correctly
- Check axios interceptor configuration
- Confirm assignment completed successfully

### Fallback Not Working
- Verify fallback removes premium headers
- Check free tier routing still works
- Monitor console for fallback messages
- Test axios interceptor error handling

## Performance Monitoring

Monitor these metrics during testing:
- **Assignment start time**: Should be immediate on login
- **Standby startup time**: 5-15 seconds for stopped→running
- **New instance time**: 60-90 seconds for new instance creation
- **Total assignment time**: Varies by availability
- **Fallback frequency**: Should be rare under normal conditions

## Architecture Comparison

### ❌ **OLD SYSTEM** (Removed)
- 6-minute migration process
- State transfer between tiers
- Complex browser testing utilities
- Migration queue Lambda function
- Spot fleet scaling

### ✅ **CURRENT SYSTEM** (Implemented)
- Immediate assignment (15-90 seconds)
- Standby pool cost optimization
- Context API state management
- Single Lambda architecture
- Regular EC2 with dynamic scaling
- Environment variable safety
- Comprehensive error handling

### **Benefits of Current Architecture**
1. **Faster**: 15-second assignment vs 6-minute migration
2. **Simpler**: Single context vs complex migration state
3. **Cheaper**: 85% cost savings with stopped instances
4. **Safer**: Environment safety and transaction locks
5. **More Reliable**: Graceful fallback and error handling
6. **Better UX**: No migration interruption, immediate access

## Success Criteria

The premium assignment system is working correctly when:

1. ✅ **Immediate Access**: Users get assignment attempt on login
2. ✅ **Fast Assignment**: 15-second response from standby pool
3. ✅ **Cost Efficiency**: Stopped instances reduce costs by 85%
4. ✅ **Graceful Fallback**: Free tier access during scaling
5. ✅ **No Crashes**: Environment safety prevents service disruption
6. ✅ **Reliable State**: Context API maintains consistent assignment state
