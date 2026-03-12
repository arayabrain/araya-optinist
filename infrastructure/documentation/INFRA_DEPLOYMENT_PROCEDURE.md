# Infrastructure Deployment Procedure

> See also: [Architecture](TERRAFORM_ARCHITECTURE.md) | [Security](INFRA_SECURITY_MODEL.md)

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

## Working with Production

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

---

## Working with Development

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

This destroys all AWS resources but **preserves**: S3 tfstate bucket, Firebase project, Stripe config, tfvars files, ECR images.

To recreate later, simply run `terraform apply` again — no first-time setup needed.

```bash
# DANGER: Destroy production — requires explicit confirmation
terraform init -backend-config=backends/production.hcl -reconfigure
terraform destroy -var-file=environments/production.tfvars
```

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

This URL is auto-generated by AWS and changes if you destroy and recreate the environment. After first apply, update `frontend_domain` in your `development.tfvars` with this value, then run `terraform apply` again so the ECS task definitions have the correct `FRONTEND_SERVER_HOST` for Stripe callbacks and CORS.

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
