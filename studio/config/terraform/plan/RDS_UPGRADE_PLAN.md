# RDS Connection Pool Upgrade Plan

## Problem Summary

On **2025-11-20 04:05-04:35 UTC**, the system experienced a database connection exhaustion crisis:
- **Error**: `ERROR 1040 (08004): Too many connections`
- **Impact**: Multiple ECS task crashes, Lambda function failures, premium user assignment failures
- **Root Cause**: Connection pool configuration mismatch

### The Issue

**RDS Configuration:**
- Instance: `db.t4g.micro` (1GB RAM)
- Max connections: ~86 connections

**Application Configuration:**
- `POOL_SIZE=100` per ECS task (from studio/app/common/db/config.py:18)
- `max_overflow=20` per ECS task (from studio/app/common/db/database.py:15)
- **Total per task: 120 potential connections**

**Math Problem:**
```
Current state (4 tasks running):
4 tasks × 120 max = 480 potential connections
Available: 86 connections
Oversubscription: 5.6x ❌

Analysis of peak crisis (20/11/25) (17-20 tasks during crash loop):
20 tasks × 120 max = 2,400 potential connections
Available: 86 connections
Oversubscription: 28x ❌❌❌

Worst case (full scale: 10 free + 20 premium):
30 tasks × 120 max = 3,600 potential connections
Available: 86 connections
Oversubscription: 42x ❌❌❌
```

### Why POOL_SIZE=100 Was Wrong

The configuration was copied from a Japanese article about high-traffic websites:
- **Article's scenario**: 100 web servers, 900 req/sec each, 90,000 total req/sec
- **Our scenario**: 4-30 ECS tasks, scientific workflows, ~10-100 req/sec total
- **Article's DB**: Enterprise MySQL with 10,000+ connections
- **Our DB**: db.t4g.micro with 86 connections

**The configuration doesn't match our architecture or scale.**

---

## Three-Phase Solution

### Phase 1: Pool

**Objective**: Prevent immediate connection exhaustion failures

#### Step 1.1: Reduce POOL_SIZE

**File**: `studio/app/common/db/config.py`

**Change**:
```python
# Line 18
# OLD:
POOL_SIZE: int = Field(default=100)

# NEW:
POOL_SIZE: int = Field(default=5)  # Sized for actual concurrent query needs per task
```

**Rationale**:
- Typical concurrent queries per task: 2-5
- Peak bursts handled by overflow pool
- 30 tasks × 5 persistent = 150 connections (need RDS upgrade)
- Actual usage: ~30-60 connections


---

### Phase 2: RDS Upgrade

**Objective**: Provide adequate connection capacity for current and future scale

#### Step 2.1: Update Terraform Configuration

**File**: `studio/config/terraform/infrastructure.tf`

**Change**:
```terraform
# Line 417
resource "aws_db_instance" "main" {
  identifier                      = "subscr-optinist-cloud-rds"
  allocated_storage               = 20
  storage_type                    = "gp3"
  engine                          = "mysql"
  engine_version                  = "8.0"
  instance_class                  = "db.t4g.small"  # CHANGED from db.t4g.micro
  parameter_group_name            = "default.mysql8.0"
  db_name                         = var.mysql_database
  username                        = var.mysql_user
  password                        = var.mysql_password
  skip_final_snapshot             = true
  final_snapshot_identifier       = "${var.mysql_database}-final-snapshot"
  backup_retention_period         = 7
  monitoring_interval             = 60
  monitoring_role_arn             = aws_iam_role.rds_monitoring.arn
  publicly_accessible             = false
  enabled_cloudwatch_logs_exports = ["error", "general", "slowquery"]
  network_type                    = "IPV4"
  port                            = 3306
  vpc_security_group_ids          = [aws_security_group.rds.id]
  db_subnet_group_name            = aws_db_subnet_group.main.name
  multi_az                        = false
  storage_encrypted               = true

  tags = {
    Name = "subscr-optinist-cloud-rds"
  }
}
```

**New Capacity**:
- Instance: `db.t4g.small` (2GB RAM)
- Max connections: ~166 connections (calculated via `{DBInstanceClassMemory/12582880}`)
- Cost increase: ~$15/month → ~$30/month

**Capacity Analysis**:
```
With Phase 1 changes (POOL_SIZE=5):
30 tasks × 5 persistent = 150 connections held
30 tasks × 25 max = 750 theoretical
Actual usage: ~60-90 connections

Available: 166 connections
Utilization: 36-54% (healthy range)
Headroom: 76-106 connections for growth
```

---

**Why This Works:**

"Theoretical max" ≠ "Actual usage"

The 750 is the absolute worst case if:
- All 30 tasks exist simultaneously, AND
- Every single task is handling 25 concurrent queries at the exact same moment, AND
- All queries happen to need database access at the same millisecond

In reality:

Actual connection usage:
- 30 tasks × 5 persistent = 150 connections ALWAYS held
- Overflow connections are created ONLY when needed
- Typical usage: 30-60 active connections
- Peak bursts: 80-120 connections
- Never reaches 750 in practice

How SQLAlchemy Pools Work:

Normal operation (low load):
- Task has 5 persistent connections in pool
- User makes request → uses 1 connection from pool
- Request completes → connection returns to pool
- Pool size stays at 5

Burst traffic (high load):
- All 5 persistent connections busy
- New request comes in → creates overflow connection (6th)
- Request completes → overflow connection is CLOSED (destroyed)
- Pool drops back to 5 persistent

---

### Phase 3:  AWS RDS Proxy

**Objective**: Implement infrastructure-level connection pooling for scalability


**Benefits**:
- Handles 1000s of application connections
- Uses only ~50-100 actual database connections
- Built-in connection pooling and failover
- Managed service (no maintenance)
- IAM authentication support

**Architecture**:
```
Before:
ECS Tasks → RDS (86-166 direct connections)

After:
ECS Tasks → RDS Proxy → RDS (50-100 pooled connections)
  ↓
Supports 1000s of app connections
```

**Implementation**:

1. **Create RDS Proxy** (Terraform):

```terraform
# New file: studio/config/terraform/rds_proxy.tf

resource "aws_db_proxy" "main" {
  name                   = "subscr-optinist-rds-proxy"
  engine_family          = "MYSQL"
  auth {
    auth_scheme = "SECRETS"
    secret_arn  = aws_secretsmanager_secret.rds_credentials.arn
  }
  role_arn               = aws_iam_role.rds_proxy.arn
  vpc_subnet_ids         = [aws_subnet.private_a.id, aws_subnet.private_c.id]
  require_tls            = false

  tags = {
    Name = "subscr-optinist-rds-proxy"
  }
}

resource "aws_db_proxy_default_target_group" "main" {
  db_proxy_name = aws_db_proxy.main.name

  connection_pool_config {
    max_connections_percent      = 100
    max_idle_connections_percent = 50
    connection_borrow_timeout    = 120
  }
}

resource "aws_db_proxy_target" "main" {
  db_proxy_name         = aws_db_proxy.main.name
  target_group_name     = aws_db_proxy_default_target_group.main.name
  db_instance_identifier = aws_db_instance.main.id
}

# IAM role for RDS Proxy
resource "aws_iam_role" "rds_proxy" {
  name = "subscr-rds-proxy-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "rds.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy" "rds_proxy_secrets" {
  role = aws_iam_role.rds_proxy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "secretsmanager:GetSecretValue"
      ]
      Resource = aws_secretsmanager_secret.rds_credentials.arn
    }]
  })
}

# Store RDS credentials in Secrets Manager
resource "aws_secretsmanager_secret" "rds_credentials" {
  name = "subscr-rds-credentials"
}

resource "aws_secretsmanager_secret_version" "rds_credentials" {
  secret_id = aws_secretsmanager_secret.rds_credentials.id
  secret_string = jsonencode({
    username = var.mysql_user
    password = var.mysql_password
  })
}

# Output proxy endpoint
output "rds_proxy_endpoint" {
  value = aws_db_proxy.main.endpoint
}
```

2. **Update Application Configuration**:

```python
# studio/app/common/db/config.py
# Update MYSQL_SERVER to use proxy endpoint instead of direct RDS
# Will be done via environment variable in ECS task definition
```

3. **Update ECS Task Definitions** (in compute.tf, deployment.tf):

```terraform
# Change MYSQL_SERVER from direct RDS to proxy
environment {
  name  = "MYSQL_SERVER"
  # OLD: value = aws_db_instance.main.endpoint
  # NEW:
  value = aws_db_proxy.main.endpoint
}
```

**Cost**:
- RDS Proxy: ~$0.015/hour = ~$11/month
- Total new cost: ~$11/month

**Benefits at scale**:
- Support 100+ ECS tasks without increasing database connections
- Better connection reuse (lower latency)
- Automatic failover if RDS restarts
- Can keep POOL_SIZE=5 indefinitely

---

## References

- CloudWatch metrics showing crisis: 2025-11-20 04:05-04:35 UTC
- SQLAlchemy connection pooling docs: https://docs.sqlalchemy.org/en/20/core/pooling.html
- RDS connection limits: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_Limits.html
- RDS Proxy docs: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/rds-proxy.html
- Original configuration source: Japanese article about 100-server high-traffic setup (misapplied to our architecture)
