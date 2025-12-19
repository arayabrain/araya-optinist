 Executive Summary                                                                                                            
                                                                                                                                  
Current Security Issue: The ALB routing system uses client-controlled HTTP headers (X-User-Tier and X-User-ID) to route      
premium users to dedicated instances. These headers are set by frontend JavaScript and can be spoofed by malicious users     
to:                                                                                                                          
- Access other premium users' dedicated instances                                                                            
- Impersonate other users                                                                                                    
- Bypass routing controls                                                                                                    
                                                                                                                        
Root Cause: Headers are set in frontend/src/utils/routing/RoutingService.ts and can be modified via browser DevTools or      
HTTP proxy before reaching the ALB.                                                                                          
                                                                                                                        
Impact: Any authenticated user can route to any premium user's instance by modifying headers.                                
                                                                                                                        
---                                                                                                                          
Question: Would generate_client_id Work with ALB Routing?                                                                    
                                                                                                                        
Answer: Technically yes (the Premium Manager Lambda can create rules with hashed UIDs), but cryptographically no       
(provides zero security benefit).                                                                                            
                                                                                                                        
Explanation:                                                                                                                 
- generate_client_id creates a 16-character MD5 hash of the UID (see studio/app/common/core/logger.py:165-190)               
- The Lambda could create ALB rules matching hashed UIDs instead of raw UIDs                                                 
- However: Headers are still client-controlled, so users can simply:                                                         
a. Hash their target victim's UID (MD5 is fast to compute)                                                                 
b. Send the hashed value in the header                                                                                     
c. Access the victim's instance                                                                                            
                                                                                                                        
Verdict: This is security through obscurity - it looks more secure but doesn't prevent the attack. MD5 is deterministic      
and reversible via rainbow tables for known UIDs.                                                                            
                                                                                                                        
---                                                                                                                          
Recommended Options                                                                                                          
                                                                                                                        
OPTION 1: AWS ALB Native JWT Verification (Primary Recommendation)                                                           
                                                                                                                        
Overview                                                                                                                     
                                                                                                                        
Use AWS's new ALB JWT verification feature (released November 2025) to validate Firebase ID tokens at the infrastructure     
layer before routing decisions are made.                                                                                     
                                                                                                                        
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
- Terraform support uncertain - need to verify AWS provider has been updated                                                 
- Requires Premium Manager Lambda refactoring (change rule creation from http-header to jwt-claim conditions)                
- Tight coupling to Firebase (can't easily switch auth providers)                                                            
- Documentation may be sparse                                                                                                
- Requires public key rotation handling                                                                                      
                                                                                                                        
Implementation Plan                                                                                                          
                                                                                                                        
Phase 1: Research & Verification (1-2 days)                                                                                  
                                                                                                                        
1. Verify Terraform AWS provider supports ALB JWT verification                                                               
- Check provider version >= 5.76.0                                                                                         
- Review aws_lb_listener resource documentation                                                                            
- Test in non-production environment                                                                                       
2. Gather Firebase configuration details                                                                                     
- Public keys URL: https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com                
- JWT issuer: https://securetoken.google.com/{firebase_project_id}                                                         
- Required claims: uid, email_verified                                                                                     
                                                                                                                        
Phase 2: ALB Configuration                                                                                                   
                                                                                                                        
3. Update /infrastructure/terraform/compute.tf                                                                               
- Add JWT verification action to aws_lb_listener.autoscaling_https (line 50-63)                                            
- Configure Firebase as trusted JWT issuer                                                                                 
- Set claim validation rules                                                                                               
- Define error handling for invalid tokens                                                                                 
                                                                                                                        
resource "aws_lb_listener" "autoscaling_https" {                                                                             
load_balancer_arn = aws_lb.autoscaling.arn                                                                                 
port              = "443"                                                                                                  
protocol          = "HTTPS"                                                                                                
                                                                                                                        
# JWT verification action (NEW)                                                                                            
default_action {                                                                                                           
type = "authenticate-jwt"                                                                                                
                                                                                                                        
authenticate_jwt {                                                                                                       
issuer                       = "https://securetoken.google.com/${var.firebase_project_id}"                             
jwt_configuration {                                                                                                    
    jwks_uri = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"                
}                                                                                                                      
required_claims = {                                                                                                    
    email_verified = "true"                                                                                              
}                                                                                                                      
}                                                                                                                        
                                                                                                                        
on_unauthenticated_request = "deny"                                                                                      
}                                                                                                                          
}                                                                                                                            
                                                                                                                        
Phase 3: Premium Manager Lambda Refactoring                                                                                  
                                                                                                                        
4. Update /infrastructure/terraform/premium_manager_package/premium_manager.py                                               
- Modify create_rule() call (lines 2283-2303)                                                                              
- Change from http-header conditions to jwt-claim conditions                                                               
- Match on JWT uid claim instead of X-User-ID header                                                                       
                                                                                                                        
# BEFORE (lines 2286-2300)                                                                                                   
Conditions=[                                                                                                                 
{"Field": "http-header", "HttpHeaderConfig": {"HttpHeaderName": "X-User-Tier", "Values": ["premium"]}},                  
{"Field": "http-header", "HttpHeaderConfig": {"HttpHeaderName": "X-User-ID", "Values": [user_id]}}                       
]                                                                                                                            
                                                                                                                        
# AFTER                                                                                                                      
Conditions=[                                                                                                                 
{"Field": "jwt-claim", "JwtClaimConfig": {"ClaimName": "uid", "Values": [user_id]}}                                      
]                                                                                                                            
                                                                                                                        
Phase 4: Frontend Cleanup                                                                                                    
                                                                                                                        
5. Remove custom routing headers                                                                                             
- Update /frontend/src/utils/routing/RoutingService.ts - remove X-User-Tier and X-User-ID header logic                     
- Update /frontend/src/utils/axios.ts - rely solely on Authorization: Bearer header (already present)                      
- Remove routing header state management                                                                                   
6. Deprecate routing-info endpoint                                                                                           
- Update /studio/app/common/routers/users_me.py - return empty routing_headers (deprecated)                                
                                                                                                                        
Phase 5: Testing & Migration                                                                                                 
                                                                                                                        
7. Test JWT verification                                                                                                     
- Valid token routing                                                                                                      
- Expired token rejection                                                                                                  
- Invalid signature rejection                                                                                              
- Missing claims handling                                                                                                  
- Public key rotation                                                                                                      
8. Migration strategy                                                                                                        
- Week 1: Deploy with both JWT and header routing enabled (parallel)                                                       
- Week 2-3: Monitor for issues, validate JWT routing works                                                                 
- Week 4: Disable header-based routing                                                                                     
- Week 5: Remove old header code                                                                                           
                                                                                                                        
Critical Files                                                                                                               
                                                                                                                        
- /infrastructure/terraform/compute.tf - ALB listener JWT configuration                                                      
- /infrastructure/terraform/premium_manager_package/premium_manager.py:2283-2303 - Rule creation logic                       
- /frontend/src/utils/routing/RoutingService.ts - Remove header logic                                                        
- /frontend/src/utils/axios.ts - Simplify interceptor                                                                        
                                                                                                                        
Risk Assessment                                                                                                              
                                                                                                                        
Blockers:                                                                                                                    
- If Terraform doesn't support JWT verification yet → Use Option 4 instead                                                   
- If Firebase public keys change format → Update ALB configuration                                                           
- If AWS feature has bugs → Rollback to header-based routing temporarily                                                     
                                                                                                                        
Mitigation:                                                                                                                  
- Test in staging environment first                                                                                          
- Keep old header-based routing as fallback during migration                                                                 
- Monitor ALB metrics for JWT verification failures                                                                          
                                                                                                                        
---                                                                                                                          
OPTION 2: HMAC Signed Routing Headers (Fallback Recommendation)                                                              
                                                                                                                        
Overview                                                                                                                     
                                                                                                                        
Backend validates JWT and issues cryptographically signed routing tokens. Lambda@Edge verifies signatures before ALB         
routing. Client cannot forge tokens without backend secret key.                                                              
                                                                                                                        
How It Works                                                                                                                 
                                                                                                                        
Client Request: Authorization: Bearer <firebase_token>                                                                       
↓                                                                                                                        
Backend Middleware validates JWT                                                                                             
↓                                                                                                                        
Extract UID + tier from DB                                                                                                   
↓                                                                                                                        
Generate HMAC token: sign(uid|tier|timestamp, secret_key)                                                                    
↓                                                                                                                        
Response header: X-Routing-Token: base64(uid|tier|timestamp|hmac-sha256)                                                     
↓                                                                                                                        
Client includes X-Routing-Token in subsequent requests                                                                       
↓                                                                                                                        
ALB → Lambda@Edge verifies HMAC signature                                                                                    
├─ Valid → Extract UID, set X-User-ID for routing rules                                                                    
└─ Invalid → Reject (403 Forbidden)                                                                                        
↓                                                                                                                        
ALB routes based on verified X-User-ID header                                                                                
                                                                                                                        
Security Benefits                                                                                                            
                                                                                                                        
✅ Cryptographically secure: HMAC-SHA256 prevents forgery without secret key                                                 
✅ Tamper-proof: Any modification invalidates signature                                                                      
✅ Backend-controlled: Only backend can issue valid routing tokens                                                           
✅ Timestamp validation: Prevents replay attacks (5-minute expiration)                                                       
✅ No client spoofing: Client cannot generate valid signatures                                                               
                                                                                                                        
Trade-offs                                                                                                                   
                                                                                                                        
Pros:                                                                                                                        
- Strong security (HMAC-SHA256 is proven)                                                                                    
- Works with existing Premium Manager Lambda (minimal changes)                                                               
- More flexible than native JWT (custom claim logic)                                                                         
- Backend controls all authorization logic                                                                                   
- No dependency on new AWS features                                                                                          
                                                                                                                        
Cons:                                                                                                                        
- Requires Lambda@Edge (adds infrastructure complexity)                                                                      
- Secret management overhead (AWS Secrets Manager)                                                                           
- Adds 1-5ms latency per request (Lambda@Edge)                                                                               
- More moving parts to maintain                                                                                              
- Backend and Lambda@Edge must share secret securely                                                                         
- Secret rotation requires coordination                                                                                      
                                                                                                                        
Implementation Plan                                                                                                          
                                                                                                                        
Phase 1: Secret Management                                                                                                   
                                                                                                                        
1. Create shared HMAC secret                                                                                                 
- Generate 256-bit random key                                                                                              
- Store in AWS Secrets Manager: premium-routing-hmac-key                                                                   
- Grant access to backend IAM role and Lambda@Edge role                                                                    
- Update /infrastructure/terraform/security.tf                                                                             
                                                                                                                        
Phase 2: Backend Middleware                                                                                                  
                                                                                                                        
2. Create /studio/app/common/core/middleware/secure_routing_middleware.py                                                    
- Validate Firebase JWT (reuse auth_helper.py logic)                                                                       
- Query subscription tier from DB (with 5-minute cache)                                                                    
- Generate HMAC token:                                                                                                     
import hmac                                                                                                                
import hashlib                                                                                                               
import base64                                                                                                                
from datetime import datetime                                                                                                
                                                                                                                        
def generate_routing_token(uid: str, tier: str, secret_key: str) -> str:                                                     
timestamp = int(datetime.utcnow().timestamp())                                                                           
message = f"{uid}|{tier}|{timestamp}"                                                                                    
signature = hmac.new(secret_key.encode(), message.encode(), hashlib.sha256).hexdigest()                                  
token = base64.b64encode(f"{message}|{signature}".encode()).decode()                                                     
return token                                                                                                             
- Set response header: X-Routing-Token: {token}                                                                            
3. Register middleware in /studio/__main_unit__.py                                                                           
- Add after ClientIdLoggingMiddleware                                                                                      
                                                                                                                        
Phase 3: Lambda@Edge Verification                                                                                            
                                                                                                                        
4. Create /infrastructure/terraform/lambda_edge_routing_verifier.py                                                          
- Verify HMAC signature                                                                                                    
- Validate timestamp (reject if > 5 minutes old)                                                                           
- Extract UID and set X-User-ID header for ALB routing                                                                     
- Return 403 for invalid tokens                                                                                            
                                                                                                                        
import json                                                                                                                  
import hmac                                                                                                                  
import hashlib                                                                                                               
import base64                                                                                                                
from datetime import datetime                                                                                                
                                                                                                                        
def lambda_handler(event, context):                                                                                          
request = event['Records'][0]['cf']['request']                                                                           
headers = request['headers']                                                                                             
                                                                                                                        
# Extract routing token                                                                                                  
if 'x-routing-token' not in headers:                                                                                     
    return reject_request("Missing routing token")                                                                       
                                                                                                                        
token = headers['x-routing-token'][0]['value']                                                                           
                                                                                                                        
# Decode and verify                                                                                                      
try:                                                                                                                     
    decoded = base64.b64decode(token).decode()                                                                           
    parts = decoded.split('|')                                                                                           
    uid, tier, timestamp, signature = parts                                                                              
                                                                                                                        
    # Verify timestamp (5 minute window)                                                                                 
    current_time = int(datetime.utcnow().timestamp())                                                                    
    if current_time - int(timestamp) > 300:                                                                              
        return reject_request("Token expired")                                                                           
                                                                                                                        
    # Verify HMAC                                                                                                        
    secret_key = get_secret_from_secrets_manager()                                                                       
    expected = hmac.new(secret_key.encode(), f"{uid}|{tier}|{timestamp}".encode(), hashlib.sha256).hexdigest()           
                                                                                                                        
    if not hmac.compare_digest(signature, expected):                                                                     
        return reject_request("Invalid signature")                                                                       
                                                                                                                        
    # Set header for ALB routing                                                                                         
    headers['x-user-id'] = [{'key': 'X-User-ID', 'value': uid}]                                                          
    headers['x-user-tier'] = [{'key': 'X-User-Tier', 'value': tier}]                                                     
                                                                                                                        
    return request                                                                                                       
                                                                                                                        
except Exception as e:                                                                                                   
    return reject_request(f"Token validation failed: {e}")                                                               
5. Deploy Lambda@Edge                                                                                                        
- Package function with dependencies                                                                                       
- Create IAM role with Secrets Manager access                                                                              
- Deploy to us-east-1 (required for Lambda@Edge)                                                                           
                                                                                                                        
Phase 4: ALB Integration                                                                                                     
                                                                                                                        
6. Update /infrastructure/terraform/compute.tf                                                                               
- Attach Lambda@Edge to ALB listener as origin request trigger                                                             
- Configure error handling                                                                                                 
                                                                                                                        
Phase 5: Frontend Updates                                                                                                    
                                                                                                                        
7. Update /frontend/src/utils/routing/RoutingService.ts                                                                      
- Receive X-Routing-Token from backend responses                                                                           
- Store token (localStorage or memory)                                                                                     
- Include in subsequent requests                                                                                           
- Handle token expiration (re-fetch from backend)                                                                          
8. Update /frontend/src/utils/axios.ts                                                                                       
- Add interceptor to include X-Routing-Token header                                                                        
- Remove old X-User-ID and X-User-Tier logic                                                                               
                                                                                                                        
Phase 6: Testing                                                                                                             
                                                                                                                        
9. Test HMAC verification                                                                                                    
- Valid token routing                                                                                                      
- Expired token rejection                                                                                                  
- Tampered signature rejection                                                                                             
- Secret rotation                                                                                                          
                                                                                                                        
Critical Files                                                                                                               
                                                                                                                        
- /studio/app/common/core/middleware/secure_routing_middleware.py (CREATE)                                                   
- /studio/__main_unit__.py (register middleware)                                                                             
- /infrastructure/terraform/lambda_edge_routing_verifier.py (CREATE)                                                         
- /infrastructure/terraform/compute.tf (ALB + Lambda@Edge integration)                                                       
- /infrastructure/terraform/security.tf (Secrets Manager, IAM roles)                                                         
- /frontend/src/utils/routing/RoutingService.ts (token handling)                                                             
- /frontend/src/utils/axios.ts (interceptor updates)                                                                         
                                                                                                                        
Risk Assessment                                                                                                              
                                                                                                                        
Challenges:                                                                                                                  
- Lambda@Edge cold start latency (1-2s first request)                                                                        
- Secret rotation coordination between backend and Lambda@Edge                                                               
- Secrets Manager API calls from Lambda@Edge (may need caching)                                                              
- Additional infrastructure to monitor and maintain                                                                          
                                                                                                                        
Mitigation:                                                                                                                  
- Cache secret in Lambda@Edge environment variables (refresh periodically)                                                   
- Monitor Lambda@Edge execution time and errors                                                                              
- Keep warm with CloudWatch Events                                                                                           
                                                                                                                        
---                                                                                                                          
NOT RECOMMENDED: Option 2 (Hashed UID with generate_client_id)                                                               
                                                                                                                        
Why This Doesn't Fix the Security Issue                                                                                      
                                                                                                                        
While generate_client_id can technically be used for routing, it provides no security benefit:                               
                                                                                                                        
1. Still client-controlled: Headers remain set by frontend JavaScript                                                        
2. Deterministic hash: Same UID always produces same hash                                                                    
3. Easy to compute: Attacker just needs to hash target UID                                                                   
// Attacker's code:                                                                                                          
const targetUID = "known_premium_user_uid";                                                                                  
const hash = md5(targetUID).substring(0, 16);                                                                                
axios.defaults.headers['X-User-ID'] = hash;  // Spoofed!                                                                     
4. Collision risk: MD5 truncated to 16 chars has higher collision probability                                                
5. False sense of security: Appears more secure but isn't                                                                    
                                                                                                                        
Implementation (For Reference Only)                                                                                          
                                                                                                                        
If you still wanted to implement this despite security limitations:                                                          
                                                                                                                        
Backend: /infrastructure/terraform/premium_manager_package/premium_manager.py:2298                                           
import hashlib                                                                                                               
                                                                                                                        
# Hash the UID before creating rule                                                                                          
hashed_uid = hashlib.md5(user_id.encode()).hexdigest()[0:16]                                                                 
                                                                                                                        
Conditions=[                                                                                                                 
{"Field": "http-header", "HttpHeaderConfig": {"HttpHeaderName": "X-User-ID", "Values": [hashed_uid]}}                    
]                                                                                                                            
                                                                                                                        
Frontend: /frontend/src/utils/routing/RoutingService.ts                                                                      
import md5 from 'crypto-js/md5';                                                                                             
                                                                                                                        
const hashedUid = md5(user.uid).toString().substring(0, 16);                                                                 
this.routingInfo.routing_headers = {                                                                                         
"X-User-Tier": UserTier.PREMIUM,                                                                                           
"X-User-ID": hashedUid                                                                                                     
}                                                                                                                            
                                                                                                                        
Verdict: This is security theater. Don't implement this expecting real security.                                             
                                                                                                                        
---                                                                                                                          
Comparison Matrix                                                                                                            
                                                                                                                        
| Feature        | Option 3 (ALB JWT)     | Option 4 (HMAC Headers) | Option 2 (Hashed UID) |                                
|----------------|------------------------|-------------------------|-----------------------|                                
| Security       | ⭐⭐⭐⭐⭐ Highest     | ⭐⭐⭐⭐ High           | ⭐ Low (spoofable)    |                                
| Complexity     | ⭐⭐⭐ Medium          | ⭐⭐⭐⭐⭐ Highest      | ⭐ Low                |                                
| Lambda Changes | ⭐⭐ Major refactoring | ⭐⭐⭐⭐⭐ None         | ⭐⭐⭐⭐ Minor        |                                
| Infrastructure | ⭐⭐⭐ Significant     | ⭐⭐⭐ Medium           | ⭐⭐⭐⭐⭐ None       |                                
| Maintenance    | ⭐⭐⭐⭐ Low           | ⭐⭐⭐ Medium           | ⭐⭐⭐⭐ Low          |                                
| Standards      | ⭐⭐⭐⭐⭐ OAuth 2.0   | ⭐⭐⭐⭐ HMAC-SHA256    | ⭐ Obscurity          |                                
| Latency        | +0ms (offloaded)       | +1-5ms (Lambda@Edge)    | +0ms                  |                                
| Terraform Risk | ⚠️ Uncertain support   | ✅ Well supported       | ✅ No changes         |                                
                                                                                                                        
Recommendation:                                                                                                              
1. First choice: Option 3 (if Terraform supports it)                                                                         
2. Fallback: Option 4 (if Option 3 blocked)                                                                                  
3. Never: Option 2 (doesn't fix vulnerability)                                                                               
                                                                                                                        
---                                                                                                                          
Implementation Recommendation                                                                                                
                                                                                                                        
Two-Phase Approach                                                                                                           
                                                                                                                        
Phase 1: Research (1-2 days)                                                                                                 
- Verify Terraform AWS provider version supports ALB JWT verification                                                        
- Check aws_lb_listener resource documentation                                                                               
- Test in sandbox environment                                                                                                
- Review AWS documentation thoroughly                                                                                        
                                                                                                                        
Phase 2A: If Terraform Supports JWT → Implement Option 3                                                                     
- Proceed with ALB native JWT verification                                                                                   
- Follow implementation plan above                                                                                           
- Strongest security, cleanest architecture                                                                                  
                                                                                                                        
Phase 2B: If Terraform Doesn't Support JWT → Implement Option 4                                                              
- Use HMAC signed headers as proven alternative                                                                              
- Still cryptographically secure                                                                                             
- More infrastructure but well-tested pattern                                                                                
                                                                                                                        
Do NOT Implement Option 2                                                                                                    
                                                                                                                        
Using generate_client_id for routing provides a false sense of security. The headers remain client-controlled and            
spoofable. If an attacker can modify headers to send raw UIDs, they can equally well send hashed UIDs.                       
                                                                                                                        
---                                                                                                                          
References                                                                                                                   
                                                                                                                        
- https://docs.aws.amazon.com/elasticloadbalancing/latest/application/listener-verify-jwt.html                               
- https://aws.amazon.com/about-aws/whats-new/2025/11/application-load-balancer-jwt-verification/                             
- https://medium.com/@mehulkothari14/aws-alb-native-jwt-validation-515cc3f72351                                              
- https://medium.com/@yoshiyuki.watanabe/jwt-verification-feature-added-to-application-load-balancer-fef3ce06b00a            
                                                                                                                        
---                                                                                                                          
Next Steps                                                                                                                   
                                                                                                                        
1. Review this document and select preferred approach                                                                        
2. If OPTION 1: Research Terraform support first                                                                             
3. If OPTION 2: Proceed with HMAC implementation                                                                             
4. Do NOT proceed with Option 2 (security theater)                                                                           
                                                                                                                        
Let me know which option you'd like to pursue and I'll create a detailed implementation plan! 