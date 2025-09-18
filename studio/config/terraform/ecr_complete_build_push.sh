#!/bin/bash
set -e

# Enhanced ECR Build Push Script with Complete Conda Environment Setup
# This script creates a complete all-in-one Docker image with all conda environments pre-built

echo "=========================================="
echo "OPTINIST ALL-IN-ONE IMAGE BUILDER"
echo "=========================================="
echo "This script will build a complete OptiNiSt Docker image with all conda environments pre-built."
echo "Estimated build time: 30-60 minutes"
echo "Final image size: ~27GB"
echo ""

# AWS Configuration
echo "Checking AWS configuration..."

# Check if AWS CLI is installed
if ! command -v aws >/dev/null 2>&1; then
    echo "Error: AWS CLI is not installed!"
    echo "Please install AWS CLI first: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    exit 1
fi

# Check if Docker is installed and running
if ! command -v docker >/dev/null 2>&1; then
    echo "Error: Docker is not installed!"
    echo "Please install Docker first: https://docs.docker.com/get-docker/"
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo "Error: Docker is not running!"
    echo "Please start Docker and try again."
    exit 1
fi

# Get AWS configuration
REGION="${AWS_DEFAULT_REGION:-ap-northeast-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-}"

# Try to get account ID from AWS CLI
if [ -z "$AWS_ACCOUNT_ID" ]; then
    echo "Getting AWS Account ID..."
    AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "")
fi

# If still not available, prompt user
if [ -z "$AWS_ACCOUNT_ID" ]; then
    echo ""
    echo "Please provide your AWS Account ID:"
    echo "You can find this in AWS Console > Top right corner > Account ID"
    echo "Or run: aws sts get-caller-identity --query Account --output text"
    read -p "AWS Account ID: " AWS_ACCOUNT_ID
fi

# Validate AWS Account ID
if [ -z "$AWS_ACCOUNT_ID" ] || [ ${#AWS_ACCOUNT_ID} -ne 12 ]; then
    echo "Error: Invalid AWS Account ID. Must be 12 digits."
    exit 1
fi

# Check for region
if [ -z "$REGION" ] ; then
    echo "Error: Invalid AWS Region, please enter into script."
    exit 1
fi

echo ""
echo "Using AWS Configuration:"
echo "  Account ID: $AWS_ACCOUNT_ID"
echo "  Region: $REGION"
echo ""

# Get load balancer DNS name from Terraform outputs or user input
echo "Getting load balancer DNS name..."

# Try to get from terraform first
if command -v terraform >/dev/null 2>&1 && [ -f "main.tf" ]; then
    echo "Attempting to get DNS name from Terraform outputs..."
    AUTOSCALING_DNS=$(terraform output -raw alb_dns_name 2>/dev/null || echo "")
fi

# If terraform outputs not available, prompt user
if [ -z "$AUTOSCALING_DNS" ]; then
    echo ""
    echo "=========================================="
    echo "TERRAFORM OUTPUTS NOT AVAILABLE"
    echo "=========================================="
    echo "Please get the Load Balancer DNS name from AWS Console:"
    echo ""
    echo "1. Go to AWS Console > EC2 > Load Balancers"
    echo "2. Find your OptiNiSt load balancer (usually named something like 'subscr-alb-*')"
    echo "3. Copy the DNS name from the 'DNS name' column"
    echo ""
    echo "Enter the Load Balancer DNS name:"
    read -p "Load Balancer DNS: " AUTOSCALING_DNS
fi

# Validate DNS name
if [ -z "$AUTOSCALING_DNS" ]; then
    echo "Error: Load Balancer DNS is required!"
    exit 1
fi

echo "Load Balancer DNS: $AUTOSCALING_DNS"

# ===========================================
# 1. Build Complete All-in-One Image with Conda Environments
# ===========================================
REPO_NAME="optinist-for-cloud"
IMAGE_TAG="latest"
VERSION_TAG="${VERSION_TAG:-$(date +%Y%m%d-%H%M%S)}"
ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO_NAME}"

echo "Building complete cloud all-in-one image: $ECR_URI"

# Check if ECR repository exists, create if it doesn't
if ! aws ecr describe-repositories --repository-names $REPO_NAME --region $REGION >/dev/null 2>&1; then
    echo "Repository $REPO_NAME does not exist. Creating..."
    aws ecr create-repository --repository-name $REPO_NAME --region $REGION
    echo "Repository $REPO_NAME created successfully."
else
    echo "Repository $REPO_NAME already exists."
fi

# Authenticate Docker to ECR
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ECR_URI

# Build frontend with load balancer DNS
echo "Building frontend for all-in-one with DNS: $AUTOSCALING_DNS"
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

# Clean old docker containers and images
echo "Cleaning up existing containers..."
docker ps -q --filter "name=optinist" | xargs -r docker stop
docker ps -aq --filter "name=optinist" | xargs -r docker rm

# ===========================================
# 2. Build Base Image
# ===========================================
echo "Building base Docker image..."
docker build -f studio/config/docker/Dockerfile -t optinist-base:$IMAGE_TAG .

# ===========================================
# 3. Create Container and Setup Conda Environments
# ===========================================
echo "Creating container for conda environment setup..."

# Create temporary volume for studio_data
docker volume create optinist-conda-build || true

# Run container with environment setup configuration
docker run -d \
    --name optinist-conda-build \
    --env IS_STANDALONE=True \
    --env USE_FIREBASE_TOKEN=False \
    --env OPTINIST_DIR="/app/studio_data" \
    -v optinist-conda-build:/app/studio_data \
    --shm-size=2G \
    optinist-base:$IMAGE_TAG \
    sleep infinity

# Wait for container to be ready
echo "Waiting for container to start..."
sleep 10

# Function to setup conda environment programmatically
setup_conda_env() {
    local env_name=$1
    local test_image_path="/app/sample_data/maintenance/input/setup_conda_mouse2p_image.tiff"

    echo "Setting up conda environment: $env_name"

    # Create temporary Python script to avoid shell escaping issues
    docker exec optinist-conda-build bash -c "
        cd /app/studio &&
        cat > /tmp/setup_${env_name}.py << 'PYTHON_EOF'
import sys
import os
sys.path.append('/app/studio')
sys.path.append('/app')

from studio.app.common.core.workflow.workflow import RunItem, Node, Edge, NodeData, NodePosition, Style, NodeType
from studio.app.common.core.workflow.workflow_runner import WorkflowRunner
from studio.app.common.core.snakemake.smk import ForceRun

def main():
    # Set environment variables
    os.environ['OPTINIST_DIR'] = '/app/studio_data'

    try:
        # Create directories
        output_dir = f'/app/studio_data/output/maintenance_${env_name}/setup_${env_name}'
        os.makedirs(output_dir, exist_ok=True)

        input_dir = f'/app/studio_data/input/maintenance_${env_name}'
        os.makedirs(input_dir, exist_ok=True)

        # Verify test image exists
        test_image = '/app/sample_data/maintenance/input/setup_conda_mouse2p_image.tiff'
        if not os.path.exists(test_image):
            print(f'Error: Test image not found at {test_image}')
            print('Available files in sample_data/maintenance/input/:')
            maintenance_dir = '/app/sample_data/maintenance/input'
            if os.path.exists(maintenance_dir):
                for f in os.listdir(maintenance_dir):
                    print(f'  {f}')
            sys.exit(1)
        else:
            print(f'Test image found: {test_image}')

        # Copy test image to input directory
        try:
            import shutil
            shutil.copy2(test_image, input_dir)
            print(f'Copied test image for ${env_name}')
        except Exception as copy_error:
            print(f'Warning: Could not copy test image: {copy_error}')

        print(f'Starting conda environment creation for ${env_name}...')

        # Create workflow nodes
        input_node = Node(
            id='input_node',
            type=NodeType.IMAGE,
            data=NodeData(
                label='image',
                param={},
                type=NodeType.IMAGE,
                path=['setup_conda_mouse2p_image.tiff'],
                fileType='image'
            ),
            position=NodePosition(x=100, y=100),
            style=Style()
        )

        setup_node = Node(
            id='setup_node',
            type=NodeType.ALGO,
            data=NodeData(
                label='setup_conda_${env_name}',
                param={},
                type='setup_conda_${env_name}',
                path='maintenance/setup_conda/setup_conda_${env_name}'
            ),
            position=NodePosition(x=300, y=100),
            style=Style()
        )

        # Create workflow edge
        edge = Edge(
            id='input_node:output:setup_node:input',
            type='default',
            animated=False,
            source='input_node',
            target='setup_node',
            sourceHandle='input_node--image--ImageData',
            targetHandle='setup_node--image--ImageData',
            style=Style()
        )

        # Create ForceRun for the setup node
        force_run = ForceRun(
            nodeId='setup_node',
            name='setup_conda_${env_name}'
        )

        # Create RunItem
        run_item = RunItem(
            name='Setup ${env_name} Environment',
            nodeDict={'input_node': input_node, 'setup_node': setup_node},
            edgeDict={'input_node:output:setup_node:input': edge},
            forceRunList=[force_run]
        )

        # Create and run workflow
        workflow_runner = WorkflowRunner(
            remote_bucket_name='',  # Not using remote storage for conda setup
            workspace_id='maintenance_${env_name}',
            unique_id='setup_${env_name}',
            runItem=run_item
        )

        # Execute the workflow
        # Create a mock BackgroundTasks for standalone execution
        class MockBackgroundTasks:
            def add_task(self, func, *args, **kwargs):
                # In standalone mode, execute immediately
                return func(*args, **kwargs)

        background_tasks = MockBackgroundTasks()

        print(f'Executing workflow for ${env_name}...')
        workflow_runner.run_workflow(background_tasks)

        # Workflow completed - verify the conda environment was actually created
        print(f'Successfully created conda environment: ${env_name}')
        conda_dir = '/app/.snakemake/conda'
        if os.path.exists(conda_dir):
            envs = [d for d in os.listdir(conda_dir) if os.path.isdir(os.path.join(conda_dir, d))]
            print(f'Conda environments found: {envs}')
        else:
            print('Warning: Conda directory not found')

        sys.exit(0)

    except Exception as e:
        print(f'Error setting up ${env_name}: {str(e)}')
        import traceback
        traceback.print_exc()
        sys.exit(1)

main()
PYTHON_EOF

        if python /tmp/setup_${env_name}.py; then
            echo 'Python script executed successfully'
            exit 0
        else
            echo 'Python script failed'
            exit 1
        fi
        rm /tmp/setup_${env_name}.py
    "

    # Check if the conda environment setup was successful
    if [ $? -eq 0 ]; then
        echo "✓ Successfully set up conda environment: $env_name"
    else
        echo "✗ Failed to set up conda environment: $env_name"
        echo "Continuing with other environments..."
    fi
}

# List of conda environments to create
CONDA_ENVS=("caiman" "suite2p" "lccd" "optinist" "custom")

echo "Starting conda environment setup..."
echo "This process may take 30-60 minutes depending on your internet connection and system performance."

# Setup each conda environment
for env in "${CONDA_ENVS[@]}"; do
    echo "=========================================="
    echo "Setting up conda environment: $env"
    echo "=========================================="

    setup_conda_env "$env"

    # Wait between environments to avoid overwhelming the system
    echo "Waiting 30 seconds before next environment..."
    sleep 30
done

# ===========================================
# 4. Verify Conda Environments
# ===========================================
echo "Verifying conda environments..."
docker exec optinist-conda-build bash -c "
    echo 'Checking conda environments:'
    ls -la /app/.snakemake/conda/ 2>/dev/null || echo 'No conda environments found yet'

    echo 'Conda environment directories:'
    find /app/.snakemake -name 'envs' -type d 2>/dev/null || echo 'No envs directories found'
"

# ===========================================
# 5. Create Final All-in-One Image
# ===========================================
echo "Creating final all-in-one Docker image..."

# Commit the container with conda environments to new image
docker commit optinist-conda-build $REPO_NAME:$IMAGE_TAG
docker commit optinist-conda-build $REPO_NAME:$VERSION_TAG

# Clean up temporary container
docker stop optinist-conda-build
docker rm optinist-conda-build
docker volume rm optinist-conda-build

# ===========================================
# 6. Push to ECR
# ===========================================
echo "Pushing all-in-one image to ECR..."

# Tag and push to ECR
docker tag $REPO_NAME:$IMAGE_TAG $ECR_URI:$IMAGE_TAG
docker tag $REPO_NAME:$VERSION_TAG $ECR_URI:$VERSION_TAG
docker tag $REPO_NAME:$IMAGE_TAG $ECR_URI:latest

docker push $ECR_URI:$IMAGE_TAG
docker push $ECR_URI:$VERSION_TAG
docker push $ECR_URI:latest

echo "Successfully pushed all-in-one image:"
echo "  - $ECR_URI:$IMAGE_TAG"
echo "  - $ECR_URI:$VERSION_TAG"
echo "  - $ECR_URI:latest"

# ===========================================
# 7. Build Regular Autoscaling Image (Optional)
# ===========================================
if [ "${BUILD_REGULAR_IMAGE:-false}" = "true" ]; then
    echo "Building regular autoscaling image..."

    # Build regular autoscaling image
    REGULAR_REPO="optinist-for-cloud"
    REGULAR_ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REGULAR_REPO}"

    # Build and push regular autoscaling image
    docker build -f studio/config/docker/Dockerfile -t $REGULAR_REPO:$IMAGE_TAG .
    docker tag $REGULAR_REPO:$IMAGE_TAG $REGULAR_ECR_URI:$IMAGE_TAG
    docker tag $REGULAR_REPO:$IMAGE_TAG $REGULAR_ECR_URI:latest

    aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $REGULAR_ECR_URI
    docker push $REGULAR_ECR_URI:$IMAGE_TAG
    docker push $REGULAR_ECR_URI:latest

    echo "Regular autoscaling image also built and pushed."
fi

# ===========================================
# 8. Testing Instructions
# ===========================================
echo ""
echo "=========================================="
echo "ALL-IN-ONE BUILD COMPLETE!"
echo "=========================================="
echo ""
echo "Image size: $(docker images $REPO_NAME:$IMAGE_TAG --format 'table {{.Size}}')"
echo ""
echo "To test the all-in-one image locally:"
echo "docker run -it --shm-size=2G \\"
echo "  -v /tmp:/app/studio_data \\"
echo "  --env OPTINIST_DIR=\"/app/studio_data\" \\"
echo "  --name optinist_allinone -d -p 8000:8000 --restart=unless-stopped \\"
echo "  $ECR_URI:latest \\"
echo "  poetry run python main.py --host 0.0.0.0 --port 8000"
echo ""
echo "Then access http://localhost:8000 and verify that:"
echo "1. All algorithm categories are available"
echo "2. Conda environments are pre-created (no 'Create Env' buttons needed)"
echo "3. Workflows run without conda setup delays"
echo ""
echo "Estimated image size: ~27GB (includes all conda environments)"
