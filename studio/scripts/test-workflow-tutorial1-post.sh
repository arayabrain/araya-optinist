#!/bin/bash

# Priority Queue Testing Script
# Tests workflow execution speed between premium and free users

# URLs - can be overridden by environment variables
PREMIUM_URL="${PREMIUM_URL:-http://localhost:8002/run/1}"
FREE_URL="${FREE_URL:-http://localhost:8002/run/1}"
DATA="./test-workflow-tutorial1-postdata.json"

# JWT tokens - load from tokens.json (required)
if [ ! -f "tokens.json" ]; then
    echo "Error: tokens.json file not found!"
    echo "Please run: python get_jwt_tokens.py --output-file tokens.json"
    exit 1
fi

PREMIUM_JWT_TOKEN=$(python3 -c "import json; data=json.load(open('tokens.json')); print(data['premium']['access_token'])" 2>/dev/null)
FREE_JWT_TOKEN=$(python3 -c "import json; data=json.load(open('tokens.json')); print(data['free']['access_token'])" 2>/dev/null)

if [ -z "$PREMIUM_JWT_TOKEN" ] || [ -z "$FREE_JWT_TOKEN" ]; then
    echo "Error: Failed to load JWT tokens from tokens.json"
    echo "Please run: python get_jwt_tokens.py --output-file tokens.json"
    exit 1
fi

# Number of workflows to submit for each user type
PREMIUM_COUNT=5
FREE_COUNT=5

# Log file for results
LOG_FILE="priority_queue_test_$(date +%Y%m%d_%H%M%S).log"

echo "=== Priority Queue Testing Started ===" | tee -a "$LOG_FILE"
echo "Timestamp: $(date)" | tee -a "$LOG_FILE"
echo "Premium workflows: $PREMIUM_COUNT" | tee -a "$LOG_FILE"
echo "Free workflows: $FREE_COUNT" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# Function to submit workflows with timing
submit_workflows() {
    local jwt_token=$1
    local count=$2
    local user_type=$3
    local url=$4  # Add URL parameter
    local workflow_ids=()

    echo "--- Submitting $count workflows for $user_type user ---" | tee -a "$LOG_FILE"
    echo "URL: $url" | tee -a "$LOG_FILE"
    start_time=$(date +%s.%N)

    for i in $(seq 1 $count); do
        # Generate unique workflow ID
        workflow_id="${user_type}_workflow_${i}_$(date +%s%3N)"
        workflow_ids+=("$workflow_id")

        echo "Submitting $user_type workflow $i (ID: $workflow_id)" | tee -a "$LOG_FILE"

        # Create temporary data file with unique workflow ID
        temp_data="temp_${workflow_id}.json"
        sed "s/\"name\": \"suite2p performance test\"/\"name\": \"${workflow_id}\"/" "$DATA" > "$temp_data"

        # Submit workflow and capture response
        response=$(curl -s --data @"$temp_data" \
            -H "Content-Type: application/json" \
            -H "Authorization:Bearer $jwt_token" \
            "$url" 2>&1)

        echo "Response: $response" | tee -a "$LOG_FILE"

        # Clean up temp file
        rm -f "$temp_data"

        # Small delay between submissions to avoid overwhelming the server
        sleep 0.5
    done

    end_time=$(date +%s.%N)
    submission_duration=$(echo "$end_time - $start_time" | bc)

    echo "All $user_type workflows submitted in ${submission_duration}s" | tee -a "$LOG_FILE"
    echo "" | tee -a "$LOG_FILE"
}

# Submit workflows for both user types in parallel
echo "Starting parallel workflow submission..." | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# Submit premium user workflows in background
{
    submit_workflows "$PREMIUM_JWT_TOKEN" "$PREMIUM_COUNT" "premium" "$PREMIUM_URL"
} &
premium_pid=$!

# Submit free user workflows in background
{
    submit_workflows "$FREE_JWT_TOKEN" "$FREE_COUNT" "free" "$FREE_URL"
} &
free_pid=$!

# Wait for both submission processes to complete
wait $premium_pid
wait $free_pid

echo "=== All workflows submitted ===" | tee -a "$LOG_FILE"
echo "Check logs and AWS Batch console to monitor execution speed differences" | tee -a "$LOG_FILE"
echo "Log file: $LOG_FILE" | tee -a "$LOG_FILE"
echo ""
echo "Next steps:"
echo "1. Monitor workflow execution in the application logs"
echo "2. Check AWS Batch console for queue assignments and execution order"
echo "3. Look for priority logging in workflow execution logs"
