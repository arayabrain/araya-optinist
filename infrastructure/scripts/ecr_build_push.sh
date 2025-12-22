#!/bin/bash
set -e

# Common Configuration
REGION="ap-northeast-1"
AWS_ACCOUNT_ID="637423646530"

# Get configuration from Terraform outputs
echo "Getting configuration from Terraform outputs..."
AUTOSCALING_HOST=$(terraform -chdir=../terraform output -raw domain_name)
AUTOSCALING_PORT=$(terraform -chdir=../terraform output -raw domain_port)
AUTOSCALING_PROTO=$(terraform -chdir=../terraform output -raw domain_protocol)
# BATCH_DNS=$(terraform output -raw alb_dns_name_batch)

echo "Autoscaling Host: $AUTOSCALING_HOST"
echo "Autoscaling Protocol: $AUTOSCALING_PROTO"
echo "Autoscaling Port: $AUTOSCALING_PORT"
# echo "Batch DNS: $BATCH_DNS"

# Validate batch DNS (required)
# if [ -z "$BATCH_DNS" ]; then
#     echo "Error: Could not get batch ALB DNS from Terraform outputs."
#     echo "Please run 'terraform apply' first to create the infrastructure."
#     exit 1
# fi

# ===========================================
# 1. Build Autoscaling Image with Frontend
# ===========================================
REPO_NAME="optinist-for-cloud"
IMAGE_TAG="latest"
ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO_NAME}"

echo "Building autoscaling image: $ECR_URI"

# Authenticate Docker to ECR (ignore keychain errors on macOS)
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ECR_URI 2>&1 | grep -v "error storing credentials" || true

# Check if ECR repository exists, create if it doesn't
if ! aws ecr describe-repositories --repository-names $REPO_NAME --region $REGION >/dev/null 2>&1; then
    echo "Repository $REPO_NAME does not exist. Creating..."
    aws ecr create-repository --repository-name $REPO_NAME --region $REGION
    echo "Repository $REPO_NAME created successfully."
else
    echo "Repository $REPO_NAME already exists."
fi

# Build frontend with custom domain for autoscaling
echo "Building frontend for autoscaling with ${AUTOSCALING_PROTO}://${AUTOSCALING_HOST}:${AUTOSCALING_PORT}"
cd ../../frontend
cat > .env.production << ENV_EOF
REACT_APP_SERVER_HOST=${AUTOSCALING_HOST}
REACT_APP_SERVER_PORT=${AUTOSCALING_PORT}
REACT_APP_SERVER_PROTO=${AUTOSCALING_PROTO}
REACT_APP_EXPDB_METADATA_EDITABLE=true
ENV_EOF

yarn install
yarn build
cd ..

# Build the Docker image
echo "Building autoscaling Docker image..."
docker build -f studio/config/docker/Dockerfile -t $REPO_NAME:$IMAGE_TAG .

# Tag and push to ECR
docker tag $REPO_NAME:$IMAGE_TAG $ECR_URI:latest
docker push $ECR_URI:latest
echo "Successfully pushed autoscaling image: $ECR_URI:latest"
