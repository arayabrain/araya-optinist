# Infrastructure Deployment Procedure

> See also: [Deployment Procedure](DEPLOYMENT_PROCEDURE.md) | [Architecture](TERRAFORM_ARCHITECTURE.md) | [Security](INFRA_SECURITY_MODEL.md) | [Dev Schedule](../scripts/DEV_SCHEDULE_GUIDE.md)

---

## Prerequisites

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

---

## First-Time Setup (One-Time Only)

The following steps only need to be done **once**. If another developer has already completed them, skip to "Working with Development".

### 1. Create S3 bucket for Terraform state

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

### 2. Create a separate Firebase project (development only)

> **Note**: The development Firebase project already exists. Config is stored in the shared `development.tfvars` on Google Drive.

If creating from scratch:

1. Create a new Firebase project in the [Firebase Console](https://console.firebase.google.com/)
2. Enable Authentication (Email/Password provider)
3. Generate a service account key (Project Settings → Service Accounts → Generate new private key)
4. Update `firebase_config_json` and `firebase_private_json` in `environments/development.tfvars`

### 3. Set up Stripe test mode (development only)

> **Note**: Stripe test mode is already configured. Keys are stored in the shared `development.tfvars` on Google Drive.

If creating from scratch:

1. In the [Stripe Dashboard](https://dashboard.stripe.com/), toggle to "Test mode"
2. Create test products and prices matching the production structure
3. Create a test webhook endpoint pointing to the test ALB
4. Update `stripe_secret_key`, `stripe_webhook_secret`, and plan IDs in `environments/development.tfvars`

### 4. Get tfvars files

Download the tfvars files from Google Drive and place them in the `environments/` directory:

```bash
# Download from Google Drive: [https://drive.google.com/drive/folders/1xUsptIrcWYMAgeqNraRzObG_CUqMXHZh]
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

## Working with Production

```bash
cd infrastructure/terraform

# 1. Initialize — connects to production state bucket
#    Use -reconfigure if you were previously initialized to a different environment
terraform init -backend-config=backends/production.hcl -reconfigure

# 2. Preview changes
terraform plan -var-file=environments/production.tfvars

# 3. Apply changes
terraform apply -var-file=environments/production.tfvars

# 4. View outputs (ALB DNS, RDS endpoint, etc.)
terraform output
```

---

## Working with Development

```bash
cd infrastructure/terraform

# 1. Initialize — connects to development state bucket
#    Use -reconfigure if you were previously initialized to a different environment
terraform init -backend-config=backends/development.hcl -reconfigure

# 2. Ensure development.tfvars exists (download from Google Drive or copy from example)

# 3. Preview what will be created
terraform plan -var-file=environments/development.tfvars

# 4. Deploy the development environment
terraform apply -var-file=environments/development.tfvars

# 5. Get the development site URL:
terraform output alb_dns_name
# Access at: http://<ALB-DNS-NAME> (redirects to port 8080)
```

---

## Switching Between Environments

**Important**: You must re-initialize when switching environments. This is a safety feature — it prevents accidentally modifying the wrong environment.

```bash
# Switch from dev → production
terraform init -backend-config=backends/production.hcl -reconfigure

# Switch from production → dev
terraform init -backend-config=backends/development.hcl -reconfigure
```

The `-reconfigure` flag tells Terraform to switch backends without migrating state.

---

## Destroying the Development Environment

```bash
# Destroy development (safe — only affects dev state)
terraform init -backend-config=backends/development.hcl -reconfigure
terraform destroy -var-file=environments/development.tfvars
```

This destroys all AWS resources but **preserves**: S3 tfstate bucket, Firebase project, Stripe config, tfvars files.

> **Note**: The development ECR repository (`development-optinist-for-cloud`) is managed by Terraform with `force_delete = true`, so `terraform destroy` will delete the repository and all images inside it. If you need to preserve images before destroying, push them to another repository first.

To recreate later, simply run `terraform apply` again — no first-time setup needed.

```bash
# DANGER: Destroy production — requires explicit confirmation
terraform init -backend-config=backends/production.hcl -reconfigure
terraform destroy -var-file=environments/production.tfvars
```

---

## ECR Repository Isolation

Production and development use **separate ECR repositories** to ensure complete image isolation:

| Environment | ECR Repository                   | Managed by                                             |
| ----------- | -------------------------------- | ------------------------------------------------------ |
| Production  | `optinist-for-cloud`             | Pre-existing (outside Terraform)                       |
| Development | `development-optinist-for-cloud` | Terraform (created when `ecr_repository_url` is empty) |

Both environments push to `:latest` within their own repo. A Docker push for dev testing **cannot** affect production.

### Building and Pushing Images

The build script reads the target environment from Terraform output, displays a confirmation prompt, and requires explicit approval before pushing:

```bash
cd infrastructure/scripts

# Standard usage — auto-generates version tag, asks for confirmation
./ecr_build_push.sh

# Custom version tag
./ecr_build_push.sh --tag v1.2.3

# Skip confirmation (for CI/CD pipelines)
./ecr_build_push.sh --yes
```

The script will display:

```
============================================
  BUILD AND PUSH CONFIRMATION
============================================
  Environment : development
  ECR Repo    : development-optinist-for-cloud
  Tags        : latest, 20260317-143022-a1b2c3d
============================================

Proceed with build and push? (y/N):
```

For production, an additional **WARNING** banner is shown.

- If initialized to **development** → pushes to `development-optinist-for-cloud:latest`
- If initialized to **production** → pushes to `optinist-for-cloud:latest`

Every push creates **two tags**:

- `:latest` — used by ECS task definitions (always current)
- `:YYYYMMDD-HHMMSS-<git-sha>` — immutable version for history and rollback (e.g., `20260317-143022-a1b2c3d`)

### Safe Deployment Workflow

1. Switch to dev backend: `terraform init -backend-config=backends/development.hcl -reconfigure`
2. Build and push dev image: `cd ../scripts && ./ecr_build_push.sh`
3. Force ECS redeployment: `aws ecs update-service --cluster development-optinist-cloud --service <service-name> --force-new-deployment --region ap-northeast-1`
4. Verify in development
5. When ready for production: switch backend, rebuild, push, and redeploy

### Rollback to a Previous Image

List available image versions:

```bash
aws ecr list-images \
  --repository-name development-optinist-for-cloud \
  --region ap-northeast-1 \
  --query 'imageIds[?imageTag!=`latest`].imageTag' \
  --output table
```

Retag a previous version as `:latest` and redeploy:

```bash
# 1. Get the manifest of the version you want to roll back to
MANIFEST=$(aws ecr batch-get-image \
  --repository-name development-optinist-for-cloud \
  --image-ids imageTag=20260316-091500-f4e5d6a \
  --query 'images[0].imageManifest' --output text \
  --region ap-northeast-1)

# 2. Retag it as :latest
aws ecr put-image \
  --repository-name development-optinist-for-cloud \
  --image-tag latest \
  --image-manifest "$MANIFEST" \
  --region ap-northeast-1

# 3. Force ECS to pull the rolled-back image
aws ecs update-service \
  --cluster development-optinist-cloud \
  --service <service-name> \
  --force-new-deployment \
  --region ap-northeast-1
```

### Image Cleanup

The ECR lifecycle policy automatically manages storage:

- **Untagged images**: removed after 7 days
- **Versioned images**: only the last 10 are kept
- **`:latest`**: always retained

---

## How to Access the Development Site

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

This URL is auto-generated by AWS and changes if you destroy and recreate the environment. Port 80 redirects to port 8080 (the main listener). `FRONTEND_SERVER_HOST` and `FRONTEND_SERVER_PORT` are auto-resolved from the ALB DNS name when `enable_custom_domain = false`.

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
terraform output -raw araya_optinist_cloud_user_secret_access_key
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
