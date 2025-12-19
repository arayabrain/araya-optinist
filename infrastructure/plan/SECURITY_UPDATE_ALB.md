# Security Update: ALB Routing Authentication

## Executive Summary

This document outlines the security update for ALB (Application Load Balancer) routing to fix a critical vulnerability where client-controlled HTTP headers can be spoofed to access other users' premium instances.

**Current Vulnerability**: The ALB routing system uses client-controlled headers (`X-User-Tier` and `X-User-ID`) set by frontend JavaScript. Malicious users can modify these headers via browser DevTools or HTTP proxy to impersonate other premium users.

**Selected Solution**: HMAC Signed Routing Headers (Option 2)

---

## Option Evaluation

### Option 1: AWS ALB Native JWT Verification

#### Overview
Use AWS's new ALB JWT verification feature (released November 2025) to validate Firebase ID tokens at the infrastructure layer.

How It Works

Client Request: Authorization: Bearer <firebase_token>
↓
ALB Pre-Routing Action: Verify JWT
├─ Fetch Firebase public keys
├─ Verify signature cryptographically
├─ Validate expiration
└─ Extract claims (uid, email, etc.)
↓
ALB Listener Rule: Match JWT.uid claim
├─ If uid == "premium_user_123" → Route to dedicated instance
└─ Else → Route to free tier pool
↓
Backend receives validated request

Security Benefits

✅ Cryptographically secure: JWT signature verified with Firebase's public keys
✅ Cannot be forged: Client cannot create valid JWTs without Firebase private key
✅ No client-controlled routing: Routing based on verified token claims, not arbitrary headers
✅ Defense in depth: Both ALB and backend validate tokens
✅ Industry standard: OAuth 2.0 / OIDC best practices

Trade-offs

Pros:
- Strongest security - eliminates header spoofing completely
- AWS-native feature (no custom crypto code to maintain)
- Offloads verification from backend to ALB (better performance)
- Standards-compliant
- Clean long-term architecture

Cons:
- NEW feature (Nov 2025) - limited production testing, may have edge cases
- Not available through terraform at this time (new feature as of November 2025)
- Requires Premium Manager Lambda refactoring (change rule creation from http-header to jwt-claim conditions)
- Tight coupling to Firebase (can't easily switch auth providers)
- Requires public key rotation handling

#### Status: NOT AVAILABLE IN TERRAFORM

**Research Findings** (December 2025):
- AWS released native ALB JWT verification feature in November 2025
- Feature is available in AWS Console and CLI
- **Terraform AWS provider does NOT currently support this feature**
- A feature request is open on GitHub for Terraform support
- No dedicated resource type (`type = "jwt-validation"`) available in `aws_lb_listener_rule`

#### Why We're Not Implementing This Now

1. **Terraform Incompatibility**: Our infrastructure is fully managed by Terraform. Using AWS CLI workarounds (local-exec provisioner) would:
   - Break Terraform's declarative model
   - Create state management conflicts
   - Make rollbacks and updates difficult
   - Introduce local environment dependencies

2. **Management Conflicts**: Mixing Terraform and manual AWS CLI configuration creates:
   - State drift issues
   - Difficult troubleshooting
   - Inconsistent infrastructure as code
   - Risk during terraform apply/destroy operations

3. **Not Recommended**: To avoid management conflicts and maintain clean infrastructure code, we should not proceed with this option at this time.

#### Future Consideration

This option can be **reconsidered when**:
- Terraform AWS provider adds native support for ALB JWT verification
- Provider version includes `authenticate-jwt` action type
- Terraform documentation is updated with examples

**Action**: Monitor the GitHub issue and AWS provider release notes. When support becomes available, Option 1 would provide the cleanest long-term architecture.

---

### Option 2: HMAC Signed Routing Headers (SELECTED)

#### Overview
Backend validates JWT and issues cryptographically signed routing tokens. Backend middleware verifies HMAC signatures and controls all routing decisions. Clients cannot forge tokens without the backend secret key.

**Note**: This implementation uses backend-only verification without Lambda@Edge. See "Why No Lambda@Edge?" section below for architectural rationale.

#### Why This Option

1. **Fully Terraform-Managed**: All infrastructure as code with proper state tracking
2. **Proven Security**: HMAC-SHA256 is cryptographically secure and well-tested
3. **Simple Architecture**: Backend-only verification, no edge functions required
4. **No Management Conflicts**: Pure Terraform resources, no CLI workarounds
5. **Strong Security**: Prevents header spoofing completely
6. **Easier Operations**: Single deployment surface (backend containers only)

#### Security Benefits

✅ **Cryptographically Secure**: HMAC-SHA256 prevents forgery without secret key
✅ **Tamper-Proof**: Any header modification invalidates the signature
✅ **Backend-Controlled**: Only backend can issue valid routing tokens
✅ **Timestamp Validation**: Prevents replay attacks (5-minute token expiration)
✅ **No Client Spoofing**: Client cannot generate valid signatures without secret

---

## Implementation Plan: HMAC Signed Routing Headers

### Architecture Flow

```
1. Client Request → ALB → Backend
   Authorization: Bearer <firebase_token>

2. Backend Middleware (SecureRoutingMiddleware)
   ├─ Validates Firebase JWT (existing auth)
   ├─ Extracts UID from validated token
   ├─ Gets subscription tier from user object
   └─ Generates HMAC-signed routing token

3. HMAC Token Generation
   message = "uid|tier|timestamp"
   signature = HMAC-SHA256(message, secret_key)
   token = base64(message|signature)

4. Backend Response
   X-Routing-Token: <signed_token>
   (Token included in every response header)

5. Subsequent Client Requests
   X-Routing-Token: <signed_token>
   (Frontend includes token in request headers)

6. Backend Validation (Implicit)
   ├─ Token provides cryptographic proof of authorization
   ├─ Backend serves requests only for authenticated users
   ├─ Invalid/missing tokens → authentication fails
   └─ Security enforced at application level

7. ALB Listener Rules (Functional Routing)
   ├─ Premium Manager Lambda maintains routing rules
   ├─ Routes to dedicated instances for performance
   ├─ Backend validates all authorization decisions
   └─ ALB routing is optimization, not security
```

**Security Architecture**:
- **Backend is the sole authority**: Only valid HMAC tokens grant access
- **ALB provides routing optimization**: Directs premium users to dedicated instances
- **Defense in depth**: Even if ALB mis-routes, backend rejects unauthorized requests
- **No reliance on client headers**: Client cannot forge cryptographic signatures

### Implementation Phases

#### Phase 1: Secret Management (Terraform)

**Tasks**:
1. Create AWS Secrets Manager secret for HMAC key
   - Secret name: `subscr-premium-routing-hmac-key`
   - Generate secure 256-bit random key (64 characters)
   - Store in Secrets Manager

2. Update IAM roles for secret access
   - Grant ECS Task Role read access to secret
   - Update `/infrastructure/terraform/security.tf`

**Files to Modify**:
- `infrastructure/terraform/security.tf`

**Terraform Resources**:
```hcl
# HMAC secret for routing token generation
resource "random_password" "routing_hmac_key" {
  length  = 64
  special = true
}

resource "aws_secretsmanager_secret" "routing_hmac_key" {
  name        = "subscr-premium-routing-hmac-key"
  description = "HMAC secret key for premium routing token verification"
}

resource "aws_secretsmanager_secret_version" "routing_hmac_key" {
  secret_id = aws_secretsmanager_secret.routing_hmac_key.id
  secret_string = jsonencode({
    key = random_password.routing_hmac_key.result
  })
}

# IAM policy for ECS Task Role to access HMAC secret
resource "aws_iam_role_policy" "ecs_task_routing_secret" {
  name = "subscr-ecs-task-routing-secret"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = [aws_secretsmanager_secret.routing_hmac_key.arn]
      }
    ]
  })
}
```

---

#### Phase 2: Backend Middleware (Python)

**Tasks**:
1. Create secure routing middleware
   - File: `/studio/app/common/core/middleware/secure_routing_middleware.py`
   - Extract authenticated user from request state (set by FastAPI auth dependencies)
   - Generate HMAC-SHA256 signed token for authenticated users
   - Set `X-Routing-Token` response header on all authenticated responses
   - Cache HMAC secret key from AWS Secrets Manager (5-minute TTL)

2. Register middleware in FastAPI application
   - Update `/studio/__main_unit__.py` to import and register middleware
   - Update `/studio/app/common/core/middleware/__init__.py` to export middleware
   - Add after `FreeUserActivityMiddleware`

**Files to Modify**:
- `studio/app/common/core/middleware/secure_routing_middleware.py` (CREATE)
- `studio/app/common/core/middleware/__init__.py`
- `studio/__main_unit__.py`

**Implementation Details**:
```python
# secure_routing_middleware.py
import hmac
import hashlib
import base64
from datetime import datetime
import boto3
from functools import lru_cache

class SecureRoutingMiddleware:
    """Generates HMAC-signed routing tokens for verified users"""

    def __init__(self, app):
        self.app = app
        self._secret_key = None

    @property
    def secret_key(self):
        """Lazy load secret from AWS Secrets Manager with caching"""
        if self._secret_key is None:
            self._secret_key = self._fetch_secret()
        return self._secret_key

    def _fetch_secret(self):
        """Fetch HMAC secret from Secrets Manager"""
        # Implementation with boto3

    def generate_routing_token(self, uid: str, tier: str) -> str:
        """Generate HMAC-signed routing token"""
        timestamp = int(datetime.utcnow().timestamp())
        message = f"{uid}|{tier}|{timestamp}"
        signature = hmac.new(
            self.secret_key.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()
        token = base64.b64encode(f"{message}|{signature}".encode()).decode()
        return token

    async def __call__(self, scope, receive, send):
        # Extract UID from validated JWT
        # Query subscription tier (with cache)
        # Generate token
        # Set response header
```

**Caching Strategy**:
- Secret key: Cached in memory (lazy load, refresh on error)
- Subscription tier: Retrieved from authenticated user object (already cached by auth system)
- Minimizes AWS API calls and database queries

---

### Why No Lambda@Edge?

**Initial Consideration**: The original plan included Lambda@Edge to verify HMAC tokens at the edge before requests reach the backend.

**Architectural Limitation**: Lambda@Edge **cannot be directly attached to Application Load Balancers**. Lambda@Edge is designed to work with CloudFront distributions, which would require:

```
Client → CloudFront → Lambda@Edge → ALB → Backend
```

**Problems with This Approach**:
1. **Added Complexity**: Introduces CloudFront as an additional service layer
2. **Cost Increase**: CloudFront + Lambda@Edge pricing on top of ALB costs
3. **Operational Overhead**: Two routing layers (CloudFront + ALB) to manage
4. **Deployment Complexity**: Lambda@Edge must be deployed to us-east-1 regardless of application region
5. **Cold Start Latency**: Edge functions can add 1-2 seconds on first request
6. **Secret Management**: Requires coordinating secret access between Lambda@Edge and backend

**Simplified Architecture Decision**:
Move token verification to the backend middleware, eliminating the need for CloudFront and Lambda@Edge entirely.

**Why This Works**:
1. **Security Equivalence**: HMAC token verification provides the same security whether done at the edge or backend
2. **Backend as Gatekeeper**: Even if ALB mis-routes a request, backend validates tokens before serving data
3. **Simpler Deployment**: Single deployment surface (backend containers)
4. **Lower Cost**: No CloudFront or Lambda@Edge charges
5. **Easier Testing**: Standard backend testing, no edge function simulation needed
6. **Better Observability**: All security logic in one place

**Security Model**:
- **Client cannot forge tokens**: Only backend has the secret key
- **Tampering detected**: HMAC signature verification catches any modifications
- **Replay protection**: Timestamp validation (5-minute window)
- **Defense in depth**: Backend is the ultimate authority, not ALB routing

**Functional Routing Still Works**:
- Premium Manager Lambda continues creating ALB listener rules
- ALB routes premium users to dedicated instances (performance optimization)
- If client spoofs headers and gets mis-routed, backend rejects invalid tokens
- ALB routing becomes a performance feature, not a security boundary

---

#### Phase 3: Frontend Updates (TypeScript)

**Tasks**:
1. Update RoutingService to handle backend-issued tokens
   - File: `/frontend/src/utils/routing/RoutingService.ts`
   - Remove client-controlled header setting logic (`X-User-Tier`, `X-User-ID`)
   - Add `routingToken` storage with localStorage persistence
   - Add `updateRoutingToken()` method to receive backend tokens
   - Modify `getRoutingHeaders()` to return `X-Routing-Token` instead

2. Update axios interceptors
   - File: `/frontend/src/utils/axios.ts`
   - Add response interceptor to capture `X-Routing-Token` from backend
   - Update 503 error handling to use new token-based routing
   - Request interceptor already calls `getRoutingHeaders()` (no changes needed)

**Files to Modify**:
- `frontend/src/utils/routing/RoutingService.ts`
- `frontend/src/utils/axios.ts`

**Implementation Details**:
```typescript
// RoutingService.ts
export class RoutingService {
  private routingToken: string | null = null;

  // Called when receiving backend response
  updateRoutingToken(token: string) {
    this.routingToken = token;
    localStorage.setItem('routing_token', token);
  }

  getRoutingToken(): string | null {
    if (!this.routingToken) {
      this.routingToken = localStorage.getItem('routing_token');
    }
    return this.routingToken;
  }

  clearRoutingToken() {
    this.routingToken = null;
    localStorage.removeItem('routing_token');
  }
}

// axios.ts - Request interceptor
axios.interceptors.request.use((config) => {
  // Add Authorization header (existing)
  const token = getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }

  // Add routing token if available
  const routingToken = routingService.getRoutingToken();
  if (routingToken) {
    config.headers['X-Routing-Token'] = routingToken;
  }

  return config;
});

// Response interceptor - capture routing token
axios.interceptors.response.use((response) => {
  const routingToken = response.headers['x-routing-token'];
  if (routingToken) {
    routingService.updateRoutingToken(routingToken);
  }
  return response;
});
```

---

#### Phase 4: Testing & Validation

**Test Scenarios**:

1. **Valid Token Flow**
   - User logs in → Receives routing token
   - Subsequent requests include token
   - ALB routes correctly to premium instance

2. **Expired Token**
   - Wait 6 minutes (past 5-minute TTL)
   - Send request with old token
   - Expect: Token rejected, new token issued

3. **Tampered Signature**
   - Modify token signature
   - Send request
   - Expect: 403 Forbidden

4. **Modified UID in Token**
   - Extract token, change UID
   - Re-encode and send
   - Expect: Signature verification fails, 403 Forbidden

5. **Replay Attack**
   - Capture valid token
   - Use same token after expiration
   - Expect: Timestamp validation fails, 403 Forbidden

6. **Missing Token**
   - Send request without routing token
   - Expect: Rejected or routed to default (free tier)

7. **Token Refresh**
   - Backend issues new token periodically
   - Frontend updates stored token
   - Verify seamless transitions

**Monitoring**:
- CloudWatch metrics for Lambda invocations
- Failed signature verification count
- Token expiration rate
- ALB routing decisions

---

## Security Considerations

### Threat Model

**Mitigated Threats**:
- ✅ Header spoofing (primary vulnerability)
- ✅ User impersonation
- ✅ Unauthorized access to premium instances
- ✅ Replay attacks (timestamp validation)
- ✅ Token tampering (HMAC signature)

**Remaining Considerations**:
- Secret key rotation strategy
- Token revocation mechanism
- Rate limiting on token generation
- Monitoring for brute-force attempts

### Secret Management

**HMAC Secret Key**:
- Stored in AWS Secrets Manager
- 256-bit random key
- Accessed via IAM roles (no credentials in code)
- Cached in Lambda/backend (5-minute TTL)
- Rotation plan: Manual rotation with gradual rollover

**Rotation Strategy**:
1. Generate new secret (secret-v2)
2. Backend accepts both old and new signatures (grace period)
3. Issue new tokens with new secret
4. After grace period (24 hours), remove old secret
5. Update Secrets Manager to use new secret exclusively

---

## Performance Impact

**Expected Overhead**:
- HMAC generation (backend): < 1ms per request
- Token verification (Lambda): 1-2ms per request
- Secrets Manager API calls: Cached (negligible after first call)
- Total latency increase: **2-3ms per request**

**Optimization**:
- Cache subscription tier queries (5-minute TTL)
- Cache secret key in memory
- Use in-process caching for frequent lookups

---

## Cost Analysis

**New AWS Resources**:
- AWS Secrets Manager: $0.40/month per secret + $0.05 per 10,000 API calls
- Minimal cost increase: **< $5/month for typical traffic**

**No Additional Costs**:
- If verification done in backend middleware only
- Uses existing ECS tasks and Secrets Manager

---

## Files Modified Summary

### Infrastructure (Terraform)
- `infrastructure/terraform/security.tf` - Secrets Manager, IAM roles
- `infrastructure/terraform/lambda_edge.tf` - Lambda function (if using Lambda@Edge)
- `infrastructure/terraform/compute.tf` - ALB integration (if using Lambda@Edge)

### Backend (Python)
- `studio/app/common/core/middleware/secure_routing_middleware.py` (CREATE)
- `studio/__main_unit__.py` - Register middleware

### Frontend (TypeScript)
- `frontend/src/utils/routing/RoutingService.ts` - Token management
- `frontend/src/utils/axios.ts` - Interceptor updates

### Lambda (Python) - Optional
- `infrastructure/terraform/lambda_edge/routing_verifier.py` (CREATE) - If using Lambda verification

---

## Success Criteria

✅ No client-controlled routing headers
✅ All routing decisions based on verified backend-issued tokens
✅ HMAC signature verification passes 100% for valid tokens
✅ Invalid/expired tokens rejected with 403
✅ Performance impact < 5ms per request
✅ Zero security incidents related to header spoofing
✅ Clean Terraform state (no manual AWS CLI configuration)

---

## References

- [AWS Secrets Manager Documentation](https://docs.aws.amazon.com/secretsmanager/)
- [HMAC-SHA256 Specification](https://datatracker.ietf.org/doc/html/rfc2104)
- [Lambda@Edge Documentation](https://docs.aws.amazon.com/lambda/latest/dg/lambda-edge.html)
- Original analysis: `/Users/milesd/.claude/plans/ethereal-tumbling-reef.md`
