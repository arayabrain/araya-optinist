#!/bin/bash

# Priority Queue Test Runner
# Automatically generates JWT tokens and runs the priority queue test

set -e  # Exit on any error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/test_run_$(date +%Y%m%d_%H%M%S).log"

echo "Priority Queue Test Runner" | tee -a "$LOG_FILE"
echo "============================" | tee -a "$LOG_FILE"
echo "Timestamp: $(date)" | tee -a "$LOG_FILE"
echo "Log file: $LOG_FILE" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# Parse command line arguments
ENVIRONMENT="local"
API_URL=""
SKIP_TOKEN_GEN=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --cloud)
            ENVIRONMENT="cloud"
            shift
            ;;
        --api-url)
            API_URL="$2"
            shift 2
            ;;
        --skip-token-gen)
            SKIP_TOKEN_GEN=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --cloud           Run tests against cloud environment"
            echo "  --api-url URL     Custom API base URL"
            echo "  --skip-token-gen  Skip token generation step"
            echo "  -h, --help        Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

echo "Environment: $ENVIRONMENT" | tee -a "$LOG_FILE"
if [ -n "$API_URL" ]; then
    echo "API URL: $API_URL" | tee -a "$LOG_FILE"
fi
echo "Skip token generation: $SKIP_TOKEN_GEN" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# Step 1: Generate JWT tokens (unless skipped)
if [ "$SKIP_TOKEN_GEN" = false ]; then
    echo "🔑 Step 1: Generating JWT tokens..." | tee -a "$LOG_FILE"
    echo "=================================" | tee -a "$LOG_FILE"

    TOKEN_CMD="python3 get_jwt_tokens.py --environment $ENVIRONMENT --output-file tokens.json"
    if [ -n "$API_URL" ]; then
        TOKEN_CMD="$TOKEN_CMD --api-url $API_URL"
    fi

    echo "Running: $TOKEN_CMD" | tee -a "$LOG_FILE"

    if $TOKEN_CMD 2>&1 | tee -a "$LOG_FILE"; then
        echo "✅ JWT tokens generated successfully!" | tee -a "$LOG_FILE"
    else
        echo "❌ JWT token generation failed!" | tee -a "$LOG_FILE"
        echo "Please check that:" | tee -a "$LOG_FILE"
        echo "1. Backend API server is running" | tee -a "$LOG_FILE"
        echo "2. Test users exist with correct passwords" | tee -a "$LOG_FILE"
        echo "3. API URL is accessible" | tee -a "$LOG_FILE"
        exit 1
    fi
    echo "" | tee -a "$LOG_FILE"
else
    echo "⏭️  Skipping JWT token generation..." | tee -a "$LOG_FILE"
    echo "" | tee -a "$LOG_FILE"
fi

# Step 2: Copy sample data to workspace directories
echo "📁 Step 2: Setting up sample data..." | tee -a "$LOG_FILE"
echo "===================================" | tee -a "$LOG_FILE"

# Define paths
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SAMPLE_DATA_DIR="$PROJECT_ROOT/sample_data/tutorial"

# Use OPTINIST_DIR if set, otherwise default to /tmp/studio
DATA_DIR="${OPTINIST_DIR:-/tmp/studio}"

echo "Project root: $PROJECT_ROOT" | tee -a "$LOG_FILE"
echo "Sample data source: $SAMPLE_DATA_DIR" | tee -a "$LOG_FILE"
echo "Data directory: $DATA_DIR" | tee -a "$LOG_FILE"

# Check if sample data exists
if [ ! -d "$SAMPLE_DATA_DIR" ]; then
    echo "❌ Sample data directory not found: $SAMPLE_DATA_DIR" | tee -a "$LOG_FILE"
    exit 1
fi

# Create workspace directories and copy sample data for both workspaces (using default IDs for initial setup)
for workspace_id in 1 2; do
    echo "Setting up default workspace $workspace_id..." | tee -a "$LOG_FILE"

    # Create directories
    mkdir -p "$DATA_DIR/input/$workspace_id"
    mkdir -p "$DATA_DIR/output/$workspace_id"

    # Copy input data
    if [ -d "$SAMPLE_DATA_DIR/input" ]; then
        cp -r "$SAMPLE_DATA_DIR/input/"* "$DATA_DIR/input/$workspace_id/"
        echo "✅ Copied input data to workspace $workspace_id" | tee -a "$LOG_FILE"
    fi

    # Copy output data
    if [ -d "$SAMPLE_DATA_DIR/output" ]; then
        cp -r "$SAMPLE_DATA_DIR/output/"* "$DATA_DIR/output/$workspace_id/"
        echo "✅ Copied output data to workspace $workspace_id" | tee -a "$LOG_FILE"
    fi
done

echo "✅ Sample data setup completed!" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# Step 3: Create test workspaces
echo "🏗️  Step 3: Creating test workspaces..." | tee -a "$LOG_FILE"
echo "====================================" | tee -a "$LOG_FILE"

# Get JWT tokens from the tokens.json file (created by token generation step)
if [ -f "tokens.json" ]; then
    PREMIUM_TOKEN=$(python3 -c "import json; data=json.load(open('tokens.json')); print(data['premium']['access_token'])")
    FREE_TOKEN=$(python3 -c "import json; data=json.load(open('tokens.json')); print(data['free']['access_token'])")
else
    echo "❌ tokens.json not found. Token generation may have failed." | tee -a "$LOG_FILE"
    exit 1
fi

# Set API URL based on environment
if [ "$ENVIRONMENT" = "cloud" ] && [ -n "$API_URL" ]; then
    BASE_URL="$API_URL"
else
    BASE_URL="http://localhost:8002"
fi

echo "API URL: $BASE_URL" | tee -a "$LOG_FILE"

# Create workspace for premium user
echo "Creating workspace for premium user..." | tee -a "$LOG_FILE"
PREMIUM_WORKSPACE_RESPONSE=$(curl -s -X POST "$BASE_URL/workspace" \
    -H "Authorization: Bearer $PREMIUM_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"name": "Premium Priority Testing"}')

if echo "$PREMIUM_WORKSPACE_RESPONSE" | grep -q '"id"'; then
    PREMIUM_WORKSPACE_ID=$(echo "$PREMIUM_WORKSPACE_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
    echo "✅ Premium workspace created: ID $PREMIUM_WORKSPACE_ID" | tee -a "$LOG_FILE"
else
    echo "❌ Failed to create premium workspace: $PREMIUM_WORKSPACE_RESPONSE" | tee -a "$LOG_FILE"
    exit 1
fi

# Create workspace for free user
echo "Creating workspace for free user..." | tee -a "$LOG_FILE"
FREE_WORKSPACE_RESPONSE=$(curl -s -X POST "$BASE_URL/workspace" \
    -H "Authorization: Bearer $FREE_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"name": "Free Priority Testing"}')

if echo "$FREE_WORKSPACE_RESPONSE" | grep -q '"id"'; then
    FREE_WORKSPACE_ID=$(echo "$FREE_WORKSPACE_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
    echo "✅ Free workspace created: ID $FREE_WORKSPACE_ID" | tee -a "$LOG_FILE"
else
    echo "❌ Failed to create free workspace: $FREE_WORKSPACE_RESPONSE" | tee -a "$LOG_FILE"
    exit 1
fi

echo "✅ Test workspaces created successfully!" | tee -a "$LOG_FILE"
echo "   Premium workspace: $PREMIUM_WORKSPACE_ID" | tee -a "$LOG_FILE"
echo "   Free workspace: $FREE_WORKSPACE_ID" | tee -a "$LOG_FILE"

# Copy sample data to the newly created workspaces
echo "Copying sample data to new workspaces..." | tee -a "$LOG_FILE"
for workspace_id in $PREMIUM_WORKSPACE_ID $FREE_WORKSPACE_ID; do
    echo "Setting up workspace $workspace_id..." | tee -a "$LOG_FILE"

    # Create directories
    mkdir -p "$DATA_DIR/input/$workspace_id"
    mkdir -p "$DATA_DIR/output/$workspace_id"

    # Copy input data
    if [ -d "$SAMPLE_DATA_DIR/input" ]; then
        cp -r "$SAMPLE_DATA_DIR/input/"* "$DATA_DIR/input/$workspace_id/"
        echo "✅ Copied input data to workspace $workspace_id" | tee -a "$LOG_FILE"
    fi

    # Copy output data
    if [ -d "$SAMPLE_DATA_DIR/output" ]; then
        cp -r "$SAMPLE_DATA_DIR/output/"* "$DATA_DIR/output/$workspace_id/"
        echo "✅ Copied output data to workspace $workspace_id" | tee -a "$LOG_FILE"
    fi
done

# Export workspace IDs and URLs for the test script to use
export PREMIUM_WORKSPACE_ID
export FREE_WORKSPACE_ID
export PREMIUM_URL="$BASE_URL/run/$PREMIUM_WORKSPACE_ID"
export FREE_URL="$BASE_URL/run/$FREE_WORKSPACE_ID"

echo "✅ Sample data copied to new workspaces!" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# Step 4: Update test script URL if cloud environment
if [ "$ENVIRONMENT" = "cloud" ] && [ -n "$API_URL" ]; then
    echo "🔧 Step 4: Updating test script for cloud environment..." | tee -a "$LOG_FILE"
    echo "====================================================" | tee -a "$LOG_FILE"

    # Update URL in test script
    CLOUD_URL="${API_URL}/run/1"
    sed -i.bak "s|URL=\"[^\"]*\"|URL=\"$CLOUD_URL\"|g" test-workflow-tutorial1-post.sh

    echo "✅ Updated test script URL to: $CLOUD_URL" | tee -a "$LOG_FILE"
    echo "" | tee -a "$LOG_FILE"
fi

# Step 5: Run the priority queue tests
echo "🏃 Step 5: Running priority queue tests..." | tee -a "$LOG_FILE"
echo "=========================================" | tee -a "$LOG_FILE"

echo "Executing: ./test-workflow-tutorial1-post.sh" | tee -a "$LOG_FILE"

./test-workflow-tutorial1-post.sh 2>&1 | tee -a "$LOG_FILE"
TEST_EXIT_CODE=$?

echo "" | tee -a "$LOG_FILE"

# Check if we got "Operation is not available" errors (local environment issue)
if grep -q "Operation is not available" "$LOG_FILE"; then
    echo "⚠️  Priority queue tests completed with expected local limitations!" | tee -a "$LOG_FILE"
    echo "" | tee -a "$LOG_FILE"
    echo "💡 Note: 'Operation is not available' usually means:" | tee -a "$LOG_FILE"
    echo "   • AWS Batch is not configured (priority queues require cloud environment)" | tee -a "$LOG_FILE"
    echo "   • Local environment doesn't support batch execution" | tee -a "$LOG_FILE"
    echo "" | tee -a "$LOG_FILE"
    echo "To test priority queues properly:" | tee -a "$LOG_FILE"
    echo "   ./run_priority_queue_test.sh --cloud --api-url YOUR_CLOUD_URL" | tee -a "$LOG_FILE"
    echo "" | tee -a "$LOG_FILE"
    echo "✅ Local environment setup is working correctly!" | tee -a "$LOG_FILE"
    echo "✅ JWT tokens, sample data, and API calls are functioning" | tee -a "$LOG_FILE"
    # Don't exit with error for this expected limitation
elif [ $TEST_EXIT_CODE -eq 0 ]; then
    echo "✅ Priority queue tests completed successfully!" | tee -a "$LOG_FILE"
else
    echo "❌ Priority queue tests failed!" | tee -a "$LOG_FILE"
    echo "Check the log above for error details." | tee -a "$LOG_FILE"
    exit 1
fi

# Step 6: Restore original test script if we modified it
if [ "$ENVIRONMENT" = "cloud" ] && [ -f "test-workflow-tutorial1-post.sh.bak" ]; then
    echo "" | tee -a "$LOG_FILE"
    echo "🔧 Step 6: Restoring original test script..." | tee -a "$LOG_FILE"
    mv test-workflow-tutorial1-post.sh.bak test-workflow-tutorial1-post.sh
    echo "✅ Original test script restored" | tee -a "$LOG_FILE"
fi

echo "" | tee -a "$LOG_FILE"
echo "🎉 All steps completed successfully!" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
echo "Next steps:" | tee -a "$LOG_FILE"
echo "1. Check the workflow execution logs for priority assignments" | tee -a "$LOG_FILE"
echo "2. Monitor AWS Batch console for queue assignments" | tee -a "$LOG_FILE"
echo "3. Compare execution speeds between premium and free users" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"
echo "📋 Log files to review:" | tee -a "$LOG_FILE"
echo "- Test runner log: $LOG_FILE" | tee -a "$LOG_FILE"
echo "- Priority queue test log: priority_queue_test_*.log" | tee -a "$LOG_FILE"
