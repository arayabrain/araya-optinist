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

# Get Firebase config from Secrets Manager (matches the environment's tfvars)
ENVIRONMENT=$(terraform -chdir=../terraform output -raw environment 2>/dev/null || echo "")
if [ -n "$ENVIRONMENT" ]; then
    echo "Getting Firebase config from Secrets Manager for environment: ${ENVIRONMENT}"
    FIREBASE_CONFIG=$(aws secretsmanager get-secret-value \
        --secret-id "${ENVIRONMENT}-optinist/firebase/config" \
        --query "SecretString" --output text --region $REGION 2>/dev/null || echo "")
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

# Inject Firebase config into .env.production if available
if [ -n "$FIREBASE_CONFIG" ]; then
    echo "Injecting Firebase config into frontend build..."
    FIREBASE_API_KEY=$(echo "$FIREBASE_CONFIG" | python3 -c "import sys,json; print(json.load(sys.stdin)['apiKey'])")
    FIREBASE_AUTH_DOMAIN=$(echo "$FIREBASE_CONFIG" | python3 -c "import sys,json; print(json.load(sys.stdin)['authDomain'])")
    FIREBASE_PROJECT_ID=$(echo "$FIREBASE_CONFIG" | python3 -c "import sys,json; print(json.load(sys.stdin)['projectId'])")
    FIREBASE_STORAGE_BUCKET=$(echo "$FIREBASE_CONFIG" | python3 -c "import sys,json; print(json.load(sys.stdin)['storageBucket'])")
    FIREBASE_MESSAGING_SENDER_ID=$(echo "$FIREBASE_CONFIG" | python3 -c "import sys,json; print(json.load(sys.stdin)['messagingSenderId'])")
    FIREBASE_APP_ID=$(echo "$FIREBASE_CONFIG" | python3 -c "import sys,json; print(json.load(sys.stdin)['appId'])")
    FIREBASE_MEASUREMENT_ID=$(echo "$FIREBASE_CONFIG" | python3 -c "import sys,json; print(json.load(sys.stdin).get('measurementId',''))")
    cat >> .env.production << ENV_EOF
REACT_APP_FIREBASE_API_KEY=${FIREBASE_API_KEY}
REACT_APP_FIREBASE_AUTH_DOMAIN=${FIREBASE_AUTH_DOMAIN}
REACT_APP_FIREBASE_PROJECT_ID=${FIREBASE_PROJECT_ID}
REACT_APP_FIREBASE_STORAGE_BUCKET=${FIREBASE_STORAGE_BUCKET}
REACT_APP_FIREBASE_MESSAGING_SENDER_ID=${FIREBASE_MESSAGING_SENDER_ID}
REACT_APP_FIREBASE_APP_ID=${FIREBASE_APP_ID}
REACT_APP_FIREBASE_MEASUREMENT_ID=${FIREBASE_MEASUREMENT_ID}
ENV_EOF
    echo "Firebase config injected for project: ${FIREBASE_PROJECT_ID}"
else
    echo "WARNING: Firebase config not found in Secrets Manager. Frontend will use defaults from .env"
fi

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
