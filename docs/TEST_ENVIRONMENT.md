# Test Environment for OptiNiSt Cloud

A parallel test environment using a separate Terraform directory (`infrastructure/terraform-dev/`).
All AWS resource names use `test-optinist` prefix instead of `subscr-optinist`.
Production terraform files in `infrastructure/terraform/` remain untouched.

## Architecture

```
infrastructure/
├── terraform/              ← Production (untouched)
│   ├── main.tf
│   ├── compute.tf
│   ├── ...
│   └── terraform.tfvars
│
├── terraform-dev/         ← Test environment
│   ├── main.tf             ← All "subscr" → "test"
│   ├── compute.tf
│   ├── ...
│   └── terraform.tfvars    ← Test-specific config (placeholders)
│
└── scripts/                ← Shared (parameterized with ENV_PREFIX)
    ├── app_setup.sh
    └── ecs-user-data.sh
```

### Key Differences from Production

| Setting | Production | Test |
|---------|-----------|------|
| Resource prefix | `subscr-optinist` | `development-optinist` |
| VPC CIDR | `10.1.0.0/16` | `10.2.0.0/16` |
| S3 tfstate bucket | `subscr-optinist-for-cloud-tfstate` | `development-optinist-for-cloud-tfstate` |
| Stripe keys | Live mode (`sk_live_...`) | Test mode (`sk_test_...`) |
| Firebase project | Production project | Separate test project |
| Frontend domain | `araya-optinist.com` (HTTPS) | ALB DNS directly (HTTP) |
| ASG size | min=1, max=3 | min=1, max=2 |

### Subnet CIDRs

| Subnet | Production | Test |
|--------|-----------|------|
| VPC | `10.1.0.0/16` | `10.2.0.0/16` |
| Public 1 | `10.1.0.0/20` | `10.2.0.0/20` |
| Public 2 | `10.1.16.0/20` | `10.2.16.0/20` |
| Private 1 | `10.1.128.0/20` | `10.2.128.0/20` |
| Private 2 | `10.1.144.0/20` | `10.2.144.0/20` |

## Bootstrap (One-Time Setup)

### 1. Create S3 bucket for Terraform state

```bash
aws s3api create-bucket \
  --bucket development-optinist-for-cloud-tfstate \
  --region ap-northeast-1 \
  --create-bucket-configuration LocationConstraint=ap-northeast-1
```

### 2. Create a separate Firebase project

Create a new Firebase project in the [Firebase Console](https://console.firebase.google.com/) for test use.
Update `firebase_config_json` and `firebase_private_json` in `terraform.tfvars`.

### 3. Set up Stripe test mode

1. In the [Stripe Dashboard](https://dashboard.stripe.com/), toggle to "Test mode"
2. Create test products and prices matching the production structure
3. Create a test webhook endpoint pointing to the test ALB
4. Update `stripe_secret_key`, `stripe_webhook_secret`, and plan IDs in `terraform.tfvars`

### 4. Fill in terraform.tfvars

Replace all `<PLACEHOLDER>` values in `infrastructure/terraform-dev/terraform.tfvars`:
- Generate new database passwords
- Add Firebase project config and service account
- Add Stripe test mode keys
- Add test admin Firebase UID
- Generate new `optinist_secret_key` and `routing_secret_key`

## Create Test Environment

```bash
git checkout test-environment
cd infrastructure/terraform-dev
terraform init
terraform plan -out=test.plan
terraform apply test.plan
```

## Destroy Test Environment

```bash
cd infrastructure/terraform-dev
terraform destroy
```

## Sync Changes from Production Terraform

When production terraform is updated on `develop-subscription`:

```bash
git checkout test-environment
git merge develop-subscription

# If infrastructure/terraform/ files changed, manually apply
# the same changes to infrastructure/terraform-dev/:

# 1. See what changed in production terraform
git diff HEAD~1 -- infrastructure/terraform/

# 2. Apply equivalent changes to terraform-dev/
#    (same logic, but with "test-" prefix instead of "subscr-")

# 3. Test
cd infrastructure/terraform-dev
terraform plan   # Review changes
terraform apply
```

### Detecting Drift

Compare structure (ignoring prefix differences):

```bash
diff <(sed 's/subscr/test/g; s/10\.1\./10.2./g' infrastructure/terraform/compute.tf) \
     infrastructure/terraform-dev/compute.tf
```

## Shared Scripts

`infrastructure/scripts/app_setup.sh` is shared between both environments via the
`ENV_PREFIX` variable:

- **Production**: `ENV_PREFIX` defaults to `subscr` (no change needed)
- **Development**: SSM document passes `ENV_PREFIX=development` before running the script

`infrastructure/scripts/ecs-user-data.sh` already uses `templatefile` variables
and requires no changes.

## When to Create / Destroy

| Scenario | Action |
|----------|--------|
| Testing a new release | `terraform apply` → test → `terraform destroy` |
| PR review with infra changes | Create, test, destroy |
| Long-running QA | Keep alive, destroy when done |
| Cost concern | Always destroy when not in use |

## Cost Estimate

The test environment runs the same infrastructure as production.
Destroy it when not in use to avoid unnecessary costs.
