# OptiNiSt Cloud — Application Deployment & Release Guide

This document covers **application code deployment, release procedures, and Git workflow**. Use this guide when deploying code changes (frontend, studio, Lambda) to the AWS environment.

> **For infrastructure management (Terraform operations, environment setup, ECR details)**, see [INFRA_DEPLOYMENT_PROCEDURE.md](INFRA_DEPLOYMENT_PROCEDURE.md).

**Production URL:** `https://araya-optinist.com`

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [AWS Profile Configuration](#aws-profile-configuration)
- [Secrets Manager Architecture](#secrets-manager-architecture)
- [Deployment Workflow](#deployment-workflow)
- [Post-Deployment Verification](#post-deployment-verification)
- [Release Preparation](#release-preparation)
- [Git Workflow and Release Tags](#git-workflow-and-release-tags)
- [Documentation Updates](#documentation-updates)
- [Hotfix Procedure](#hotfix-procedure)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Required Tools

```bash
# Install AWS CLI
# https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html

# Install Docker
# https://docs.docker.com/get-docker/

# Install Terraform (v1.0+) — only needed if deploying infrastructure changes
brew install terraform
```

### Required AWS Permissions

The deploying user needs the following AWS permissions:

- **ECR**: Push images (`ecr:GetAuthorizationToken`, `ecr:BatchCheckLayerAvailability`, `ecr:PutImage`, etc.)
- **ECS**: List and update services (`ecs:ListClusters`, `ecs:ListServices`, `ecs:UpdateService`, etc.)
- **Secrets Manager**: Read secrets (granted automatically to the deployment role)
- **RDS**: Describe instances (for verification)
- **S3**: List buckets (for verification)

---

## AWS Profile Configuration

When your machine has multiple AWS profiles configured, you **must** specify the correct profile before running commands. Without this, commands may execute against the wrong AWS account.

### Setting Up a Named Profile

```bash
aws configure --profile optinist
# AWS Access Key ID:     <your-key>       (obtain from team lead)
# AWS Secret Access Key: <your-secret>    (obtain from team lead)
# Default region name:   ap-northeast-1
# Default output format: json
```

### Activating the Profile

#### Option A: Environment Variable (recommended)

Set `AWS_PROFILE` once per terminal session. All subsequent `aws`, `terraform`, and `ecr_build_push.sh` commands will use this profile automatically.

```bash
export AWS_PROFILE=optinist

# Verify the correct account is active
aws sts get-caller-identity
```

> **Tip:** Add `export AWS_PROFILE=optinist` to your shell configuration (`~/.bashrc`, `~/.zshrc`) if this is your primary AWS project.

#### Option B: Per-Command `--profile` Flag (AWS CLI only)

```bash
aws ecs list-clusters --profile optinist --region ap-northeast-1
```

> **Note:** `terraform` and `ecr_build_push.sh` do not accept `--profile`. They read from the `AWS_PROFILE` environment variable. Always use `export AWS_PROFILE=...` before running these tools.

---

## Secrets Manager Architecture

OptiNiSt uses **AWS Secrets Manager** for credential storage. This enables team members to deploy application code without needing access to Terraform tfvars files.

### How It Works

1. **One-time setup (requires Terraform access):**
   - Run `terraform apply` once to create AWS Secrets Manager secrets
   - Secrets contain: Firebase config, database credentials, application keys, Stripe config
   - Secrets are stored permanently in AWS

2. **Ongoing deployments (no Terraform needed):**
   - Build and push Docker images using `ecr_build_push.sh`
   - The `app_setup.sh` script inside the container automatically:
     - Reads secrets from AWS Secrets Manager
     - Discovers infrastructure (RDS endpoint, S3 buckets) via AWS CLI
     - Configures the application with correct settings

---

## Deployment Workflow

### Determine What Needs to Be Deployed

| What changed                 | Actions needed                                                   |
| ---------------------------- | ---------------------------------------------------------------- |
| Frontend code (`frontend/`)  | `ecr_build_push.sh` → Force ECS redeployment                     |
| Studio code (`studio/`)      | `ecr_build_push.sh` → Force ECS redeployment                     |
| Lambda code (`*_package/`)   | `terraform apply`                                                |
| Infrastructure (`.tf` files) | `terraform apply`                                                |
| Frontend + Infrastructure    | `terraform apply` → `ecr_build_push.sh` → Force ECS redeployment |
| Everything                   | `terraform apply` → `ecr_build_push.sh` → Force ECS redeployment |

> **Important:** When both infrastructure and application code change, run `terraform apply` **before** `ecr_build_push.sh`. The build script reads Terraform outputs (domain, port, protocol) to configure the frontend build.

### Step 1: Apply Infrastructure Changes (if needed)

Skip this step if only `frontend/` or `studio/` code changed.

```bash
export AWS_PROFILE=optinist
cd infrastructure/terraform

# <ENV> = production | development

# Initialize with the correct environment backend
terraform init -backend-config=backends/<ENV>.hcl -reconfigure

# Review changes
terraform plan -var-file=environments/<ENV>.tfvars

# Apply changes
terraform apply -var-file=environments/<ENV>.tfvars
```

**What `terraform apply` updates:**

- Infrastructure (VPC, ALB, RDS, ECS, Auto Scaling, Route53, ACM)
- Lambda function code and layers
- Copies `infrastructure/aws_constants.py` to all Lambda packages via provisioners

> **Note:** The commands above are a quick reference for production deployment. For the authoritative guide — including environment switching, development setup, destroying environments, and Terraform troubleshooting — see [INFRA_DEPLOYMENT_PROCEDURE.md](INFRA_DEPLOYMENT_PROCEDURE.md).

### Step 2: Build and Push Docker Image (if application code changed)

Skip this step if only Lambda or infrastructure code changed.

```bash
export AWS_PROFILE=optinist
cd infrastructure/scripts
./ecr_build_push.sh
```

The script automatically:

1. Reads infrastructure configuration from Terraform outputs
2. Builds frontend with correct environment variables
3. Builds and tags Docker image
4. Pushes to the ECR repository for the active environment

> For details on ECR repository isolation, image tagging, and rollback, see [INFRA_DEPLOYMENT_PROCEDURE.md — ECR Repository Management](INFRA_DEPLOYMENT_PROCEDURE.md#ecr-repository-management).

**If Terraform outputs aren't available**, the script prompts you for:

- Frontend Host: `araya-optinist.com` (or ALB DNS for development)
- Frontend Protocol: `https` (production) / `http` (development)
- Frontend Port: `443` (production) / `80` (development)

### Step 3: Force ECS Redeployment (after Docker image push)

Skip this step if only Lambda or infrastructure code changed.

**Option A: AWS Console**

AWS Console → ECS → Cluster → Service → Update → check "Force new deployment"

**Option B: AWS CLI**

```bash
# Find cluster and service names
CLUSTER_NAME=$(aws ecs list-clusters \
  --region ap-northeast-1 \
  --query "clusterArns[?contains(@, 'subscr-optinist')]" \
  --output text | cut -d'/' -f2)

SERVICE_NAME=$(aws ecs list-services \
  --cluster $CLUSTER_NAME \
  --region ap-northeast-1 \
  --query "serviceArns[?contains(@, 'subscr-optinist')]" \
  --output text | cut -d'/' -f3)

# Force new deployment
aws ecs update-service \
  --cluster $CLUSTER_NAME \
  --service $SERVICE_NAME \
  --force-new-deployment \
  --region ap-northeast-1
```

---

## Post-Deployment Verification

After deployment, verify the application is running correctly.

### 1. Check ECS Service Status

```bash
# Find cluster and service (if not already set from previous step)
CLUSTER_NAME=$(aws ecs list-clusters \
  --region ap-northeast-1 \
  --query "clusterArns[?contains(@, 'subscr-optinist')]" \
  --output text | cut -d'/' -f2)

SERVICE_NAME=$(aws ecs list-services \
  --cluster $CLUSTER_NAME \
  --region ap-northeast-1 \
  --query "serviceArns[?contains(@, 'subscr-optinist')]" \
  --output text | cut -d'/' -f3)

aws ecs describe-services \
  --cluster $CLUSTER_NAME \
  --services $SERVICE_NAME \
  --region ap-northeast-1 \
  --query 'services[0].[serviceName,status,runningCount,desiredCount]' \
  --output table
```

Expected: `runningCount` should match `desiredCount` and status should be `ACTIVE`

### 2. Check Application Health

```bash
# Health check endpoint (using custom domain)
curl https://araya-optinist.com/health

# Or use ALB DNS directly
ALB_DNS=$(aws elbv2 describe-load-balancers \
  --query "LoadBalancers[?contains(LoadBalancerName, 'subscr-optinist')].DNSName" \
  --output text \
  --region ap-northeast-1)
curl http://$ALB_DNS/health
```

Expected: HTTP 200 response

### 3. Access the Application

Open in browser:

- Production: `https://araya-optinist.com`
- ALB (direct): `http://$ALB_DNS`

Verify:

- Login page appears
- Application loads without errors
- Can create and run workflows

### 4. Check ECS Task Logs

```bash
# Get task ARN
TASK_ARN=$(aws ecs list-tasks \
  --cluster $CLUSTER_NAME \
  --service-name $SERVICE_NAME \
  --region ap-northeast-1 \
  --query 'taskArns[0]' \
  --output text)

# Describe task
aws ecs describe-tasks \
  --cluster $CLUSTER_NAME \
  --tasks $TASK_ARN \
  --region ap-northeast-1 \
  --query 'tasks[0].[taskArn,lastStatus,healthStatus,containers[0].name]' \
  --output table
```

### 5. View CloudWatch Logs

```bash
# Get log group name
LOG_GROUP=$(aws logs describe-log-groups \
  --region ap-northeast-1 \
  --query "logGroups[?contains(logGroupName, 'subscr-optinist')].logGroupName" \
  --output text | head -1)

# Get recent log streams
aws logs describe-log-streams \
  --log-group-name $LOG_GROUP \
  --region ap-northeast-1 \
  --order-by LastEventTime \
  --descending \
  --max-items 5 \
  --query 'logStreams[*].[logStreamName,lastEventTime]' \
  --output table

# Tail recent logs
aws logs tail $LOG_GROUP \
  --follow \
  --region ap-northeast-1
```

**Tip:** You can also view logs in AWS Console → CloudWatch → Log Groups → `/ecs/subscr-optinist-*`

### 6. Verify Secrets Manager Access (optional)

```bash
# List all OptiNiSt secrets
aws secretsmanager list-secrets \
  --filters Key=name,Values=subscr-optinist \
  --region ap-northeast-1 \
  --query 'SecretList[*].[Name,ARN]' \
  --output table
```

---

## Release Preparation

Before deploying a new release, complete the following preparation steps.

### 1. Version File Updates

Update version numbers in the following files before building:

| File                    | Field to Update         |
| ----------------------- | ----------------------- |
| `pyproject.toml`        | `[tool.poetry] version` |
| `frontend/package.json` | `version`               |

**Version Format:** Use semantic versioning `X.Y.Z` (e.g., `2.4.0`)

```bash
# Example: Update from 2.4.0 to 2.5.0
# Edit pyproject.toml line: version = "2.5.0"
# Edit frontend/package.json line: "version": "2.5.0"
```

### 2. Pre-Release Testing

**Timeline:** Complete testing at least 1 week before the planned release date.

**Manual Test Cases:** [Test Case Spreadsheet](https://docs.google.com/spreadsheets/d/1bq0ySUQCnmSc9Lh5PUnfIKcS00fFCvpbDs_e797Z8W4/edit?usp=sharing)

**Automated Tests:**

```bash
# Run the test suite
cd /path/to/optinist-for-cloud
pytest studio/tests/
```

### 3. Staging Environment Testing (test-optinist-for-cloud) TODO

**Status:** Not yet set up

A parallel test infrastructure (`test-optinist-for-cloud`) will be available for pre-release testing. This is an exact copy of the production infrastructure with `test-` prefix on all resources.

**Workflow:**

1. **Create test environment:**

   ```bash
   cd infrastructure/terraform
   # Use test workspace/configuration
   terraform workspace select test
   terraform apply
   ```

2. **Deploy and test:**
   - Build and push Docker image to test ECR
   - Run manual test cases against test environment
   - Verify all functionality works as expected

3. **Destroy after testing:**
   ```bash
   terraform destroy
   ```

**Test Environment Resources:**

- `test-subscr-optinist-cluster` (ECS)
- `test-subscr-optinist-*` (ALB, RDS, S3, etc.)
- Separate Secrets Manager secrets
- Isolated from production data

**Benefits:**

- Safe testing without affecting production users
- Full infrastructure validation before release
- Cost-effective (only runs during testing periods)

### 4. CloudWatch Monitoring

Before and after releases, monitor the CloudWatch Dashboard for issues:

```bash
# Quick check via AWS CLI
aws cloudwatch get-metric-statistics \
  --namespace AWS/ECS \
  --metric-name CPUUtilization \
  --dimensions Name=ClusterName,Value=subscr-optinist-cloud-cluster \
  --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 300 \
  --statistics Average \
  --region ap-northeast-1
```

**TODO:** Register an Araya team email address to receive CloudWatch Alarm notifications for crash reports and errors.

---

## Git Workflow and Release Tags

### Standard Release Flow

```
feature-branch → develop-main → main → release tag (vX.Y.Z)
```

### Creating a Release

1. **Ensure all changes are merged to develop-main**

   ```bash
   git checkout develop-main
   git pull origin develop-main
   ```

2. **Create Pull Request: develop-main → main**
   - Open GitHub: https://github.com/arayabrain/araya-optinist/pulls
   - Create PR from `develop-main` to `main`
   - Ensure all CI tests pass
   - Get code review approval

3. **Merge and Create Release Tag**

   After merging to main:
   1. Go to [Releases page](https://github.com/arayabrain/araya-optinist/releases)
   2. Click "Draft a new release"
   3. **Choose a tag:** Create new tag in `vX.Y.Z` format (e.g., `v2.5.0`)
   4. **Target:** `main` branch
   5. **Release title:** `YYYY/MM Release` (e.g., `2024/12 Release`)
   6. **Description:** Include:
      - Summary of changes
      - New features
      - Bug fixes
      - Breaking changes (if any)
   7. Check "Set as the latest release"
   8. Click "Publish release"

### Release Notes Template

```markdown
## What's Changed

### New Features

- Feature description (#PR_NUMBER)

### Bug Fixes

- Fix description (#PR_NUMBER)

### Improvements

- Improvement description (#PR_NUMBER)

**Full Changelog:** https://github.com/arayabrain/araya-optinist/compare/vX.Y.Z-1...vX.Y.Z
```

---

## Documentation Updates

### Readthedocs TODO

**Documentation URL:** https://optinist-for-cloud.readthedocs.io

1. Documentation source files are located in the `docs/` directory

2. **Build and preview locally:**

   ```bash
   cd docs
   make html
   # Preview at docs/_build/html/index.html
   ```

3. **Trigger Readthedocs build:**
   - Login to [Readthedocs Dashboard](https://readthedocs.org/dashboard/)
   - Navigate to the optinist-for-cloud project
   - Click "Build Version"
   - Wait for build to complete

4. **Verify the update:**
   - Visit https://optinist-for-cloud.readthedocs.io
   - Confirm changes are reflected

### Wiki TODO

**Wiki URL:** https://github.com/oist/optinist/wiki

The project wiki contains additional documentation including:

- Architecture diagrams
- Troubleshooting guides
- FAQ

_Note: Update wiki documentation as needed when making significant changes._

---

## Hotfix Procedure

Use the hotfix procedure for urgent fixes that cannot wait for the regular release cycle.

### When to Use Hotfix

A hotfix is required when:

- **Security vulnerabilities** are discovered
- **Critical bugs** causing data loss or corruption
- **Application fails to start** or crashes frequently
- **Core functionality is completely broken**

### Hotfix Workflow

```
main → hotfix/vX.Y.Z → main → release tag
         ↓
    develop-main (sync after release)
```

### Expedited Hotfix Process

#### 1. Create Hotfix Branch from Main

```bash
git checkout main
git pull origin main
git checkout -b hotfix/vX.Y.Z
```

#### 2. Implement the Fix

- Make minimal changes to fix the issue
- Update version number (increment patch version Z)
  - `pyproject.toml`: version = "X.Y.Z"
  - `frontend/package.json`: "version": "X.Y.Z"

#### 3. Expedited Testing

For hotfixes, perform focused testing:

1. **Verify the fix:** Test that the specific issue is resolved
2. **Smoke test:** Basic application functionality
   - Application starts successfully
   - User can login
   - Basic workflow execution works
3. **Run automated tests:**
   ```bash
   pytest studio/tests/ -x  # Stop on first failure for faster feedback
   ```

#### 4. Deploy Hotfix

```bash
# Merge hotfix to main
git checkout main
git merge hotfix/vX.Y.Z
git push origin main

# Create release tag
# Follow "Creating a Release" section above
# Use title format: "Hotfix vX.Y.Z"

# Deploy to production
export AWS_PROFILE=optinist
cd infrastructure/scripts
./ecr_build_push.sh
```

#### 5. Sync Hotfix to develop-main

**Important:** After the hotfix is released, sync changes back to prevent future conflicts:

```bash
git checkout develop-main
git pull origin develop-main
git merge main
git push origin develop-main
```

Or create a PR: `main → develop-main` with title "Sync hotfix vX.Y.Z to develop-main"

---

## Troubleshooting

### ECS service not updating after image push

```bash
# Force new deployment
aws ecs update-service \
  --cluster $CLUSTER_NAME \
  --service $SERVICE_NAME \
  --force-new-deployment \
  --region ap-northeast-1
```

### Health check failing

- Check CloudWatch logs for application errors (see [View CloudWatch Logs](#5-view-cloudwatch-logs))
- Verify database connectivity (check RDS security groups)
- Check Secrets Manager access (verify IAM role has `secretsmanager:GetSecretValue`)

### Build script fails

```bash
# 1. Verify AWS credentials are valid
aws sts get-caller-identity

# 2. Verify Docker is running
docker info

# 3. Verify ECR repository exists
aws ecr describe-repositories \
  --repository-names optinist-for-cloud \
  --region ap-northeast-1

# 4. Verify Terraform is initialized to the correct environment
cat infrastructure/terraform/.terraform/terraform.tfstate | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['backend']['config']['bucket'])"
```

### Wrong AWS account / permission denied

If you receive `AccessDenied` or `UnauthorizedAccess` errors:

```bash
# Check which account/profile is currently active
aws sts get-caller-identity

# If wrong, set the correct profile
export AWS_PROFILE=optinist
aws sts get-caller-identity
```

### ECS task failing to start

```bash
# Check stopped task reasons
CLUSTER_NAME=$(aws ecs list-clusters \
  --region ap-northeast-1 \
  --query "clusterArns[?contains(@, 'subscr-optinist')]" \
  --output text | cut -d'/' -f2)

aws ecs list-tasks \
  --cluster $CLUSTER_NAME \
  --desired-status STOPPED \
  --region ap-northeast-1 \
  --query 'taskArns[0]' \
  --output text | xargs -I {} aws ecs describe-tasks \
    --cluster $CLUSTER_NAME \
    --tasks {} \
    --region ap-northeast-1 \
    --query 'tasks[0].{reason:stoppedReason,status:lastStatus,exitCode:containers[0].exitCode}'
```

Common causes:

- **Image pull failure**: ECR image does not exist or IAM role lacks ECR permissions
- **Secrets Manager error**: Container cannot read secrets (check IAM task execution role)
- **Out of memory**: Task memory limit too low for current workload
