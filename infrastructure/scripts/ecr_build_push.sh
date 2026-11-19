#!/bin/bash
set -e

# Common Configuration
REGION="ap-northeast-1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TERRAFORM_DIR="$(cd "$SCRIPT_DIR/../terraform" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# ===========================================
# Parse arguments
# ===========================================
# Usage: ./ecr_build_push.sh [--tag <version-tag>] [--yes] [--deploy]
#   --tag <tag>  : Custom version tag (default: the release tag on HEAD, such as
#                  v1.2.3, when the repository does not have it yet, else
#                  auto-generated YYYYMMDD-HHMMSS-<git-sha>)
#   --yes        : Skip confirmation prompt
#   --deploy     : After push, force-new-deployment on EVERY service in the
#                  cluster so all tiers re-pull the just-pushed :latest
CUSTOM_TAG=""
SKIP_CONFIRM=false
DEPLOY=false

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
        --deploy)
            DEPLOY=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: ./ecr_build_push.sh [--tag <version-tag>] [--yes] [--deploy]"
            exit 1
            ;;
    esac
done

# ===========================================
# Guard: reject builds from a dirty worktree
# ===========================================
DIRTY_FILES=$(git -C "$(git rev-parse --show-toplevel)" status --porcelain)
if [ -n "$DIRTY_FILES" ]; then
    CHANGED_FILES=$(printf '%s\n' "$DIRTY_FILES" | grep -v '^??' | cut -c4-)
    UNTRACKED_FILES=$(printf '%s\n' "$DIRTY_FILES" | grep '^??' | cut -c4-)
    echo "ERROR: the image is built only from committed code, and this checkout has"
    echo "files that are not committed. Untracked files count too: anything"
    echo "'git status' lists blocks the build."
    if [ -n "$CHANGED_FILES" ]; then
        echo ""
        echo "Changed (modified, staged, deleted or renamed):"
        printf '%s\n' "$CHANGED_FILES" | sed 's/^/  /'
    fi
    if [ -n "$UNTRACKED_FILES" ]; then
        echo ""
        echo "Untracked (not in git):"
        printf '%s\n' "$UNTRACKED_FILES" | sed 's/^/  /'
    fi
    echo ""
    echo "To fix: commit them, or set them aside with 'git stash -u' and bring them"
    echo "back after the build with 'git stash pop'."
    echo "From terraform apply: what the apply already changed stays changed, and"
    echo "null_resource.build_and_deploy is marked tainted, so rerunning"
    echo "terraform apply retries the build."
    exit 1
fi

# ===========================================
# Detect environment and ECR target
# ===========================================
echo "Reading Terraform outputs..."
ENVIRONMENT=$(terraform -chdir="$TERRAFORM_DIR" output -raw environment 2>/dev/null || echo "")
ECR_URI=$(terraform -chdir="$TERRAFORM_DIR" output -raw ecr_repository_url 2>/dev/null || echo "")

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

# Generate version tag and git provenance (see git_ref_info.sh for why the
# branch is resolved with symbolic-ref rather than rev-parse --abbrev-ref)
# shellcheck source=infrastructure/scripts/git_ref_info.sh
. "$SCRIPT_DIR/git_ref_info.sh"
resolve_git_ref_info "$REPO_ROOT"
GIT_SHA=$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo "unknown")
GIT_COMMIT_FULL="$GIT_INFO_COMMIT"
GIT_BRANCH="$GIT_INFO_BRANCH"
GIT_TAG="$GIT_INFO_TAG"
BUILD_TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
# A release commit gets its version even without --tag, which terraform apply
# never passes; a prerelease or untagged commit gets a timestamp.
# A release tag already in the repository stays on the image it marks: a rebuild
# of the release commit (a rollback apply, a retry) is a new image and gets a
# timestamp. Only --tag moves a tag. A failed check also falls back to the timestamp
TIMESTAMP_TAG="$(date +%Y%m%d-%H%M%S)-${GIT_SHA}"
TAG_WARNING=""
if [ -n "$CUSTOM_TAG" ]; then
    VERSION_TAG="$CUSTOM_TAG"
elif [ -n "$GIT_INFO_RELEASE_TAG" ]; then
    TAG_CHECK_ERR=$(mktemp)
    if aws ecr describe-images --repository-name "$REPO_NAME" \
        --image-ids imageTag="$GIT_INFO_RELEASE_TAG" --region "$REGION" >/dev/null 2>"$TAG_CHECK_ERR"; then
        VERSION_TAG="$TIMESTAMP_TAG"
        TAG_WARNING="${GIT_INFO_RELEASE_TAG} is already in ${REPO_NAME} and stays on that image; this build is tagged ${VERSION_TAG}. To move ${GIT_INFO_RELEASE_TAG}, run this script with --tag ${GIT_INFO_RELEASE_TAG}"
    elif grep -q ImageNotFoundException "$TAG_CHECK_ERR"; then
        VERSION_TAG="$GIT_INFO_RELEASE_TAG"
    else
        VERSION_TAG="$TIMESTAMP_TAG"
        TAG_WARNING="could not check whether ${GIT_INFO_RELEASE_TAG} is already in ${REPO_NAME}, so this build is tagged ${VERSION_TAG}: $(cat "$TAG_CHECK_ERR")"
    fi
    rm -f "$TAG_CHECK_ERR"
else
    VERSION_TAG="$TIMESTAMP_TAG"
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
echo "  Git commit  : ${GIT_COMMIT_FULL} (${GIT_SHA})"
echo "  Git branch  : ${GIT_BRANCH:--}"
echo "  Git tag     : ${GIT_TAG:--}"
echo "  Build time  : ${BUILD_TIMESTAMP}"
echo "============================================"
echo ""

# Production environment uses environment="subscr" (see environments/production.tfvars)
PRODUCTION_ENV="subscr"
if [ "$ENVIRONMENT" = "$PRODUCTION_ENV" ]; then
    echo "  *** WARNING: You are pushing to PRODUCTION! ***"
    echo ""
fi

if [ -n "$TAG_WARNING" ]; then
    echo "  WARNING: ${TAG_WARNING}"
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
AUTOSCALING_HOST=$(terraform -chdir="$TERRAFORM_DIR" output -raw domain_name)
AUTOSCALING_PORT=$(terraform -chdir="$TERRAFORM_DIR" output -raw domain_port)
AUTOSCALING_PROTO=$(terraform -chdir="$TERRAFORM_DIR" output -raw domain_protocol)

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

# Verify ECR repository exists (must be created by Terraform, not this script)
if ! aws ecr describe-repositories --repository-names $REPO_NAME --region $REGION >/dev/null 2>&1; then
    echo "ERROR: ECR repository '$REPO_NAME' does not exist."
    echo "The repository must be created by Terraform. Run 'terraform apply' first."
    exit 1
fi

# GTM ID: only the gitignored .env.deploy (not a shell export) overrides SSM; neither means analytics off.
GTM_ID_VAR="REACT_APP_GTM_ID_$(echo "$ENVIRONMENT" | tr '[:lower:]-' '[:upper:]_')"
GTM_ID_PARAM="/${ENVIRONMENT}/optinist/gtm-id"
REACT_APP_GTM_ID=$(
    set +e
    unset "$GTM_ID_VAR"
    if [ -f "$REPO_ROOT/infrastructure/.env.deploy" ]; then
        . "$REPO_ROOT/infrastructure/.env.deploy"
    fi
    printf '%s' "${!GTM_ID_VAR:-}"
)
GTM_ID_SOURCE=".env.deploy $GTM_ID_VAR"

if [ -z "$REACT_APP_GTM_ID" ]; then
    GTM_SSM_ERR=$(mktemp)
    if REACT_APP_GTM_ID=$(aws ssm get-parameter --name "$GTM_ID_PARAM" \
        --query Parameter.Value --output text --region "$REGION" 2>"$GTM_SSM_ERR"); then
        GTM_ID_SOURCE="SSM $GTM_ID_PARAM"
    elif grep -q ParameterNotFound "$GTM_SSM_ERR"; then
        REACT_APP_GTM_ID=""
    else
        echo "ERROR: could not read $GTM_ID_PARAM from SSM:"
        cat "$GTM_SSM_ERR"
        rm -f "$GTM_SSM_ERR"
        exit 1
    fi
    rm -f "$GTM_SSM_ERR"
fi

if [ -n "$REACT_APP_GTM_ID" ] && ! [[ "$REACT_APP_GTM_ID" =~ ^GTM-[A-Z0-9]+$ ]]; then
    echo "ERROR: malformed GTM ID from $GTM_ID_SOURCE ('$REACT_APP_GTM_ID'). Expected GTM-XXXXXXX."
    exit 1
fi

# Build frontend with custom domain for autoscaling
echo "Building frontend for autoscaling with ${AUTOSCALING_PROTO}://${AUTOSCALING_HOST}:${AUTOSCALING_PORT}"
if [ -n "$REACT_APP_GTM_ID" ]; then
    echo "Analytics ($ENVIRONMENT): $REACT_APP_GTM_ID (from $GTM_ID_SOURCE)"
else
    echo "Analytics ($ENVIRONMENT): <none: $GTM_ID_VAR unset in .env.deploy and $GTM_ID_PARAM not in SSM, analytics disabled>"
fi
cd "$REPO_ROOT/frontend"
cat > .env.production << ENV_EOF
REACT_APP_SERVER_HOST=${AUTOSCALING_HOST}
REACT_APP_SERVER_PORT=${AUTOSCALING_PORT}
REACT_APP_SERVER_PROTO=${AUTOSCALING_PROTO}
REACT_APP_EXPDB_METADATA_EDITABLE=true
REACT_APP_GTM_ID=${REACT_APP_GTM_ID:-}
ENV_EOF

yarn install
yarn build
cd "$REPO_ROOT"

# Build the Docker image with embedded build metadata
echo "Building autoscaling Docker image..."
docker build -f studio/config/docker/Dockerfile \
    --build-arg GIT_COMMIT="${GIT_COMMIT_FULL}" \
    --build-arg GIT_BRANCH="${GIT_BRANCH}" \
    --build-arg GIT_TAG="${GIT_TAG}" \
    --build-arg BUILD_TIMESTAMP="${BUILD_TIMESTAMP}" \
    -t $REPO_NAME:$IMAGE_TAG .

# Tag and push to ECR — both :latest (for ECS) and versioned (for history/rollback)
docker tag $REPO_NAME:$IMAGE_TAG $ECR_URI:latest
docker tag $REPO_NAME:$IMAGE_TAG $ECR_URI:$VERSION_TAG
# Without a terminal (terraform apply, CI) docker drops its progress bars, sizes
# included, and prints a line per layer and state. Keep a count instead: a line
# each time a layer upload ends; a layer already in the registry is only counted.
# The containerd image store lists no layers up front (no Preparing lines) and
# also reports the config and manifest uploads, so there the line gives the
# uploads alone, with no total
count_pushed_layers() {
    local tag="$1" line total=0 finished=0 uploaded=0
    while IFS= read -r line; do
        case "$line" in
            *": Preparing") total=$((total + 1)) ;;
            *": Pushed")
                finished=$((finished + 1))
                uploaded=$((uploaded + 1))
                if [ "$total" -gt 0 ]; then
                    echo "  ${tag}: ${finished}/${total} layers"
                else
                    echo "  ${tag}: ${uploaded} pushed"
                fi
                ;;
            *": Layer already exists"|*": Already exists"|*": Mounted from "*) finished=$((finished + 1)) ;;
            *": Waiting"|"The push refers to repository"*) ;;
            *) printf '%s\n' "$line" ;;
        esac
    done
}

for PUSH_TAG in latest "$VERSION_TAG"; do
    echo "Pushing ${ECR_URI}:${PUSH_TAG}..."
    if [ -t 1 ]; then
        docker push "$ECR_URI:$PUSH_TAG"
    else
        # pipefail: a failed push still stops the build
        (set -o pipefail; docker push "$ECR_URI:$PUSH_TAG" | count_pushed_layers "$PUSH_TAG")
    fi
done

echo ""
echo "============================================"
echo "  PUSH COMPLETE"
echo "============================================"
echo "  Environment : ${ENVIRONMENT}"
echo "  latest      : ${ECR_URI}:latest"
echo "  Version     : ${ECR_URI}:${VERSION_TAG}"
echo "============================================"
if [ -n "$TAG_WARNING" ]; then
    echo "  WARNING: ${TAG_WARNING}"
fi

# ===========================================
# Optional: force every service to re-pull :latest
# ===========================================
# Runs AFTER the push, so the force always resolves the digest we just pushed.
# Cycles all tiers (main/premium/public/background), not just the main service;
# ecs_rollout.sh is the same rollout terraform apply runs.
if [ "$DEPLOY" = true ]; then
    CLUSTER=$(terraform -chdir="$TERRAFORM_DIR" output -raw ecs_cluster_name)
    ROLLOUT_ARGS="--cluster $CLUSTER --region $REGION"
    # An output missing from an older state only leaves that group's host
    # health checks on during the rollout
    for ASG_OUTPUT in asg_name public_asg_name; do
        ASG=$(terraform -chdir="$TERRAFORM_DIR" output -raw "$ASG_OUTPUT" 2>/dev/null || echo "")
        if [ -n "$ASG" ]; then
            ROLLOUT_ARGS="$ROLLOUT_ARGS --asg $ASG"
        else
            echo "Warning: no '${ASG_OUTPUT}' output; that group's host health checks stay on during the rollout"
        fi
    done
    HYGIENE_ID=$(terraform -chdir="$TERRAFORM_DIR" output -raw docker_hygiene_association_id 2>/dev/null || echo "")
    if [ -n "$HYGIENE_ID" ]; then
        ROLLOUT_ARGS="$ROLLOUT_ARGS --hygiene-association $HYGIENE_ID"
    fi
    echo ""
    # Unquoted: the names and ids carry no spaces
    # shellcheck disable=SC2086
    bash "$SCRIPT_DIR/ecs_rollout.sh" $ROLLOUT_ARGS
    echo "Any service at desiredCount 0 re-pulls :latest on its next scale-up."
fi
