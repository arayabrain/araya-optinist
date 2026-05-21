# Dev Environment Scheduler — Implementation Review

## Why It Was Implemented

The development environment runs 24/7 but is only used during business hours (08:00-22:00 JST, Mon-Fri). This wastes ~46% of compute costs on idle resources during nights, weekends, and holidays.

The dev scheduler Lambda automatically stops all resources at 22:00 and starts them at 08:00, eliminating idle costs while keeping the environment ready for developers each morning.

## What Was Implemented

### Core: Dev Scheduler Lambda (`dev_scheduler.py`)
- **Stop/Start orchestration** for: NAT, RDS, EC2 (background + premium), ASG, ECS services, EventBridge rules, CloudWatch alarms
- **Stop/Destroy mode** — `"stop"` for fast resume (~2 min), `"destroy"` for max savings before long holidays (~10 min resume)
- **Per-stage retry** with exponential backoff (3 attempts per operation)
- **Manual override** — skip the next stop with a TTL-based SSM parameter
- **Startup timestamp** — written to SSM for premium_manager grace period

### Infrastructure: Terraform (`dev_schedule.tf`, `main.tf`, `infrastructure.tf`, `security.tf`, `compute.tf`)
- EventBridge rules for start (08:00), stop (22:00), and verification (08:15, 22:15)
- IAM permissions scoped to specific resources
- `stop_mode` configurable via Terraform variable
- NAT instance iptables via systemd service (survives stop/start cycles)
- ALB port 80 → 8080 redirect + port 8080 security group ingress (dev only)
- `effective_frontend_port` local resolves to 8080 for dev, frontend_port for prod

### Premium Integration (`premium_manager.py`, `premium_manager.tf`)
- Startup grace period (20 min) — premium_manager skips monitoring after environment start
- Prevents acting on stale DB state while instances boot

### Subscription Fix (`checkout_service.py`, `subscription.py`)
- `SELECT FOR UPDATE` to prevent duplicate subscription records from concurrent webhooks
- Unique constraint on `user_id` in `subscription_users` table

### Frontend (`axios.ts`)
- Handle ALB 502 (empty target group) same as 503 for premium routing fallback

## Fixes Applied In This Review

| Fix | File | Change |
|---|---|---|
| Handle terminated instances | `dev_scheduler.py` | `start_instance()` and `stop_instance()` now detect `terminated` state and log a clear warning instead of silently failing |
| SSM client reuse | `premium_manager.py` | Moved boto3 SSM client to module-level lazy singleton instead of creating a new client on every 15-min monitoring call |
| Port 8080 ingress dev-only | `security.tf` | Made port 8080 ALB ingress rule conditional — only created when `enable_custom_domain = false` (dev). Production won't expose port 8080 |

## Known Limitations (Dev-Only, Acceptable)

| Limitation | Impact | Reason Acceptable |
|---|---|---|
| `PREMIUM_INSTANCE_IDS` becomes stale if instance is terminated | Scheduler tries to start a dead instance; premium users get autoscaling-pool fallback | Dev only — `terraform apply` refreshes the ID. Premium_manager dynamically creates instances as fallback |
| No Alembic migration for `user_id` unique constraint | Constraint was added manually to dev DB, not via migration | Dev only — production deployment will need the migration added before merge |
| Premium users get `autoscaling-pool` assignment after stop/start | Users need to re-login to get re-assigned to dedicated instance | The 502 fallback in `axios.ts` handles this gracefully — user sees free tier temporarily, then premium assignment triggers on next login |
| `handler(event, context)` — `context` unused | Pylance warning | Lambda requires the signature; could use `context.get_remaining_time_in_millis()` for future timeout-aware execution |

## Pros and Cons

### Pros

| Pro | Reason |
|---|---|
| **~46% compute cost savings** | Dev resources idle 14h/weekday + 48h/weekend = 118h/168h wasted |
| **Fast resume with stop mode** | RDS starts in ~2 min instead of ~10 min restore, developers not blocked |
| **Destroy mode for holidays** | Prevents AWS 7-day auto-restart on extended breaks (Golden Week, year-end) |
| **Self-healing with verification rules** | 15-min retry catches Lambda timeouts or crashes automatically |
| **Per-stage retry** | Transient AWS API failures don't stop the entire operation |
| **Idempotent operations** | Safe to re-run start or stop multiple times (no side effects) |
| **Clean ECS shutdown** | Setting `desired_count=0` prevents failed placement noise in logs overnight |
| **Grace period for premium_manager** | Prevents monitoring from fighting startup process |
| **Configurable via Terraform** | `stop_mode`, `enable_dev_schedule`, schedule times all in tfvars |
| **NAT systemd service** | Iptables rules persist reliably across stop/start cycles |

### Cons

| Con | Mitigation |
|---|---|
| **Premium routing breaks after restart** | 502 fallback in frontend; users re-login to re-establish. Could be improved with premium_manager reconciliation on startup |
| **Adds infrastructure complexity** | 4 EventBridge rules, SSM parameters, IAM policies, systemd service — more moving parts to maintain |
| **No post-start health verification** | Scheduler reports "success" even if NAT isn't forwarding or ECS tasks aren't running. Verification rules re-invoke but don't check health |
| **NAT instance is a single point of failure** | If NAT fails, all private subnet instances lose connectivity. A managed NAT Gateway would be more reliable but costs more |
| **15-min gap between start and verify** | If start fails silently, users wait 15 min for the verify rule to retry |
| **Stale instance IDs after termination** | Requires `terraform apply` to update — no self-discovery by tag |
