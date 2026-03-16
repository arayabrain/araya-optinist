# Public Site Separation Plan: Isolating Public-Facing Pages from Free Tier Instances

## 1. Background and Problem

### Current Architecture
- Two tiers of ECS instances exist: "free tier" and "premium tier"
- Public-facing pages (landing page, login, registration, public data repository, etc.) run on free tier instances
- A single ALB Target Group contains all free tier instances, using round-robin + sticky sessions (5-minute ALB cookie)

### Problem
- Free tier instances are shared between authenticated user workloads and public-facing pages, creating **bidirectional performance coupling**:
  - **Free tier → Public site impact**: When free tier user workloads are under heavy load (e.g., running compute-intensive workflows), public page performance degrades — landing page, login, and public data repository become slow or unresponsive
  - **Public site → Free tier impact**: Conversely, spikes in public traffic (e.g., from marketing campaigns, search engine crawlers, or public data repository access) can consume instance resources and degrade performance for authenticated free tier users running workflows
- Both tiers have fundamentally different workload characteristics — free tier runs long-running, CPU/memory-intensive scientific workflows, while public pages serve lightweight, stateless HTTP requests — making shared instances an inefficient architecture

### Goal
- Separate free tier instances from public-facing page instances ("public site instances") to achieve **bidirectional isolation**: protect public page availability from free tier workloads, and protect free tier user performance from public traffic spikes

---

## 2. Current Architecture Investigation

### ALB Routing to Free Tier Instances

| Aspect | Specification |
|--------|--------------|
| Target Group | Single (`${env}-optinist-tg`) |
| ALB-level routing | Round-robin + Sticky Session (ALB Cookie, 5 min) |
| Logical user assignment | Free Manager Lambda manages via `free_user_assignments` table |
| Rebalancing | Free Manager Lambda monitors active user count every 5 min, redistributes idle users via round-robin |
| Scaling | ASG scaling based on active user count (1 instance per 5 users, max 10) |

### Public-Facing Pages (No Authentication Required)

| URL | Type | Purpose |
|-----|------|---------|
| `/` | Frontend | Landing page |
| `/public`, `/public/*` | Frontend + API | Public data repository |
| `/login` | Frontend | Login page |
| `/register` | Frontend | User registration page |
| `/reset-password` | Frontend | Password reset page |
| `/subscription/thanks` | Frontend | Checkout success page |
| `/subscription/failed` | Frontend | Checkout failure page |
| `/account-deleted` | Frontend | Account deletion confirmation |
| `/api/public/dataview` | API | Public data search API |
| `/api/public/dataview/workflow/reproduce/*` | API | Public experiment detail API |
| `/api/auth/login` | API | Login API |
| `/api/auth/refresh` | API | Token refresh API |
| `/api/auth/send_reset_password_mail` | API | Password reset email API |
| `/api/register` | API | User registration API |
| `/api/register/verify-status/*` | API | Email verification status API |
| `/api/register/resend-verification` | API | Resend verification email API |
| `/static/*` | Static | Static assets |
| `/images/*` | Static | Image assets |
| `/health` | API | Health check endpoint |

### Premium Tier Routing (No Changes)

Premium tier users are routed to dedicated instances via ALB listener rules matching `X-Routing-ID` / `X-User-Tier` headers issued by the backend after sign-in.

---

## 3. Proposed Architecture

### 3.1 Architecture Overview

```
                        ┌─────────────────────────────────────────────┐
                        │                   ALB                       │
                        │          (existing: ${env}-optinist-lb)     │
                        └────────┬──────────────┬─────────────────────┘
                                 │              │
                    ┌────────────┴───┐   ┌──────┴─────────────────────┐
                    │ Listener Rule  │   │  Listener Rule             │
                    │ (Premium)      │   │  (Authenticated)           │
                    │ X-Routing-ID   │   │  Authorization: Bearer *   │
                    │ Priority: high │   │  Priority: 50              │
                    └───────┬────────┘   └──────┬──────────┬──────────┘
                            │                   │          │
                   ┌────────▼────────┐  ┌───────▼──────┐  │
                   │ Premium TG      │  │  Free Tier   │  │
                   │ (per-user,      │  │ Target Group │  │
                   │  dynamic)       │  │  (existing)  │  │
                   └────────┬────────┘  └───────┬──────┘  │
                            │                   │         │
                   ┌────────▼────────┐  ┌───────▼──────┐  │  Default Action
                   │ Premium Tier   │  │ Free Tier    │  │         │
                   │ Instances      │  │ Instances    │  │  ┌──────▼──────┐
                   │ (existing)     │  │ (existing)   │  │  │ Public Site │
                   └────────────────┘  └──────────────┘  │  │ Target Group│
                                                         │  │ (NEW)       │
                                                         │  └──────┬──────┘
                                                         │  ┌──────▼──────┐
                                                         │  │ Public Site │
                                                         │  │ Instance(s) │
                                                         │  │ (NEW ASG)   │
                                                         │  └─────────────┘
                                                         │
                                        (all unmatched requests → Public Site)
```

### 3.2 Routing Strategy: JWT Header-Based Routing

Instead of path-based routing (which requires maintaining a list of public URLs in Terraform), we use **JWT header presence** to determine routing. This is simpler and requires no infrastructure changes when new pages or API endpoints are added.

#### Routing Decision Matrix

| Priority | Condition | Target | Notes |
|----------|-----------|--------|-------|
| High (existing, dynamic) | `X-Routing-ID` + `X-User-Tier` header match | Premium Instance | No change |
| 50 (new) | `Authorization: Bearer *` header present | Free Tier TG (existing) | Authenticated users |
| Default (changed) | No match (no Authorization header) | **Public Site TG (new)** | Unauthenticated users |

#### Why Header-Based Routing is Superior to Path-Based

| Aspect | Path-pattern approach | JWT header approach |
|--------|----------------------|---------------------|
| **Maintainability** | Must update Terraform when public paths change | No infrastructure changes needed for new pages |
| **Rule complexity** | Long list of path patterns | Single header condition |
| **Risk of misconfiguration** | Missing a path routes it to wrong tier | Automatic: no JWT = public, JWT = free/premium |
| **Classification logic** | Infrastructure defines "what is public" | App-level auth determines routing naturally |

### 3.3 New Terraform Resources

All new resources will be placed in a new file: `compute_public.tf`

#### A. Public Site Target Group

```hcl
resource "aws_lb_target_group" "public" {
  name        = "${local.env_prefix}-public-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "instance"

  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 5
    interval            = 60
    matcher             = "200"
    path                = "/health"
    port                = "traffic-port"
    protocol            = "HTTP"
    timeout             = 30
  }

  # No sticky sessions needed — public pages are stateless
  stickiness {
    type    = "lb_cookie"
    enabled = false
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "${local.env_prefix}-public-target-group"
  }
}
```

#### B. ALB Listener Rule and Default Action Changes

```hcl
# Route authenticated requests to Free Tier
resource "aws_lb_listener_rule" "authenticated_to_free_tier" {
  listener_arn = aws_lb_listener.autoscaling_https.arn
  priority     = 50  # After Premium rules (dynamically created, higher priority)

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.autoscaling.arn
  }

  condition {
    http_header {
      http_header_name = "Authorization"
      values           = ["Bearer *"]
    }
  }

  tags = {
    Name = "${local.env_prefix}-authenticated-rule"
  }
}

# Dev environment HTTP listener rule (only when custom domain is disabled)
resource "aws_lb_listener_rule" "authenticated_to_free_tier_http" {
  count        = var.enable_custom_domain ? 0 : 1
  listener_arn = aws_lb_listener.autoscaling.arn
  priority     = 50

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.autoscaling.arn
  }

  condition {
    http_header {
      http_header_name = "Authorization"
      values           = ["Bearer *"]
    }
  }

  tags = {
    Name = "${local.env_prefix}-authenticated-rule-http"
  }
}
```

**Changes to existing listeners** (in `compute.tf`):

```hcl
# MODIFIED: Default action changed from Free Tier TG to Public Site TG
resource "aws_lb_listener" "autoscaling_https" {
  # ... existing settings ...
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.public.arn  # CHANGED
  }
}

# MODIFIED: Default action for HTTP listener (dev environment)
resource "aws_lb_listener" "autoscaling" {
  # ... existing settings ...
  default_action {
    # When enable_custom_domain = true: redirect to HTTPS (unchanged)
    # When enable_custom_domain = false: forward to public TG (CHANGED)
    target_group_arn = var.enable_custom_domain ? null : aws_lb_target_group.public.arn  # CHANGED
    # ...
  }
}
```

#### C. Public Site Launch Template

```hcl
resource "aws_launch_template" "public" {
  name_prefix   = "${local.env_prefix}-public-"
  image_id      = data.aws_ami.ecs_optimized.id
  instance_type = var.public_instance_type  # Default: "t3.small"
  key_name      = aws_key_pair.subscr_optinist_cloud_key_pair.key_name

  vpc_security_group_ids = [aws_security_group.ecs.id]

  iam_instance_profile {
    name = aws_iam_instance_profile.ecs_instance_profile.name
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size = 40  # Smaller — public site has minimal local storage needs
      volume_type = "gp3"
      encrypted   = true
    }
  }

  monitoring {
    enabled = true
  }

  user_data = base64encode(templatefile("${path.module}/../scripts/ecs-user-data.sh", {
    tier                  = "public"
    cluster_name          = aws_ecs_cluster.main.name
    git_branch            = var.git_branch
    git_repo              = var.git_repo
    firebase_config_json  = var.firebase_config_json
    firebase_private_json = var.firebase_private_json
    ecr_registry          = split("/", var.ecr_repository_url)[0]
    ecr_repository_url    = var.ecr_repository_url
    efs_id                = aws_efs_file_system.snmk.id
    db_host               = replace(aws_db_instance.main.endpoint, ":3306", "")
    swap_size_mb          = 4096  # 4GB swap to compensate for t3.small 2GB RAM
  }))

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name    = "${local.env_prefix}-public-instance"
      Type    = "ECS-Public"
      Tier    = "public"
      Service = "public"
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}
```

#### D. Public Site ASG

```hcl
resource "aws_autoscaling_group" "public" {
  name                      = "${local.env_prefix}-public-asg"
  vpc_zone_identifier       = [aws_subnet.private1.id, aws_subnet.private2.id]
  target_group_arns         = [aws_lb_target_group.public.arn]
  health_check_type         = "ELB"
  health_check_grace_period = 900
  default_cooldown          = 300

  min_size         = var.public_asg_min_size          # Default: 1
  max_size         = var.public_asg_max_size          # Default: 2
  desired_capacity = var.public_asg_desired_capacity  # Default: 1

  launch_template {
    id      = aws_launch_template.public.id
    version = "$Latest"
  }

  force_delete              = true
  termination_policies      = ["OldestInstance"]
  wait_for_capacity_timeout = "0"
  protect_from_scale_in     = false

  enabled_metrics = [
    "GroupMinSize",
    "GroupMaxSize",
    "GroupDesiredCapacity",
    "GroupInServiceInstances",
    "GroupTotalInstances",
  ]

  tag {
    key                 = "Name"
    value               = "${local.env_prefix}-public-asg-instance"
    propagate_at_launch = true
  }

  tag {
    key                 = "Service"
    value               = "public"
    propagate_at_launch = true
  }

  tag {
    key                 = "Tier"
    value               = "public"
    propagate_at_launch = true
  }

  tag {
    key                 = "LaunchTemplateVersion"
    value               = aws_launch_template.public.latest_version
    propagate_at_launch = true
  }

  instance_refresh {
    strategy = "Rolling"
    preferences {
      instance_warmup        = 300
      min_healthy_percentage = 0
    }
  }

  lifecycle {
    ignore_changes = [desired_capacity]
  }
}
```

#### E. Public Site ECS Capacity Provider and Service

```hcl
resource "aws_ecs_capacity_provider" "public" {
  name = "${local.env_prefix}-public-capacity-provider"

  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.public.arn
    managed_termination_protection = "DISABLED"

    managed_scaling {
      status                    = "DISABLED"
      maximum_scaling_step_size = 1
      minimum_scaling_step_size = 1
      target_capacity           = 90
      instance_warmup_period    = 300
    }
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "${local.env_prefix}-public-capacity-provider"
  }
}

resource "aws_ecs_service" "public" {
  name                               = "${local.env_prefix}-public-service"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.public.arn  # Dedicated lightweight task def
  desired_count                      = 1
  deployment_maximum_percent         = 200
  deployment_minimum_healthy_percent = 0

  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.public.name
    weight            = 1
    base              = 0
  }

  enable_execute_command = true

  load_balancer {
    target_group_arn = aws_lb_target_group.public.arn
    container_name   = "${local.env_prefix}-public-container"
    container_port   = 8000
  }

  # Target public tier instances only
  placement_constraints {
    type       = "memberOf"
    expression = "attribute:tier == public"
  }

  health_check_grace_period_seconds = 900

  depends_on = [
    aws_autoscaling_group.public,
    aws_db_instance.main,
    aws_lb.autoscaling,
    aws_lb_listener.autoscaling
  ]

  tags = {
    Name = "${local.env_prefix}-public-service"
    Tier = "public"
  }
}
```

#### F. Public Site Task Definition (Lightweight)

A dedicated task definition is required because t3.small (2 GB RAM) cannot satisfy the existing task definition's `memoryReservation` of 4096 MB. The public site only serves static SPA pages and lightweight public APIs, so significantly lower memory is sufficient.

```hcl
resource "aws_ecs_task_definition" "public" {
  family                   = "${local.env_prefix}-public-taskdef"
  requires_compatibilities = ["EC2"]
  network_mode             = "bridge"
  cpu                      = 1024
  memory                   = 1536
  task_role_arn            = aws_iam_role.ecs_task.arn
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn

  container_definitions = jsonencode([
    {
      name              = "${local.env_prefix}-public-container"
      image             = "${var.ecr_repository_url}:latest"
      cpu               = 896
      memory            = 1536
      memoryReservation = 1024
      essential         = true
      workingDirectory  = "/app"
      entryPoint        = ["/bin/sh", "-c"]
      command           = ["./cloud-startup.sh"]

      linuxParameters = {
        maxSwap    = 4096  # 4GB swap on t3.small
        swappiness = 20
      }

      portMappings = [
        {
          name          = "${local.env_prefix}-public-container-port-8000"
          containerPort = 8000
          hostPort      = 8000
          protocol      = "tcp"
        }
      ]

      environment = [
        # Same environment variables as free tier task definition
        # (DB connection, Firebase config, Stripe keys, etc.)
        # ... (identical to aws_ecs_task_definition.autoscaling)
      ]

      secrets = [
        # Same secrets as free tier task definition
        # ... (identical to aws_ecs_task_definition.autoscaling)
      ]

      mountPoints = [
        {
          sourceVolume  = "${local.env_prefix}-public-snmk-volume"
          containerPath = "/app/.snakemake"
          readOnly      = false
        }
      ]

      healthCheck = {
        command     = ["CMD-SHELL", "curl -v http://127.0.0.1:8000/health"]
        interval    = 300
        timeout     = 5
        retries     = 3
        startPeriod = 300
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"             = "/ecs/${local.env_prefix}-public-taskdef"
          "mode"                      = "non-blocking"
          "awslogs-multiline-pattern" = "^\\[\\d{4}-\\d{2}-\\d{2}\\s\\d{2}:\\d{2}:\\d{2}"
          "max-buffer-size"           = "25m"
          "awslogs-region"            = var.aws_region
          "awslogs-create-group"      = "true"
          "awslogs-stream-prefix"     = "ecs"
        }
      }
    }
  ])

  volume {
    name = "${local.env_prefix}-public-snmk-volume"
    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.snmk.id
      root_directory     = "/"
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.snmk.id
        iam             = "DISABLED"
      }
    }
  }

  tags = {
    Name = "${local.env_prefix}-public-taskdef"
    Tier = "public"
  }
}
```

**Instance type and memory sizing rationale:**

| Spec | Free Tier (existing) | Public Site (new) |
|------|---------------------|-------------------|
| Instance type | t3.large (8 GB RAM) | t3.small (2 GB RAM) |
| `memory` (hard limit) | 6656 MB | 1536 MB |
| `memoryReservation` (soft) | 4096 MB | 1024 MB |
| `maxSwap` | 32768 MB | 4096 MB |
| EBS volume | 120 GB | 40 GB |
| Workload | User workflows (CPU/memory intensive) | SPA serving + lightweight public APIs |

t3.small provides ~1.7 GB usable RAM after the ECS agent. With `memoryReservation=1024`, ECS can schedule the task. The 4 GB swap compensates for occasional memory spikes (e.g., public data API queries with large result sets).

#### G. New Variables

```hcl
variable "public_instance_type" {
  description = "Instance type for public site instances"
  type        = string
  default     = "t3.small"
}

variable "public_asg_min_size" {
  description = "Minimum number of public site instances in ASG"
  type        = number
  default     = 1
}

variable "public_asg_max_size" {
  description = "Maximum number of public site instances in ASG"
  type        = number
  default     = 2
}

variable "public_asg_desired_capacity" {
  description = "Desired number of public site instances in ASG"
  type        = number
  default     = 1
}
```

### 3.4 Terraform File Structure

```
infrastructure/terraform/
├── compute.tf              # MODIFIED (default action changed to public site TG)
├── compute_domain.tf       # Existing (Route53, ACM) — no changes
├── compute_public.tf       # NEW — all public site resources
│   ├── aws_lb_target_group.public
│   ├── aws_lb_listener_rule.authenticated_to_free_tier
│   ├── aws_lb_listener_rule.authenticated_to_free_tier_http
│   ├── aws_launch_template.public
│   ├── aws_autoscaling_group.public
│   ├── aws_ecs_capacity_provider.public
│   ├── aws_ecs_task_definition.public
│   ├── aws_ecs_service.public
│   ├── aws_cloudwatch_log_group.public_logs
│   ├── aws_cloudwatch_metric_alarm.public_task_stopped
│   ├── output.public_service_name
│   └── output.public_asg_name
├── main.tf                 # MODIFIED (new variables added)
└── ...
```

### 3.5 Changes to Existing Resources

| Resource | File | Change |
|----------|------|--------|
| `aws_lb_listener.autoscaling_https` | `compute.tf` | `default_action.target_group_arn` → `public` TG |
| `aws_lb_listener.autoscaling` | `compute.tf` | `default_action.target_group_arn` → `public` TG (when `enable_custom_domain = false`) |
| `aws_ecs_cluster_capacity_providers.main` | `compute.tf` | Add `public` capacity provider to the list |
| `aws_ecs_service.autoscaling` | `compute.tf` | Placement constraint changed from `distinctInstance` to `attribute:tier == free` (prevents free tier tasks from being scheduled on public instances) |

---

## 4. Routing Flow After Sign-In

### Request Flow by Authentication State

```
[Unauthenticated User]
  │
  ├─ GET /                     (no Authorization header) → default → Public Site ✓
  ├─ GET /login                (no Authorization header) → default → Public Site ✓
  ├─ GET /public               (no Authorization header) → default → Public Site ✓
  ├─ POST /api/auth/login      (no Authorization header) → default → Public Site ✓
  │     │
  │     ▼ (JWT returned in response body, frontend stores in localStorage)
  │
[Authenticated User (JWT in localStorage)]
  │
  ├─ GET /api/workspaces/*     (Authorization: Bearer xxx) → header rule → Free Tier ✓
  ├─ GET /api/users/me         (Authorization: Bearer xxx) → header rule → Free Tier ✓
  ├─ POST /api/experiments/*   (Authorization: Bearer xxx) → header rule → Free Tier ✓
  │
  └─ [Premium User (X-Routing-ID header attached)]
       │
       └─ GET /api/*            → Premium rule (higher priority) → Premium Instance ✓

[Browser Reload (Authenticated User)]
  │
  ├─ GET /                     (no Authorization header*) → default → Public Site
  │                            * JWT is in localStorage, not sent with browser navigation
  │     ▼ SPA Middleware serves index.html
  │     ▼ React boots, reads JWT from localStorage
  ├─ GET /api/users/me         (Authorization: Bearer xxx) → header rule → Free Tier ✓
```

### How the Switch Works

1. **Before sign-in**: All requests lack the `Authorization` header → ALB default action → Public Site instance
2. **Sign-in (`POST /api/auth/login`)**: Processed by Public Site instance. Returns JWT. `SecureRoutingMiddleware` also returns `x-routing-id` / `x-user-tier` headers in the response
3. **After sign-in**: Frontend attaches `Authorization: Bearer <JWT>` to all API requests via axios interceptor → ALB header rule matches → Free Tier Target Group
4. **Premium users**: `X-Routing-ID` / `X-User-Tier` headers match Premium listener rules (higher priority) → dedicated Premium instance (no change)

### Frontend Code Changes

**None required.** The routing switch is handled entirely at the ALB level based on the `Authorization` header that the frontend already attaches to authenticated requests. No frontend code modifications are needed.

---

## 5. Components That Require No Changes

| Component | Reason |
|-----------|--------|
| Premium tier routing | `X-Routing-ID`-based listener rules are unchanged |
| Free Manager Lambda | Existing ASG management logic is unchanged |
| Premium Manager Lambda | Existing premium instance management is unchanged |
| SecureRoutingMiddleware | Header issuance and validation logic is unchanged |
| Frontend (SPA) | Routing handled by ALB; no frontend changes needed |
| Docker Image | Same image used for all tiers |
| RDS / RDS Proxy | Unchanged |
| S3 / EFS | Unchanged |

---

## 6. Considerations and Notes

### 6.1 Login API (`/api/auth/login`) on Public Site

- The login API is processed by the Public Site instance
- On successful login, the response includes JWT along with `x-routing-id` / `x-user-tier` headers (via `SecureRoutingMiddleware`)
- This ensures proper routing to free/premium instances begins immediately after login
- The Public Site instance runs the same application and has full DB connectivity via RDS Proxy, so login processing works identically

### 6.2 Stripe Webhook Routing

- Stripe webhooks POST to `/api/subscription/webhook`
- Stripe uses `Stripe-Signature` header for authentication, not `Authorization: Bearer`
- Therefore, webhook requests have no `Authorization` header → routed to Public Site instance via default action
- The Public Site instance runs the same application with full DB access, so webhook processing works correctly
- Note: In-memory cache invalidation (`invalidate_user_tier_cache`) is local to the processing instance. However, the cache has a 5-minute TTL, so this is not a new issue (same behavior exists today with multiple free tier instances)

### 6.3 t3.small Instance Sizing

- t3.small provides 2 GB RAM + 2 vCPUs
- After ECS agent overhead (~300 MB), approximately 1.7 GB is available for the container
- The dedicated lightweight task definition (`memoryReservation=1024 MB`) fits within this budget
- 4 GB swap compensates for occasional memory spikes
- Public site workload is lightweight: SPA static file serving + read-only public data API queries
- If monitoring shows memory pressure, upgrade path is straightforward: change `public_instance_type` to `t3.medium`

### 6.4 Future Scaling

- ASG-based design allows easy scaling by adjusting `public_asg_max_size`
- CPU/memory-based auto-scaling policies can be added to the ASG if needed
- No Lambda-based management (like Free Manager) is needed for public site — ALB handles load distribution naturally

### 6.5 `ecs-user-data.sh` Verification

- The `tier` parameter has a new value `"public"`
- **Verified**: `ecs-user-data.sh` simply passes the tier value through to ECS Container Instance attributes via `echo ECS_INSTANCE_ATTRIBUTES='{"tier":"${tier}"}' >> /etc/ecs/ecs.config`
- No script changes are needed — any tier string value is accepted

### 6.6 ECS Cluster Capacity Providers Update

- The existing `aws_ecs_cluster_capacity_providers` resource must include the new `public` capacity provider
- This is a modification to `compute.tf`

### 6.7 CloudWatch Monitoring

- A dedicated log group `/ecs/${env}-optinist-public-taskdef` is created for public site container logs (14-day retention)
- A `public_task_stopped` CloudWatch alarm monitors the ECS service and fires when running task count drops below 1, using the same `critical_alerts_actions` as other service alarms

---

## 7. Cost Impact

| Resource | Additional Cost (est./month) |
|----------|------------------------------|
| EC2 t3.small x1 (Public Site) | ~$15 |
| EBS 40 GB gp3 | ~$3 |
| ALB Target Group (additional) | Free (ALB itself is existing) |
| CloudWatch Log Group | ~$1 |
| **Total** | **~$19** |

---

## 8. Implementation Steps

### Phase 1: Terraform Code (Completed)
1. Created `compute_public.tf` with all new resources (target group, listener rules, launch template, ASG, capacity provider, task definition, ECS service, CloudWatch log group, CloudWatch alarm, outputs)
2. Added 4 new variables to `main.tf` (`public_instance_type`, `public_asg_min_size`, `public_asg_max_size`, `public_asg_desired_capacity`)
3. Modified existing listener default actions in `compute.tf` (both HTTP and HTTPS listeners)
4. Updated `aws_ecs_cluster_capacity_providers` in `compute.tf` to include the public site capacity provider
5. Changed free tier ECS service placement constraint from `distinctInstance` to `attribute:tier == free` in `compute.tf`
6. Verified `ecs-user-data.sh` handles `tier=public` correctly — no changes needed (passes through any tier value)
7. Ran `terraform fmt` — all files formatted correctly
8. Note: `terraform validate` requires `terraform init` with backend config (`.terraform/` is gitignored)

### Phase 2: Development Environment Validation
1. Run `terraform plan` to review diff
2. Run `terraform apply` to deploy to dev environment
3. Verify unauthenticated requests (landing page, login page, public data) reach Public Site instance
4. Verify authenticated API requests reach Free Tier instance after login
5. Verify Premium tier routing is unaffected
6. Verify Stripe webhook processing works on Public Site instance

### Phase 3: Load Testing
1. Apply load to Free Tier instances while monitoring Public Site response times — confirm isolation
2. Verify Public Site instance health checks and auto-recovery
3. Monitor t3.small memory usage and swap utilization under realistic traffic

### Phase 4: Production Deployment
1. Run `terraform plan` against production
2. Deploy during maintenance window via `terraform apply`
3. Verify routing behavior and monitor CloudWatch metrics
4. Confirm public page performance is decoupled from free tier load

---

## 9. Related Documents

| Document | Contents |
|----------|----------|
| [TERRAFORM_ARCHITECTURE.md](TERRAFORM_ARCHITECTURE.md) | Overall Terraform architecture |
| [ALB_ROUTING_ARCHITECTURE.md](ALB_ROUTING_ARCHITECTURE.md) | ALB routing and Secure Routing IDs |
| [AUTH_ROUTING_ARCHITECTURE.md](AUTH_ROUTING_ARCHITECTURE.md) | Authentication flow and SPA routing |
| [FREE_MANAGER_ARCHITECTURE.md](FREE_MANAGER_ARCHITECTURE.md) | Free tier auto-scaling and rebalancing |
