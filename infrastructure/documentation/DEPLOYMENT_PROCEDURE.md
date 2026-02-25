# OptiNiSt Cloud Deployment Procedure

This document describes how to deploy OptiNiSt to AWS infrastructure. There are two deployment methods depending on your level of access.

**Production URL:** `https://araya-optinist.com`

## Overview: Secrets Manager Architecture

**OptiNiSt uses AWS Secrets Manager for credential storage.** This enables team members to deploy without needing access to `terraform.tfvars`.

### How It Works:

1. **One-time setup (requires terraform.tfvars):**
   - Run `terraform apply` once to create AWS Secrets Manager secrets
   - Secrets contain: Firebase config, database credentials, application keys, Stripe config
   - Secrets are stored permanently in AWS

2. **Ongoing deployments (no terraform.tfvars needed):**
   - Build and push Docker images using `ecr_build_push.sh`
   - The static `app_setup.sh` script automatically:
     - Reads secrets from AWS Secrets Manager
     - Discovers infrastructure (RDS endpoint, S3 buckets) via AWS CLI
     - Configures the application with correct settings

## Table of Contents

- [Method 1: Deployment with Terraform Access](#method-1-deployment-with-terraform-access-full-access)
- [Method 2: Deployment without Terraform Access](#method-2-deployment-without-terraform-access)
- [Post-Deployment Verification](#post-deployment-verification)
- [Release Preparation](#release-preparation)
- [Git Workflow and Release Tags](#git-workflow-and-release-tags)
- [Documentation Updates (Readthedocs)](#documentation-updates-readthedocs)
- [Wiki Documentation](#wiki-documentation)
- [Hotfix Procedure](#hotfix-procedure)
- [Troubleshooting](#troubleshooting)

---

### Required Tools Installation

```bash
# Install AWS CLI
# https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html

# Install Docker
# https://docs.docker.com/get-docker/

# Configure AWS credentials
aws configure
```

---

## Method 1: Deployment with Terraform Access (Full Access)

Use this method if you have access to `infrastructure/terraform/terraform.tfvars`.

### Prerequisites for Method 1

- Access to `infrastructure/terraform/terraform.tfvars`
- Terraform installed (v1.0+)
- Firebase credentials (already configured in terraform.tfvars)

### 1.1 Standard Deployment (Image Update Only)

Use this when you only need to update the application code without infrastructure changes.

```bash
cd infrastructure/scripts
./ecr_build_push.sh
```

This script will:

1. Read configuration from Terraform outputs (domain, port, protocol)
2. Build the frontend with the correct environment variables
3. Build and tag the Docker image
4. Push the image to ECR
5. ECS will automatically deploy the new image

**Note:** ECS service is configured to automatically pull the `:latest` tag, so the deployment happens automatically after the push.

### 1.2 Deployment with Infrastructure Changes

Use this when you need to update AWS infrastructure (VPC, ALB, RDS, etc.) in addition to the application.

```bash
cd infrastructure/terraform

# Review planned changes
terraform plan

# Apply infrastructure changes
terraform apply

# The deployment script runs automatically via terraform provisioners
# But you can manually trigger image build and deployment if needed:
cd ../scripts
./ecr_build_push.sh
```

**What gets updated:**

- VPC, subnets, security groups (if modified)
- Load balancers and target groups
- ECS services and task definitions
- RDS database configuration
- Auto Scaling Groups
- Route53 and ACM certificates

---

## Method 2: Deployment without Terraform Access

**With AWS Secrets Manager, you can now deploy without terraform.tfvars**

Use this method for routine deployments after the initial infrastructure setup has been completed.

### Prerequisites for Method 2

- AWS CLI configured with valid credentials
- Docker installed and running
- AWS credentials with permissions for:
  - ECR (push images)
  - Secrets Manager (read secrets) - granted automatically to deployment role
  - ECS (list services/clusters)
  - RDS (describe instances)
  - S3 (list buckets)

### How It Works

The deployment process is now streamlined:

1. **Secrets are already in AWS** - Created during initial `terraform apply`
2. **app_setup.sh reads from Secrets Manager** - No hardcoded credentials
3. **Infrastructure discovery via AWS CLI** - Finds RDS, S3, etc. automatically
4. **You only need to build and push** - Everything else is automatic

**No Firebase extraction needed!** The `app_setup.sh` script automatically retrieves Firebase and all other secrets from AWS Secrets Manager.

### 2.1 Simple Deployment Steps

**Deploy in 2 simple steps:**

```bash
cd infrastructure/scripts
./ecr_build_push.sh
```

**Script automatically:**

1. - Gets infrastructure configuration (tries Terraform outputs first, then prompts if unavailable)
2. - Builds frontend with correct environment variables
3. - Builds and tags Docker image
4. - Pushes to ECR
5. - ECS automatically deploys the new image

**If Terraform outputs aren't available, you'll be prompted for:**

- Frontend Host: `araya-optinist.com` (or ALB DNS)
- Frontend Protocol: `https`
- Frontend Port: `443`

### 2.2 Finding AWS Resource Values (If Needed)

If the build script prompts you for values, here's how to find them via AWS CLI:

#### Get ALB DNS Name (For Testing)

```bash
# Find the Application Load Balancer DNS
aws elbv2 describe-load-balancers \
  --query "LoadBalancers[?contains(LoadBalancerName, 'subscr-optinist')].DNSName" \
  --output text \
  --region ap-northeast-1

# Production domain: araya-optinist.com
# Verify Route53 configuration
aws route53 list-hosted-zones \
  --query "HostedZones[?contains(Name, 'araya-optinist')].Id" \
  --output text
```

#### Get AWS Account ID

```bash
aws sts get-caller-identity --query Account --output text
```

#### Check Secrets Manager Secrets (For Verification)

```bash
# List all OptiNiSt secrets
aws secretsmanager list-secrets \
  --filters Key=name,Values=subscr-optinist \
  --region ap-northeast-1 \
  --query 'SecretList[*].[Name,ARN]' \
  --output table
```

**Note:** You don't need to manually extract or configure Firebase credentials. The `app_setup.sh` script automatically retrieves everything from AWS Secrets Manager when EC2 instances launch.

---

## Post-Deployment Verification

After deployment, verify the application is running correctly:

### 1. Find Your Cluster and Service Names

```bash
# Find ECS cluster
CLUSTER_NAME=$(aws ecs list-clusters \
  --region ap-northeast-1 \
  --query "clusterArns[?contains(@, 'subscr-optinist')]" \
  --output text | cut -d'/' -f2)

# Find ECS service
SERVICE_NAME=$(aws ecs list-services \
  --cluster $CLUSTER_NAME \
  --region ap-northeast-1 \
  --query "serviceArns[?contains(@, 'subscr-optinist')]" \
  --output text | cut -d'/' -f3)

echo "Cluster: $CLUSTER_NAME"
echo "Service: $SERVICE_NAME"
```

### 2. Check ECS Service Status

```bash
aws ecs describe-services \
  --cluster $CLUSTER_NAME \
  --services $SERVICE_NAME \
  --region ap-northeast-1 \
  --query 'services[0].[serviceName,status,runningCount,desiredCount]' \
  --output table
```

Expected: `runningCount` should match `desiredCount` and status should be `ACTIVE`

### 3. Get ALB DNS Name

```bash
ALB_DNS=$(aws elbv2 describe-load-balancers \
  --query "LoadBalancers[?contains(LoadBalancerName, 'subscr-optinist')].DNSName" \
  --output text \
  --region ap-northeast-1)

echo "ALB DNS: $ALB_DNS"
```

### 4. Check Application Health

```bash
# Health check endpoint (using custom domain)
curl https://araya-optinist.com/health

# Or use ALB DNS directly
curl http://$ALB_DNS/health
```

Expected: HTTP 200 response

### 5. Access the Application

Open in browser:

- Production: `https://araya-optinist.com`
- ALB (direct): `http://$ALB_DNS`

Verify:

- Login page appears
- Application loads without errors
- Can create and run workflows

### 6. Check ECS Task Logs

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

### 7. View CloudWatch Logs

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

# Tail recent logs (replace LOG_STREAM_NAME with actual value from above)
aws logs tail $LOG_GROUP \
  --follow \
  --region ap-northeast-1
```

**Tip:** You can also view logs in AWS Console → CloudWatch → Log Groups → `/ecs/subscr-optinist-*`

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
  --dimensions Name=ClusterName,Value=subscr-optinist-cluster \
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

## Documentation Updates (Readthedocs) TODO

**Documentation URL:** https://optinist-for-cloud.readthedocs.io

### Updating Documentation

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

---

## Wiki Documentation TODO

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

### Common Issues

**ECS service not updating after push:**

```bash
# Force new deployment
aws ecs update-service \
  --cluster $CLUSTER_NAME \
  --service $SERVICE_NAME \
  --force-new-deployment \
  --region ap-northeast-1
```

**Health check failing:**

- Check CloudWatch logs for application errors
- Verify database connectivity
- Check Secrets Manager access

**Build script fails:**

- Ensure AWS credentials are valid: `aws sts get-caller-identity`
- Verify Docker is running
- Check ECR repository exists
