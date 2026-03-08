# Terraform Multi-Environment Guide

## Executive Summary

- **Single parameterized codebase** manages both production and development AWS environments
- **Separate S3 backends** per environment ensure complete state isolation — no accidental cross-environment destruction
- **Variable-driven configuration** controls resource naming, VPC CIDRs, domain/SSL settings, and scaling parameters
- **Conditional resources** (Route53, ACM, HTTPS) are enabled for production and disabled for development
- **Provider `default_tags`** automatically tag every AWS resource with `Environment`, `ManagedBy`, and `Project`

---

## Directory Structure

```
infrastructure/terraform/
├── main.tf                      # Provider, variables, locals, data sources, outputs
├── infrastructure.tf            # VPC, subnets, NAT, S3, EFS, RDS, RDS Proxy
├── compute.tf                   # ALB, ECS cluster, ASG, launch templates, services, task definitions
├── compute_domain.tf            # Route53, ACM certificate (conditional — production only)
├── security.tf                  # IAM roles/policies, security groups, key pairs, Secrets Manager
├── monitoring.tf                # CloudWatch log groups, alarms, dashboard
├── deployment.tf                # SSM document for app_setup.sh deployment
├── background_service.tf        # Background job ECS service and task definition
├── premium_manager.tf           # Premium tier Lambda functions and scheduling
├── free_manager.tf              # Free tier Lambda functions and scheduling
├── common_user_manager.tf       # Shared user lifecycle Lambda function
├── lambda_layers.tf             # Shared Lambda layer (aws_constants)
│
├── backends/
│   ├── production.hcl           # S3 backend config → subscr-optinist-for-cloud-tfstate
│   └── development.hcl          # S3 backend config → development-optinist-for-cloud-tfstate
│
├── environments/
│   ├── production.tfvars        # Production variable values
│   └── development.tfvars       # Development variable values (placeholders for secrets)
│
├── *_package/                   # Lambda function source code directories
│   ├── premium_manager_package/
│   ├── free_manager_package/
│   ├── common_user_manager_package/
│   ├── free_cleanup_package/
│   └── cost_tracker_package/
│
└── .terraform/                  # Auto-generated — provider plugins, state config (gitignored)
```

---

## Key Variables

These variables control environment-specific behavior:

| Variable | Type | Description | Production | Development |
|----------|------|-------------|------------|-------------|
| `environment` | string | Resource name prefix | `"subscr"` | `"development"` |
| `enable_custom_domain` | bool | Toggle Route53/ACM/HTTPS | `true` | `false` |
| `vpc_cidr` | string | VPC CIDR block | `"10.1.0.0/16"` | `"10.2.0.0/16"` |
| `s3_user_bucket_prefix` | string | Per-user S3 bucket IAM wildcard | `"optinist-user"` | `"development-optinist-user"` |
| `frontend_domain` | string | Custom domain | `"araya-optinist.com"` | `""` (uses ALB DNS) |
| `frontend_protocol` | string | HTTP or HTTPS | `"https"` | `"http"` |
| `frontend_port` | string | Listener port | `"443"` | `"80"` |
| `asg_max_size` | number | Max ASG instances | `3` | `2` |

### How Resource Names Are Generated

```
locals {
  env_prefix = "${var.environment}-optinist"
}

# Examples:
# Production: subscr-optinist-app-storage, subscr-optinist-cloud-ecs-cluster
# Development: development-optinist-app-storage, development-optinist-cloud-ecs-cluster
```

---

## How-To Guide

### Prerequisites

```bash
# Install Terraform (v1.0+)
brew install terraform

# Install AWS CLI
brew install awscli

# Configure AWS credentials
aws configure
# Region: ap-northeast-1
# Access Key / Secret Key: (from team lead)
```

### First-Time Setup

Before deploying any environment, the S3 state bucket must exist:

```bash
# Create the state bucket (one-time, per environment)
aws s3 mb s3://subscr-optinist-for-cloud-tfstate --region ap-northeast-1         # production
aws s3 mb s3://development-optinist-for-cloud-tfstate --region ap-northeast-1    # development
```

---

### Working with Production

```bash
cd infrastructure/terraform

# 1. Initialize — connects to production state bucket
terraform init -backend-config=backends/production.hcl

# 2. Preview changes
terraform plan -var-file=environments/production.tfvars

# 3. Apply changes
terraform apply -var-file=environments/production.tfvars

# 4. View outputs (ALB DNS, RDS endpoint, etc.)
terraform output
```

### Working with Development

```bash
cd infrastructure/terraform

# 1. Initialize — connects to development state bucket
terraform init -backend-config=backends/development.hcl

# 2. Fill in placeholders in development.tfvars
#    Replace all <PLACEHOLDER> values with real secrets:
#    - MySQL passwords (generate new ones)
#    - Stripe TEST mode keys
#    - Firebase config (separate dev Firebase project)
#    - OptiNiSt secret keys (generate new ones)

# 3. Preview what will be created
terraform plan -var-file=environments/development.tfvars

# 4. Deploy the development environment
terraform apply -var-file=environments/development.tfvars

# 5. After first apply — note the ALB DNS name from output:
terraform output alb_dns_name
# Update frontend_domain in development.tfvars with this value, then:
terraform apply -var-file=environments/development.tfvars
```

### Switching Between Environments

**Important**: You must re-initialize when switching environments. This is a safety feature — it prevents accidentally modifying the wrong environment.

```bash
# Switch from dev → production
terraform init -backend-config=backends/production.hcl -reconfigure

# Switch from production → dev
terraform init -backend-config=backends/development.hcl -reconfigure
```

The `-reconfigure` flag tells Terraform to switch backends without migrating state.

### Destroying an Environment

```bash
# Destroy development (safe — only affects dev state)
terraform init -backend-config=backends/development.hcl -reconfigure
terraform destroy -var-file=environments/development.tfvars

# DANGER: Destroy production — requires explicit confirmation
terraform init -backend-config=backends/production.hcl -reconfigure
terraform destroy -var-file=environments/production.tfvars
```

---

## Environment Differences

### What Changes Between Production and Development

| Aspect | Production (`subscr`) | Development (`development`) |
|--------|----------------------|----------------------------|
| **VPC CIDR** | `10.1.0.0/16` | `10.2.0.0/16` |
| **Custom domain** | `araya-optinist.com` (HTTPS) | ALB DNS name (HTTP) |
| **Route53 / ACM** | Created | Not created |
| **ALB HTTP listener** | Redirects to HTTPS (301) | Forwards to target group |
| **ALB HTTPS listener** | Port 443, TLS 1.3 | Port 8080, plain HTTP |
| **ASG max instances** | 3 | 2 |
| **S3 state bucket** | `subscr-optinist-for-cloud-tfstate` | `development-optinist-for-cloud-tfstate` |
| **Stripe keys** | Live mode | Test mode |
| **Firebase project** | Production project | Separate dev project |
| **Resource name prefix** | `subscr-optinist-*` | `development-optinist-*` |

### What Stays the Same

- AWS region (`ap-northeast-1`)
- ECR repository (shared between environments)
- Architecture (VPC, subnets, NAT, RDS, ECS, ALB, Lambda functions)
- Lambda function code and scheduling intervals
- Security group rules structure
- Monitoring alarms and dashboard layout

---

## Safety Model

### Why Accidental Cross-Environment Destruction Is Impossible

Each environment has its own S3 backend (state bucket). When you run `terraform init -backend-config=backends/development.hcl`, Terraform physically connects to the development state bucket and **cannot see** production resources.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        terraform/ (code)                            │
│                                                                     │
│   ┌──────────────────────┐       ┌──────────────────────┐          │
│   │ backends/             │       │ environments/         │          │
│   │  production.hcl ─────┼──┐    │  production.tfvars    │          │
│   │  development.hcl ────┼──┼─┐  │  development.tfvars   │          │
│   └──────────────────────┘  │ │  └──────────────────────┘          │
└─────────────────────────────┼─┼────────────────────────────────────┘
                              │ │
                    ┌─────────┘ └─────────┐
                    ▼                     ▼
        ┌───────────────────┐  ┌───────────────────┐
        │ S3: subscr-       │  │ S3: development-   │
        │ optinist-for-     │  │ optinist-for-      │
        │ cloud-tfstate     │  │ cloud-tfstate      │
        │                   │  │                    │
        │ (production       │  │ (development       │
        │  state only)      │  │  state only)       │
        └───────────────────┘  └───────────────────┘
                 │                       │
                 ▼                       ▼
        ┌───────────────────┐  ┌───────────────────┐
        │ AWS Resources:    │  │ AWS Resources:     │
        │ subscr-optinist-* │  │ development-       │
        │                   │  │ optinist-*         │
        └───────────────────┘  └───────────────────┘
```

### Identifying Resources in AWS Console

All resources are tagged automatically via `default_tags`:

| Tag | Value | Purpose |
|-----|-------|---------|
| `Environment` | `subscr` or `development` | Identify which environment owns the resource |
| `ManagedBy` | `terraform` | Distinguish Terraform-managed vs manually-created resources |
| `Project` | `optinist-cloud` | Filter across all OptiNiSt resources |

**Filter in AWS Console**: Use `Environment = development` to see only dev resources.

**Filter via CLI**:
```bash
# List only development EC2 instances
aws ec2 describe-instances \
  --filters "Name=tag:Environment,Values=development" \
  --query "Reservations[].Instances[].[InstanceId,Tags[?Key=='Name'].Value|[0]]" \
  --output table

# List only production S3 buckets by prefix
aws s3 ls | grep subscr-optinist
```

---

## Common Tasks

### View Current Terraform State

```bash
# List all resources in current environment's state
terraform state list

# Show details of a specific resource
terraform state show aws_ecs_cluster.main
```

### View Outputs

```bash
# All outputs
terraform output

# Specific output
terraform output alb_dns_name
terraform output rds_endpoint

# Sensitive output
terraform output -raw optinist_cloud_user_secret_access_key
```

### Update a Single Resource

```bash
# Target a specific resource (e.g., after changing only the Lambda code)
terraform apply -var-file=environments/development.tfvars -target=aws_lambda_function.free_manager
```

### Import an Existing Resource

```bash
# If a resource was created manually and needs to be managed by Terraform
terraform import -var-file=environments/development.tfvars aws_s3_bucket.app_storage development-optinist-app-storage
```

### Check Which Environment You're Connected To

```bash
# The backend config tells you which state bucket is active
cat .terraform/terraform.tfstate | python3 -c "import sys,json; print(json.load(sys.stdin)['backend']['config']['bucket'])"
```

---

## Troubleshooting

### "Backend configuration changed" error

This happens when switching environments. Use `-reconfigure`:

```bash
terraform init -backend-config=backends/development.hcl -reconfigure
```

### "Error acquiring state lock"

Another Terraform process is running against this environment's state:

```bash
# Check who holds the lock
aws dynamodb scan --table-name terraform-state-lock --region ap-northeast-1

# Force unlock (only if you're sure no other process is running)
terraform force-unlock <LOCK_ID>
```

### "Resource already exists" error

The resource was created outside Terraform. Import it:

```bash
terraform import -var-file=environments/<env>.tfvars <resource_address> <resource_id>
```

### Provider plugin errors during validate

Re-initialize providers:

```bash
terraform init -backend=false    # Just install plugins, skip backend
terraform validate
```

### Development tfvars placeholders

Before first `terraform apply` on development, replace all `<PLACEHOLDER>` values in `environments/development.tfvars`:

| Placeholder | How to Generate |
|-------------|-----------------|
| MySQL passwords | `openssl rand -base64 24` |
| `optinist_secret_key` | `openssl rand -hex 32` |
| `routing_secret_key` | `openssl rand -hex 32` |
| Stripe TEST keys | From Stripe Dashboard → Developers → API Keys (test mode) |
| Firebase config | From Firebase Console → Project Settings → Web app config |
| Firebase private key | From Firebase Console → Service Accounts → Generate new private key |
| `optinist_admin_uid` | Create a test user in Firebase Auth, copy their UID |

---

## AWS Resources Created Per Environment

| Category | Resources | Named As |
|----------|-----------|----------|
| **Networking** | VPC, 2 public + 2 private subnets, IGW, NAT instances, route tables | `${env}-optinist-cloud-*` |
| **Compute** | ECS cluster, ASG, launch template, 2 ECS services (free + premium), EC2 premium instances | `${env}-optinist-cloud-*` |
| **Load Balancing** | ALB, target groups, listeners | `${env}-optinist-lb`, `${env}-optinist-tg` |
| **Database** | RDS MySQL, RDS Proxy, subnet group | `${env}-optinist-rds-*` |
| **Storage** | S3 bucket, EFS filesystem | `${env}-optinist-app-storage`, `${env}-optinist-cloud-snmk-volume` |
| **Lambda** | 5 functions (premium-manager, free-manager, common-user-manager, free-cleanup, cost-tracker) | `${env}-premium-manager`, `${env}-free-manager`, etc. |
| **Secrets** | 5 Secrets Manager secrets (firebase, database, app, stripe config) | `${env}-optinist/firebase/config`, etc. |
| **Monitoring** | CloudWatch log groups, alarms, dashboard | `${env}-optinist-*` |
| **DNS/SSL** | Route53 zone, ACM certificate (production only) | `araya-optinist.com` |
| **IAM** | Task execution role, task role, instance role, IAM user, Lambda roles | `${env}-optinist-*`, `${env}-*` |
| **Background** | Background ECS service, launch template, ASG | `${env}-optinist-background-*` |
