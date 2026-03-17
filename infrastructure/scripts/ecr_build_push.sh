#!/bin/bash
set -e

# Common Configuration
REGION="ap-northeast-1"
AWS_ACCOUNT_ID="637423646530"

# ===========================================
# Parse arguments
# ===========================================
# Usage: ./ecr_build_push.sh [--tag <version-tag>] [--yes]
#   --tag <tag>  : Custom version tag (default: auto-generated YYYYMMDD-HHMMSS-<git-sha>)
#   --yes        : Skip confirmation prompt
CUSTOM_TAG=""
SKIP_CONFIRM=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --tag)
            CUSTOM_TAG="$2"
            shift 2
            ;;
        --yes|-y)
            SKIP_CONFIRM=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: ./ecr_build_push.sh [--tag <version-tag>] [--yes]"
            exit 1
            ;;
    esac
done

# ===========================================
# Detect environment and ECR target
# ===========================================
echo "Reading Terraform outputs..."
ENVIRONMENT=$(terraform -chdir=../terraform output -raw environment 2>/dev/null || echo "")
ECR_URI=$(terraform -chdir=../terraform output -raw ecr_repository_url 2>/dev/null || echo "")

if [ -z "$ENVIRONMENT" ]; then
    echo "ERROR: Could not read environment from Terraform output."
    echo "Make sure you have initialized Terraform with the correct backend:"
    echo "  terraform init -backend-config=backends/development.hcl"
    echo "  terraform init -backend-config=backends/production.hcl"
    exit 1
fi

if [ -z "$ECR_URI" ]; then
    echo "ERROR: Could not read ecr_repository_url from Terraform output."
    echo "Make sure you have run 'terraform apply' for the target environment."
    exit 1
fi

REPO_NAME=$(echo "$ECR_URI" | sed 's|.*/||')

# Generate version tag
GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
if [ -n "$CUSTOM_TAG" ]; then
    VERSION_TAG="$CUSTOM_TAG"
else
    VERSION_TAG="$(date +%Y%m%d-%H%M%S)-${GIT_SHA}"
fi

# ===========================================
# Environment confirmation
# ===========================================
echo ""
echo "============================================"
echo "  BUILD AND PUSH CONFIRMATION"
echo "============================================"
echo "  Environment : ${ENVIRONMENT}"
echo "  ECR Repo    : ${REPO_NAME}"
echo "  ECR URI     : ${ECR_URI}"
echo "  Tags        : latest, ${VERSION_TAG}"
echo "  Git commit  : ${GIT_SHA}"
echo "============================================"
echo ""

if [ "$ENVIRONMENT" = "subscr" ] || echo "$ENVIRONMENT" | grep -qi "prod"; then
    echo "  *** WARNING: You are pushing to PRODUCTION! ***"
    echo ""
fi

if [ "$SKIP_CONFIRM" = false ]; then
    read -p "Proceed with build and push? (y/N): " CONFIRM
    if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
        echo "Aborted."
        exit 0
    fi
fi

# ===========================================
# Get configuration from Terraform outputs
# ===========================================
AUTOSCALING_HOST=$(terraform -chdir=../terraform output -raw domain_name)
AUTOSCALING_PORT=$(terraform -chdir=../terraform output -raw domain_port)
AUTOSCALING_PROTO=$(terraform -chdir=../terraform output -raw domain_protocol)

echo "Autoscaling Host: $AUTOSCALING_HOST"
echo "Autoscaling Protocol: $AUTOSCALING_PROTO"
echo "Autoscaling Port: $AUTOSCALING_PORT"

# ===========================================
# 1. Build Autoscaling Image with Frontend
# ===========================================
IMAGE_TAG="latest"

echo "Building image for repo: $ECR_URI (repo: $REPO_NAME)"

# Authenticate Docker to ECR (ignore keychain errors on macOS)
ECR_REGISTRY=$(echo "$ECR_URI" | sed 's|/.*||')
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ECR_REGISTRY 2>&1 | grep -v "error storing credentials" || true

# Check if ECR repository exists, create if it doesn't
if ! aws ecr describe-repositories --repository-names $REPO_NAME --region $REGION >/dev/null 2>&1; then
    echo "Repository $REPO_NAME does not exist. Creating..."
    aws ecr create-repository --repository-name $REPO_NAME --region $REGION
    echo "Repository $REPO_NAME created successfully."
else
    echo "Repository $REPO_NAME already exists."
fi

# Get Firebase config from Secrets Manager (matches the environment's tfvars)
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

# Tag and push to ECR — both :latest (for ECS) and versioned (for history/rollback)
docker tag $REPO_NAME:$IMAGE_TAG $ECR_URI:latest
docker tag $REPO_NAME:$IMAGE_TAG $ECR_URI:$VERSION_TAG
docker push $ECR_URI:latest
docker push $ECR_URI:$VERSION_TAG

echo ""
echo "============================================"
echo "  PUSH COMPLETE"
echo "============================================"
echo "  Environment : ${ENVIRONMENT}"
echo "  latest      : ${ECR_URI}:latest"
echo "  Version     : ${ECR_URI}:${VERSION_TAG}"
echo "============================================"
