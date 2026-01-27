# ALB Security Enhancement: Non-Reversible Routing IDs

## Executive Summary

This document describes the security enhancement for ALB (Application Load Balancer) routing to fix a critical vulnerability where client-controlled HTTP headers expose user IDs and can be spoofed to access other users' premium instances.

**Security Fix:**
- **Before:** Client-controlled headers (`X-User-ID`, `X-User-Tier`) expose Firebase UIDs and can be spoofed
- **After:** Backend-issued non-reversible routing IDs derived from HMAC-SHA256

**Key Benefits:**
- Prevents UID exposure to clients (privacy compliance)
- Prevents header spoofing (cannot forge HMAC without SECRET_KEY)
- Prevents unauthorized premium instance access
- Immediate tier change responsiveness (webhook-triggered cache invalidation)

---

## Security Vulnerability (Current State)

### Problem

The current ALB routing system allows malicious users to:

1. **See UIDs in network traffic** - Firebase UIDs exposed in `X-User-ID` header
2. **Modify headers via DevTools** - Client sets headers using JavaScript
3. **Impersonate premium users** - Change `X-User-ID` to access other instances
4. **Bypass authentication** - ALB routes based on unvalidated headers

**Attack Vector:**
```
1. Malicious user opens browser DevTools
2. Modifies X-User-ID header to another user's UID
3. Modifies X-User-Tier header to "premium"
4. Sends request → ALB routes to premium instance
5. Gains unauthorized access to premium resources
```

---

## Solution: Non-Reversible Routing IDs

### Core Concept

Replace raw UID exposure with cryptographically secure routing identifiers:

```
routing_id = HMAC-SHA256(uid, SECRET_KEY).hex()[:16]
```

**Security Properties:**
- **Non-reversible:** Cannot extract UID from routing_id (one-way hash)
- **Deterministic:** Same UID always produces same routing_id (consistent routing)
- **Backend-verifiable:** Backend regenerates from JWT to validate headers
- **Client-agnostic:** Client stores opaque identifier without UID knowledge

### Before vs After Comparison

| Aspect | Before (Vulnerable) | After (Secure) |
|--------|---------------------|----------------|
| **UID Visibility** | Exposed in `X-User-ID` | Never exposed (HMAC hash) |
| **Header Control** | Client-controlled | Backend-issued, client-cached |
| **Spoofing Risk** | High (client sets headers) | None (can't forge HMAC) |
| **Privacy** | UID visible in network traffic | Opaque 16-char hex string |
| **Validation** | None | Backend regenerates & validates |
| **Complexity** | Simple (but insecure) | Simple (and secure) |

---

## Architecture Flow

### Before: Vulnerable Routing

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Client JavaScript sets headers (VULNERABLE)              │
│    X-User-ID: "firebase_uid_abc123"                         │
│    X-User-Tier: "premium"                                   │
└─────────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. ALB evaluates listener rules (NO VALIDATION)             │
│    IF X-User-Tier == 'premium' AND X-User-ID matches        │
│    THEN forward to premium target group                     │
└─────────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. Backend processes request                                │
│    No header validation                                     │
│    Client can impersonate any user                          │
└─────────────────────────────────────────────────────────────┘
```

### After: Secure Routing with Non-Reversible IDs

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Initial Request (JWT only)                               │
│    Authorization: Bearer <firebase_jwt>                     │
└─────────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. Backend validates JWT → Extracts UID → Queries tier      │
│    routing_id = HMAC-SHA256(uid, SECRET)[:16]               │
│    Response headers:                                        │
│      X-Routing-ID: a3f2e8b9c1d4e567                         │
│      X-User-Tier: premium                                   │
└─────────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. Client caches routing headers (localStorage)             │
│    Client stores opaque routing_id (no UID knowledge)       │
└─────────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. Subsequent Requests                                      │
│    Authorization: Bearer <firebase_jwt>                     │
│    X-Routing-ID: a3f2e8b9c1d4e567                           │
│    X-User-Tier: premium                                     │
└─────────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 5. ALB evaluates listener rules                             │
│    IF X-User-Tier == 'premium' AND X-Routing-ID matches     │
│    THEN forward to premium target group                     │
└─────────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 6. Backend validates routing_id                             │
│    Extract UID from JWT                                     │
│    Regenerate routing_id from UID                           │
│    Compare with header → Log mismatch if different          │
└─────────────────────────────────────────────────────────────┘
```

---

## Attack Scenario Analysis

### Attack 1: Client Modifies Routing ID

**Attempt:** Malicious user changes `X-Routing-ID` to access another instance

**Defense:**
1. Backend extracts UID from JWT
2. Regenerates routing_id from UID
3. Detects mismatch with header
4. Logs security event
5. ALB won't match anyway (wrong routing_id for their JWT)

**Result:** Attack fails

---

### Attack 2: Client Sniffs Another User's Routing ID

**Attempt:** Client captures another premium user's routing_id from network

**Defense:**
1. Attacker's JWT has different UID
2. Backend validates: routing_id doesn't match JWT UID
3. ALB routes to wrong instance (no data access due to JWT mismatch)
4. Security event logged

**Result:** Attack fails

---

### Attack 3: Subscription Downgrade Delay

**Attempt:** User downgrades but continues using premium routing_id

**Defense:**
1. Stripe webhook immediately invalidates tier cache
2. Next request: fresh tier query → tier='free'
3. Response headers exclude routing_id
4. Client cache updated (routing_id removed)
5. Immediate routing change (no delay)

**Result:** Attack fails

---

## Implementation Overview

### Phase 1: Backend Middleware

**File:** `studio/app/common/core/middleware/secure_routing_middleware.py`

**Changes:**
- Add `generate_routing_id()` function using HMAC-SHA256
- Update response headers to include routing_id for premium users
- Add validation for incoming routing_id headers
- Export `invalidate_user_tier_cache()` for webhooks

**Example:**
```python
def generate_routing_id(uid: str, secret_key: str) -> str:
    """Generate non-reversible routing ID from UID"""
    signature = hmac.new(
        secret_key.encode('utf-8'),
        uid.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature[:16]  # 16 hex chars = 64 bits
```

---

### Phase 2: Premium Manager Lambda

**File:** `infrastructure/terraform/premium_manager_package/premium_manager.py`

**Changes:**
- Add routing ID generation (same algorithm as middleware)
- Update ALB rule creation to use `X-Routing-ID` instead of `X-User-ID`
- Store routing_id in database (optional)

**ALB Rule Update:**
```python
Conditions=[
    {
        "Field": "http-header",
        "HttpHeaderConfig": {
            "HttpHeaderName": "X-User-Tier",
            "Values": ["premium"],
        },
    },
    {
        "Field": "http-header",
        "HttpHeaderConfig": {
            "HttpHeaderName": "X-Routing-ID",  # Changed from X-User-ID
            "Values": [routing_id],            # Changed from user_id
        },
    },
]
```

---

### Phase 3: Subscription Webhooks

**File:** `studio/app/common/core/subscription/webhook_service.py`

**Changes:**
- Import `invalidate_user_tier_cache` from middleware
- Add cache invalidation to subscription change handlers:
  - `handle_checkout_completed` (upgrade to premium)
  - `handle_subscription_deleted` (downgrade to free)
  - `handle_subscription_schedule_released` (plan change)

**Example:**
```python
def handle_subscription_deleted(subscription_data: Dict, db: Session):
    # ... existing logic ...

    # Invalidate cache for immediate routing update
    invalidate_user_tier_cache(user.uid)
    logger.info(f"Invalidated tier cache after subscription cancellation")
```

---

### Phase 4: Frontend Updates

**File:** `frontend/src/utils/routing/RoutingService.ts`

**Changes:**
- Change header name: `X-Routing-Token` → `X-Routing-ID`
- Update storage key for clarity
- No other logic changes needed (already supports backend-issued tokens)

**File:** `frontend/src/utils/axios.ts`

**Changes:**
- Update response interceptor to capture both headers:
  - `x-routing-id` (opaque identifier)
  - `x-user-tier` (premium/free)
- Handle tier changes (clear routing_id on downgrade)

---

### Phase 5: Infrastructure Configuration

**Files:** `infrastructure/terraform/premium_manager.tf`, `infrastructure/terraform/main.tf`

**Changes:**
- Add `ROUTING_SECRET_KEY` environment variable to Lambda
- Add variable definition in Terraform (sensitive)
- Generate secure 256-bit random key:
  ```bash
  openssl rand -hex 32
  ```
- Store in AWS Secrets Manager (recommended for production)

---

## Security Guarantees

### 1. UID Privacy

**Guarantee:** Client never sees UID

**Implementation:**
- Routing ID is cryptographic one-way hash
- No reverse-engineering possible without SECRET_KEY
- Network sniffing reveals only opaque identifier

---

### 2. Header Spoofing Prevention

**Guarantee:** Client cannot forge routing_id

**Implementation:**
- Client lacks SECRET_KEY (stored in backend only)
- Backend validates routing_id matches JWT UID
- Mismatch detection and logging

---

### 3. Immediate Tier Changes

**Guarantee:** Subscription changes reflect immediately

**Implementation:**
- Webhook invalidates cache on subscription change
- No TTL delay (previous: 5 minutes)
- Next request fetches fresh tier from database

---

### 4. JWT as Source of Truth

**Guarantee:** Authentication always validated

**Implementation:**
- JWT validation happens first (existing middleware)
- Routing headers are supplemental (routing only)
- Authentication and authorization remain JWT-based

---

## Performance & Monitoring

### Expected Performance

**HMAC Generation:**
- Algorithm: SHA256 (fastest secure hash)
- Latency: <0.5ms per request
- No caching needed (fast enough for every request)

**Database Queries:**
- Without cache: 1 query per request - Too high
- With cache: 1 query per user every 5 minutes - Acceptable
- With invalidation: Queries only on cache miss or tier change - Optimal

**Memory Footprint:**
```
Per user in cache:
- UID: ~28 bytes (Firebase UID)
- Tier: ~8 bytes (string 'premium' or 'free')
- Timestamp: 8 bytes (float)
Total: ~50 bytes per user

10,000 users: ~500 KB (negligible)
```

---

### Key Metrics

**Monitoring:**
1. `routing_id_generation_time_ms` - HMAC generation latency
   - Alert if >10ms (should be <1ms)

2. `routing_id_mismatch_count` - Validation failures
   - Alert if >10/hour (potential attack or client bug)

3. `tier_cache_hit_rate` - Cache effectiveness
   - Target: >90%

4. `tier_cache_invalidation_count` - Subscription changes
   - Track against Stripe webhook volume

---

## Files Modified Summary

### Backend
1. **`studio/app/common/core/middleware/secure_routing_middleware.py`**
   - Add routing ID generation and validation
   - Export cache invalidation function

2. **`infrastructure/terraform/premium_manager_package/premium_manager.py`**
   - Add routing ID generation
   - Update ALB rule creation

3. **`studio/app/common/core/subscription/webhook_service.py`**
   - Add cache invalidation to subscription webhooks

### Frontend
4. **`frontend/src/utils/routing/RoutingService.ts`**
   - Update header name (`X-Routing-ID`)

5. **`frontend/src/utils/axios.ts`**
   - Update response interceptor to capture routing headers

### Infrastructure
6. **`infrastructure/terraform/premium_manager.tf`**
   - Add `ROUTING_SECRET_KEY` environment variable

7. **`infrastructure/terraform/main.tf`**
   - Add `routing_secret_key` variable (sensitive)

---

## Success Criteria

- **No UID exposure** - Client never sees Firebase UID in any header or response
- **No header spoofing** - Routing ID cannot be forged without SECRET_KEY
- **Immediate cache invalidation** - Subscription changes reflect in <1 second
- **Backend validation** - Routing ID mismatch detected and logged
- **Performance** - HMAC generation <1ms, cache hit rate >90%
- **ALB routing** - Premium users correctly routed to dedicated instances
- **Clean architecture** - No Lambda@Edge, no complex infrastructure
- **JWT-centric** - Reuses existing Firebase JWT validation

---

## Testing

### Unit Tests

**File:** `studio/tests/app/common/core/middleware/test_secure_routing_middleware.py`

**Coverage:**
- Routing ID generation (determinism, uniqueness)
- Routing ID validation (match/mismatch detection)
- Cache invalidation (tier changes trigger cache clear)
- Header injection (correct headers in response)
- Premium vs. free tier behavior

---

### Integration Tests

**File:** `infrastructure/scripts/test_alb_routing_security.py`

**Scenarios:**
1. Valid JWT flow (login → routing_id → premium routing)
2. Tier change flow (upgrade: free → premium)
3. Downgrade flow (downgrade: premium → free)
4. Routing ID mismatch (invalid routing_id detection)
5. Missing routing ID (default routing to free tier)

---

### Manual Testing

```bash
# 1. Get Firebase JWT
./infrastructure/scripts/get_jwt_tokens.py

# 2. Test login and routing ID issuance
curl -H "Authorization: Bearer <firebase_jwt>" \
     https://your-alb-url.amazonaws.com/api/auth/login

# Expected: Response headers include x-routing-id and x-user-tier

# 3. Test subsequent request with routing ID
curl -H "Authorization: Bearer <firebase_jwt>" \
     -H "X-Routing-ID: <routing_id_from_login>" \
     -H "X-User-Tier: premium" \
     https://your-alb-url.amazonaws.com/api/workflows

# Expected: ALB routes to premium instance

# 4. Test invalid routing ID (security test)
curl -H "Authorization: Bearer <firebase_jwt>" \
     -H "X-Routing-ID: invalid123456789" \
     -H "X-User-Tier: premium" \
     https://your-alb-url.amazonaws.com/api/workflows

# Expected: Backend logs mismatch, request still processed (graceful degradation)
```

---

## References

- [HMAC-SHA256 Specification (RFC 2104)](https://datatracker.ietf.org/doc/html/rfc2104)
- [Firebase JWT Verification](https://firebase.google.com/docs/auth/admin/verify-id-tokens)
- [AWS ALB Listener Rules Documentation](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/listener-update-rules.html)
- Detailed implementation plan: `infrastructure/plan/SECURITY_UPDATE_ALB.md`
- Current branch: `feature/security-update-alb`

---
