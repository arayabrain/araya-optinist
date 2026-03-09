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
│   ├── production.tfvars.example    # Production variable template (secrets redacted)
│   ├── development.tfvars.example   # Development variable template (placeholders)
│   ├── production.tfvars            # Actual production values (gitignored, stored in Google Drive)
│   └── development.tfvars           # Actual development values (gitignored, stored in Google Drive)
│
├── *_package/                   # Lambda function source code directories
│
└── .terraform/                  # Auto-generated — provider plugins, state config (gitignored)
```

### Shared Resources (already exist, not created by Terraform)

The following resources are **pre-existing** and shared across environments. They are **not created or destroyed** by `terraform apply` / `terraform destroy`:

| Resource                    | Status          | Location / Notes                                                                                                                                                 |
| --------------------------- | --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **S3 state buckets**        | Already created | `subscr-optinist-for-cloud-tfstate` (prod), `development-optinist-for-cloud-tfstate` (dev). These persist across all testing rounds to preserve Terraform state. |
| **ECR repository**          | Already created | `637423646530.dkr.ecr.ap-northeast-1.amazonaws.com/optinist-for-cloud` — shared between prod and dev                                                             |
| **Firebase project (prod)** | Already created | Production Firebase project in [Firebase Console](https://console.firebase.google.com/)                                                                          |
| **Firebase project (dev)**  | Already created | Separate development Firebase project. Config stored in `development.tfvars`                                                                                     |
| **Stripe account**          | Already created | Test mode keys for dev, live mode keys for prod. Keys in [Stripe Dashboard](https://dashboard.stripe.com/)                                                       |
| **tfvars files**            | Already created | Stored in Google Drive: [https://drive.google.com/drive/folders/1FBIAqBjIdzkXCvNKKGgKu17J4-O3dX-F]. Download to `environments/` before running Terraform         |

---

## What Gets Destroyed vs What Persists

When you run `terraform destroy`, only the AWS resources managed by Terraform are removed. External dependencies and configuration files persist:

| Destroyed by `terraform destroy`         | NOT destroyed (persists)                                      |
| ---------------------------------------- | ------------------------------------------------------------- |
| VPC, subnets, route tables               | S3 tfstate bucket (keeps your state history)                  |
| EC2 instances (NAT, ASG, premium)        | ECR repository and Docker images                              |
| RDS instance and RDS Proxy               | Firebase project and user accounts                            |
| ALB, target groups, listeners            | Stripe products, prices, and webhook configs                  |
| ECS cluster, services, task definitions  | `*.tfvars` files (local files on your machine / Google Drive) |
| S3 app storage bucket                    | `backends/*.hcl` files (checked into git)                     |
| EFS filesystem                           | `*.tfvars.example` files (checked into git)                   |
| Lambda functions and layers              | AWS IAM user credentials (if saved locally)                   |
| Secrets Manager secrets                  |                                                               |
| CloudWatch log groups, alarms, dashboard |                                                               |
| IAM roles, policies, user                |                                                               |
| Security groups                          |                                                               |
| Route53/ACM (production only)            |                                                               |

**Important**: The S3 tfstate bucket is **never destroyed** by `terraform destroy`. It is kept permanently so you can track state history and re-create the environment. If you ever need to remove it, do so manually with `aws s3 rb s3://development-optinist-for-cloud-tfstate --force`.

---

## Key Variables

These variables control environment-specific behavior:

| Variable                | Type   | Description                     | Production             | Development                   |
| ----------------------- | ------ | ------------------------------- | ---------------------- | ----------------------------- |
| `environment`           | string | Resource name prefix            | `"subscr"`             | `"development"`               |
| `enable_custom_domain`  | bool   | Toggle Route53/ACM/HTTPS        | `true`                 | `false`                       |
| `vpc_cidr`              | string | VPC CIDR block                  | `"10.1.0.0/16"`        | `"10.2.0.0/16"`               |
| `s3_user_bucket_prefix` | string | Per-user S3 bucket IAM wildcard | `"optinist-user"`      | `"development-optinist-user"` |
| `frontend_domain`       | string | Custom domain                   | `"araya-optinist.com"` | `""` (uses ALB DNS)           |
| `frontend_protocol`     | string | HTTP or HTTPS                   | `"https"`              | `"http"`                      |
| `frontend_port`         | string | Listener port                   | `"443"`                | `"80"`                        |
| `asg_max_size`          | number | Max ASG instances               | `3`                    | `2`                           |

### How Resource Names Are Generated

```hcl
locals {
  env_prefix = "${var.environment}-optinist"
}

# Examples:
# Production: subscr-optinist-app-storage, subscr-optinist-cloud-ecs-cluster
# Development: development-optinist-app-storage, development-optinist-cloud-ecs-cluster
```

---

## How `enable_custom_domain` Works

The `enable_custom_domain` variable controls whether Route53, ACM (SSL certificate), and HTTPS are set up. This has a significant impact on how the application is accessed.

### Production (`enable_custom_domain = true`)

```
User → https://araya-optinist.com
         │
         ▼
    Route53 (DNS)
         │  A record → ALB
         ▼
    ALB Listener (port 443, HTTPS)
         │  SSL terminated with ACM certificate
         │  TLS 1.3
         ▼
    ECS Target Group (port 8000)

    ALB Listener (port 80, HTTP)
         │  Redirects to HTTPS (301)
         ▼
    https://araya-optinist.com
```

**Resources created**: Route53 hosted zone, ACM certificate, DNS validation records, A records (apex + www), HTTPS listener with SSL.

### Development (`enable_custom_domain = false`)

```
User → http://<ALB-DNS-NAME>
         │
         ▼
    ALB Listener (port 80, HTTP)
         │  Forwards directly to target group (no redirect)
         ▼
    ECS Target Group (port 8000)

    ALB Listener (port 8080, HTTP)
         │  Secondary listener, also forwards to target group
         ▼
    ECS Target Group (port 8000)
```

**Resources NOT created**: No Route53, no ACM certificate, no DNS records, no SSL. The ALB uses plain HTTP only.

### How to Access the Development Site

After `terraform apply`, get the ALB DNS name:

```bash
# Get the ALB DNS name from Terraform outputs
terraform output alb_dns_name

# Example output:
# development-optinist-lb-1234567890.ap-northeast-1.elb.amazonaws.com
```

Access the development site at:

```
http://<ALB-DNS-NAME>
```

This URL is auto-generated by AWS and changes if you destroy and recreate the environment. After first apply, update `frontend_domain` in your `development.tfvars` with this value, then run `terraform apply` again so the ECS task definitions have the correct `FRONTEND_SERVER_HOST` for Stripe callbacks and CORS.

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

### First-Time Setup (One-Time Only)

The following steps only need to be done **once**. If another developer has already completed them, skip to "Working with Development".

#### 1. Create S3 bucket for Terraform state

> **Note**: These buckets already exist. This step is only needed if setting up a completely new environment from scratch. The state buckets are **never destroyed** — they persist across all testing rounds.

```bash
# Production state bucket (already exists)
aws s3api create-bucket \
  --bucket subscr-optinist-for-cloud-tfstate \
  --region ap-northeast-1 \
  --create-bucket-configuration LocationConstraint=ap-northeast-1

# Development state bucket (already exists)
aws s3api create-bucket \
  --bucket development-optinist-for-cloud-tfstate \
  --region ap-northeast-1 \
  --create-bucket-configuration LocationConstraint=ap-northeast-1
```

#### 2. Create a separate Firebase project (development only)

> **Note**: The development Firebase project already exists. Config is stored in the shared `development.tfvars` on Google Drive.

If creating from scratch:

1. Create a new Firebase project in the [Firebase Console](https://console.firebase.google.com/)
2. Enable Authentication (Email/Password provider)
3. Generate a service account key (Project Settings → Service Accounts → Generate new private key)
4. Update `firebase_config_json` and `firebase_private_json` in `environments/development.tfvars`

#### 3. Set up Stripe test mode (development only)

> **Note**: Stripe test mode is already configured. Keys are stored in the shared `development.tfvars` on Google Drive.

If creating from scratch:

1. In the [Stripe Dashboard](https://dashboard.stripe.com/), toggle to "Test mode"
2. Create test products and prices matching the production structure
3. Create a test webhook endpoint pointing to the test ALB
4. Update `stripe_secret_key`, `stripe_webhook_secret`, and plan IDs in `environments/development.tfvars`

#### 4. Get tfvars files

Download the tfvars files from Google Drive and place them in the `environments/` directory:

```bash
# Download from Google Drive: [https://drive.google.com/drive/folders/1FBIAqBjIdzkXCvNKKGgKu17J4-O3dX-F]
# Place files at:
#   infrastructure/terraform/environments/production.tfvars
#   infrastructure/terraform/environments/development.tfvars
```

If the tfvars files don't exist yet, create them from the examples:

```bash
cd infrastructure/terraform/environments
cp development.tfvars.example development.tfvars
# Edit development.tfvars — replace all <PLACEHOLDER> values
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

# 2. Ensure development.tfvars exists (download from Google Drive or copy from example)

# 3. Preview what will be created
terraform plan -var-file=environments/development.tfvars

# 4. Deploy the development environment
terraform apply -var-file=environments/development.tfvars

# 5. Get the development site URL:
terraform output alb_dns_name
# Access at: http://<ALB-DNS-NAME>

# 6. After first apply — update frontend_domain in development.tfvars
#    with the ALB DNS name, then apply again for correct CORS/callback URLs:
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

### Destroying the Development Environment

```bash
# Destroy development (safe — only affects dev state)
terraform init -backend-config=backends/development.hcl -reconfigure
terraform destroy -var-file=environments/development.tfvars
```

This destroys all AWS resources but **preserves**: S3 tfstate bucket, Firebase project, Stripe config, tfvars files, ECR images.

To recreate later, simply run `terraform apply` again — no first-time setup needed.

```bash
# DANGER: Destroy production — requires explicit confirmation
terraform init -backend-config=backends/production.hcl -reconfigure
terraform destroy -var-file=environments/production.tfvars
```

---

## Environment Differences

### What Changes Between Production and Development

| Aspect                          | Production (`subscr`)               | Development (`development`)                                   |
| ------------------------------- | ----------------------------------- | ------------------------------------------------------------- |
| **Access URL**                  | `https://araya-optinist.com`        | `http://<ALB-DNS-NAME>` (see `terraform output alb_dns_name`) |
| **VPC CIDR**                    | `10.1.0.0/16`                       | `10.2.0.0/16`                                                 |
| **Custom domain**               | `araya-optinist.com` (HTTPS)        | ALB DNS name (HTTP)                                           |
| **Route53 / ACM**               | Created                             | Not created (`enable_custom_domain = false`)                  |
| **ALB HTTP listener (port 80)** | Redirects to HTTPS (301)            | Forwards directly to target group                             |
| **ALB HTTPS listener**          | Port 443, TLS 1.3, ACM cert         | Port 8080, plain HTTP, no cert                                |
| **ASG max instances**           | 3                                   | 2                                                             |
| **S3 state bucket**             | `subscr-optinist-for-cloud-tfstate` | `development-optinist-for-cloud-tfstate`                      |
| **Stripe keys**                 | Live mode                           | Test mode                                                     |
| **Firebase project**            | Production project                  | Separate dev project                                          |
| **Resource name prefix**        | `subscr-optinist-*`                 | `development-optinist-*`                                      |

### Subnet CIDRs

Subnets are derived automatically via `cidrsubnet(var.vpc_cidr, 4, N)`:

| Subnet    | Production      | Development     |
| --------- | --------------- | --------------- |
| VPC       | `10.1.0.0/16`   | `10.2.0.0/16`   |
| Public 1  | `10.1.0.0/20`   | `10.2.0.0/20`   |
| Public 2  | `10.1.16.0/20`  | `10.2.16.0/20`  |
| Private 1 | `10.1.128.0/20` | `10.2.128.0/20` |
| Private 2 | `10.1.144.0/20` | `10.2.144.0/20` |

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

| Tag           | Value                     | Purpose                                                     |
| ------------- | ------------------------- | ----------------------------------------------------------- |
| `Environment` | `subscr` or `development` | Identify which environment owns the resource                |
| `ManagedBy`   | `terraform`               | Distinguish Terraform-managed vs manually-created resources |
| `Project`     | `optinist-cloud`          | Filter across all OptiNiSt resources                        |

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

| Placeholder           | How to Generate                                                     |
| --------------------- | ------------------------------------------------------------------- |
| MySQL passwords       | `openssl rand -base64 24`                                           |
| `optinist_secret_key` | `openssl rand -hex 32`                                              |
| `routing_secret_key`  | `openssl rand -hex 32`                                              |
| Stripe TEST keys      | From Stripe Dashboard → Developers → API Keys (test mode)           |
| Firebase config       | From Firebase Console → Project Settings → Web app config           |
| Firebase private key  | From Firebase Console → Service Accounts → Generate new private key |
| `optinist_admin_uid`  | Create a test user in Firebase Auth, copy their UID                 |

---

## AWS Resources Created Per Environment

| Category           | Resources                                                                                    | Named As                                                           |
| ------------------ | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| **Networking**     | VPC, 2 public + 2 private subnets, IGW, NAT instances, route tables                          | `${env}-optinist-cloud-*`                                          |
| **Compute**        | ECS cluster, ASG, launch template, 2 ECS services (free + premium), EC2 premium instances    | `${env}-optinist-cloud-*`                                          |
| **Load Balancing** | ALB, target groups, listeners                                                                | `${env}-optinist-lb`, `${env}-optinist-tg`                         |
| **Database**       | RDS MySQL, RDS Proxy, subnet group                                                           | `${env}-optinist-rds-*`                                            |
| **Storage**        | S3 bucket, EFS filesystem                                                                    | `${env}-optinist-app-storage`, `${env}-optinist-cloud-snmk-volume` |
| **Lambda**         | 5 functions (premium-manager, free-manager, common-user-manager, free-cleanup, cost-tracker) | `${env}-premium-manager`, `${env}-free-manager`, etc.              |
| **Secrets**        | 5 Secrets Manager secrets (firebase, database, app, stripe config)                           | `${env}-optinist/firebase/config`, etc.                            |
| **Monitoring**     | CloudWatch log groups, alarms, dashboard                                                     | `${env}-optinist-*`                                                |
| **DNS/SSL**        | Route53 zone, ACM certificate (production only)                                              | `araya-optinist.com`                                               |
| **IAM**            | Task execution role, task role, instance role, IAM user, Lambda roles                        | `${env}-optinist-*`, `${env}-*`                                    |
| **Background**     | Background ECS service, launch template, ASG                                                 | `${env}-optinist-background-*`                                     |

---

## Development Environment Lifecycle

### When to Create / Destroy

| Scenario                     | Action                                         |
| ---------------------------- | ---------------------------------------------- |
| Testing a new release        | `terraform apply` → test → `terraform destroy` |
| PR review with infra changes | Create, test, destroy                          |
| Long-running QA              | Keep alive, destroy when done                  |
| Cost concern                 | Always destroy when not in use                 |

### Cost Considerations

The development environment runs the same infrastructure as production (VPC, RDS, ECS, ALB, NAT instances, Lambda functions). **Destroy it when not in use** to avoid unnecessary costs. Key cost drivers:

- **RDS instance** — runs 24/7 while the environment is up
- **NAT instances** — 2x t3.nano running continuously
- **EC2 instances** — ASG maintains at least 1 instance
- **ALB** — hourly charge while provisioned

See `DEV_ENVIRONMENT_COST_ESTIMATE.md` for a detailed monthly cost breakdown (~$286/month if running 24/7).
