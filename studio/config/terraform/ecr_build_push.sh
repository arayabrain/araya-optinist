#!/bin/bash
set -e

# Common Configuration
REGION="ap-northeast-1"
AWS_ACCOUNT_ID="637423646530"

# Get load balancer DNS names from Terraform outputs
echo "Getting load balancer DNS names from Terraform..."
AUTOSCALING_DNS=$(terraform output -raw alb_dns_name)
BATCH_DNS=$(terraform output -raw alb_dns_name_batch)

echo "Autoscaling DNS: $AUTOSCALING_DNS"
echo "Batch DNS: $BATCH_DNS"

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

# Build frontend with autoscaling DNS
echo "Building frontend for autoscaling with DNS: $AUTOSCALING_DNS"
cd ../../../frontend
cat > .env.production << ENV_EOF
REACT_APP_SERVER_HOST=${AUTOSCALING_DNS}
REACT_APP_SERVER_PORT=80
REACT_APP_SERVER_PROTO=http
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

# ===========================================
# 2. Build Batch Image with Frontend
# ===========================================
REPO_NAME="optinist-for-cloud-batch"
ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO_NAME}"

echo "Building batch image: $ECR_URI"

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

# Build frontend with batch DNS
echo "Building frontend for batch with DNS: $BATCH_DNS"
cd frontend
cat > .env.production << ENV_EOF
REACT_APP_SERVER_HOST=${BATCH_DNS}
REACT_APP_SERVER_PORT=80
REACT_APP_SERVER_PROTO=http
REACT_APP_EXPDB_METADATA_EDITABLE=true
ENV_EOF

yarn install
yarn build
cd ..

# Build the Docker image
echo "Building batch Docker image..."
docker build -f studio/config/docker/Dockerfile -t $REPO_NAME:$IMAGE_TAG .

# Tag and push to ECR
docker tag $REPO_NAME:$IMAGE_TAG $ECR_URI:latest
docker push $ECR_URI:latest
echo "Successfully pushed batch image: $ECR_URI:latest"

# ===========================================
# 3. Build Snakemake Batch Image (No Frontend)
# ===========================================
REPO_NAME="optinist-for-cloud-snakemake-batch"
ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO_NAME}"

echo "Building snakemake batch image: $ECR_URI"

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

# Build the Docker image (no frontend build needed)
echo "Building snakemake batch Docker image..."
docker build -f studio/config/docker/Dockerfile.batch -t $REPO_NAME:$IMAGE_TAG .

# Tag and push to ECR
docker tag $REPO_NAME:$IMAGE_TAG $ECR_URI:latest
docker push $ECR_URI:latest
echo "Successfully pushed snakemake batch image: $ECR_URI:latest"

echo "All images built and pushed successfully!"
