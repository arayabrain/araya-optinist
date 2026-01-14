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
- Method 1: Deployment with Terraform Access
- Method 2: Deployment without Terraform Access
- Post-Deployment Verification

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
1. ✅ Gets infrastructure configuration (tries Terraform outputs first, then prompts if unavailable)
2. ✅ Builds frontend with correct environment variables
3. ✅ Builds and tags Docker image
4. ✅ Pushes to ECR
5. ✅ ECS automatically deploys the new image

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
