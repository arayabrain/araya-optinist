# Premium Routing Implementation Guide - UPDATED

## Overview

Premium users get automatically assigned to their own dedicated instances with **STANDBY POOL ARCHITECTURE**:
1. **Free Tier** - Shared free instances (all users start here)
2. **Standby Pool** - Pre-stopped premium instances for fast assignment (~15 seconds)
3. **Dedicated Premium** - User's own private instance

**CURRENT ARCHITECTURE**: Premium users get immediate assignment from standby pool (stopped instances started on-demand) with graceful fallback to free tier during scaling.

## Quick Architecture Summary

**SIMPLIFIED ROUTING STRATEGY**:
- **Priority 10**: `X-User-Tier: premium` + `X-User-ID: {id}` → Dedicated instance ✅ **IMPLEMENTED**
- **Default Rule**: No headers → Free tier ✅ **IMPLEMENTED**

**USER FLOW**:
1. **Premium Login** → Immediate assignment attempted
2. **Standby Pool** → Stopped instance started (15 seconds) or share existing
3. **Automatic Assignment** → Headers activated when instance ready
4. **Seamless Experience** → User continues with dedicated resources

**COMPONENTS**:
1. ✅ `PremiumAssignmentContext` - Context API for assignment state
2. ✅ `RoutingService` - Header management
3. ✅ `PremiumAssignmentApi` - Core API calls
4. ✅ `PremiumAssignmentManager` - Assignment orchestration
5. ✅ Axios interceptor with fallback to free tier

## Current Implementation Status

### ✅ **IMPLEMENTED COMPONENTS**

1. **Core Routing System:**
   - Premium routing headers (`X-User-Tier`, `X-User-ID`)
   - ALB listener rules with proper priority
   - Fallback mechanism to free tier

2. **Frontend Integration:**
   - `PremiumAssignmentManager.tsx` - Auto-assignment orchestration
   - `PremiumAssignmentContext.tsx` - Context API for state management ✅ **ACTUAL**
   - `RoutingService.ts` - Header management
   - `PremiumAssignmentApi.ts` - API calls
   - Enhanced axios interceptor with fallback

3. **Backend Services:**
   - `premium_assignment_service.py` - Backend service
   - Enhanced `/users/me/*` endpoints
   - User schema with subscription type

4. **AWS Infrastructure:**
   - Premium instances with standby pool (stopped instances) ✅ **ACTUAL**
   - Premium manager Lambda (single function) ✅ **ACTUAL**
   - RDS tables for assignment tracking
   - EFS for temporary workspaces

### **❌ REMOVED/OBSOLETE COMPONENTS**

**Migration System (Replaced with Immediate Assignment):**
- ❌ `usePremiumAssignment` hook → Replaced with Context API
- ❌ Migration queue Lambda → Not needed with standby pool
- ❌ Spot fleet scaling → Uses regular EC2 with standby pool
- ❌ State migration flow → Immediate assignment instead

### **⚡ STANDBY POOL ADVANTAGES**

**Cost Optimization:**
- 85% cost reduction (stopped instances = EBS storage only)
- Fast startup: 5-15 seconds vs 60-90 seconds for new instances
- Pre-warmed instances ready for immediate use

**Performance Benefits:**
- No 6-minute provisioning wait
- Immediate assignment or 15-second startup
- Graceful sharing during high demand
- Automatic replacement of used standby instances

## Updated User Flow

### Premium Users - STANDBY POOL FLOW

1. **On Login (Immediate Assignment):**
   - User authentication loads subscription information
   - `PremiumAssignmentContext` detects premium user
   - **PRIORITY 1**: Use available running instance (immediate)
   - **PRIORITY 2**: Start standby instance (15 seconds)
   - **PRIORITY 3**: Share existing instance (temporary)
   - **PRIORITY 4**: Scale up new instances (60+ seconds)

2. **During Session (Normal Operation):**
   - All HTTP requests include premium routing headers
   - **Dedicated Mode**: `X-User-Tier: premium` + `X-User-ID: {user_id}` → Dedicated instance
   - **Fallback**: If dedicated fails → Remove headers → Route to free tier

3. **On Logout:**
   - Automatic call to `/users/me/premium/assign` (DELETE)
   - Premium manager Lambda releases user from dedicated instance
   - Instance converted to standby or terminated based on pool needs
   - ALB routing rules cleaned up

## Configuration

### Environment Variables

Backend requires:
- `RDS_HOST`, `RDS_USER`, `RDS_PASSWORD`, `RDS_DATABASE` - Database connection ✅ **SAFE ACCESS**
- `PREMIUM_LAUNCH_TEMPLATE_ID` - EC2 launch template ✅ **SAFE ACCESS**
- `VPC_ID`, `ALB_LISTENER_ARN` - Network configuration ✅ **SAFE ACCESS**
- `CLUSTER_NAME` - ECS cluster name ✅ **SAFE ACCESS**
- `PREMIUM_EXTRA_CAPACITY` (default: 2) - Extra instances beyond subscribers
- `PREMIUM_STANDBY_POOL_SIZE` (default: 1) - Number of stopped instances maintained
- `ABSOLUTE_MAX` (default: 20) - Hard limit to prevent runaway scaling

### Database Schema

Enhanced `premium_user_assignments` table:
- `is_standby` (BOOLEAN) - Marks standby instances ✅ **IMPLEMENTED**
- `standby_created_at` (TIMESTAMP) - Standby creation time ✅ **IMPLEMENTED**
- `instance_state` (ENUM) - Tracks AWS instance state ✅ **IMPLEMENTED**
  - Values: 'launching', 'running', 'stopping', 'stopped', 'terminating'
- `is_shared` (BOOLEAN) - Tracks shared instances ✅ **IMPLEMENTED**

## Performance Impact

- **Immediate Assignment**: ~15 seconds from standby pool
- **Cost Savings**: 85% reduction vs running instances
- **Fallback Performance**: Graceful degradation to free tier
- **Scalability**: Dynamic capacity based on subscriber count

## Monitoring

### Logs to Monitor

**Backend Logs:**
```
🚀 === PREMIUM USER ASSIGNMENT START ===
📊 Assignment context: standby_available=2, running=3, active_users=1
✅ Starting standby instance i-1234567890abcdef0
Creating replacement standby instance
✅ === ASSIGNMENT SUCCESS ===
```

**Frontend Console:**
```
Premium instance assigned successfully!
Premium routing active with headers: X-User-Tier=premium, X-User-ID=123
Premium routing failed, falling back to free tier routing
```

## Security & Safety Features

### Environment Variable Safety ✅ **NEW**
- All environment variables use safe access with `get_required_env_var()`
- Missing configuration provides helpful error messages
- No more `KeyError` crashes in production
- Graceful degradation on configuration issues

### Database Transaction Safety ✅ **VERIFIED**
- All operations use `FOR UPDATE` locks to prevent race conditions
- Atomic transactions for assignment/release operations
- Safe concurrent user assignment handling

### Duplicate Function Cleanup ✅ **COMPLETED**
- Removed duplicate maintenance functions from premium_manager
- Preserved critical immediate cost optimization
- Eliminated ~180 lines of redundant code
- Single-responsibility principle maintained

## Testing

Test files available in `/studio/scripts/`:
- `test_safe_environment_variables.py` - Environment variable safety ✅ **NEW**
- `test_database_schema.py` - Database schema validation
- `test_premium_api_integration.py` - API endpoint testing
- `test_lambda_integration.py` - Lambda function testing

## Future Enhancements

1. **Advanced Standby Management:**
   - Predictive scaling based on usage patterns
   - Regional standby distribution
   - Health monitoring automation

2. **Cost Optimization:**
   - Per-user cost tracking
   - Budget alerts and spending limits
   - Instance type optimization

3. **User Experience:**
   - Real-time assignment status
   - Premium usage analytics
   - Custom instance preferences
