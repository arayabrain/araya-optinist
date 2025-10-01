# Priority Queue Testing Scripts

This directory contains automated scripts for testing priority queue functionality between premium and free users.

## Quick Start

### Local Testing (Setup Verification)
```bash
# Start backend server first
python main.py --port 8002

# In another terminal - This verifies JWT tokens and sample data setup
cd studio/scripts
./run_priority_queue_test.sh
```
**Note:** Priority queues require AWS Batch, so local testing will show "Operation is not available" but verifies that tokens and data setup work correctly.

### Cloud Testing
```bash
cd studio/scripts
./run_priority_queue_test.sh --cloud --api-url https://your-cloud-api.com
```

### Python Test Runner (Recommended)
```bash
# Comprehensive test with monitoring
python priority_queue_test.py

# Cloud testing
python priority_queue_test.py --environment cloud --api-url https://your-cloud-api.com

# Quick submission test only
python priority_queue_test.py --submission-only

# Free workspace isolation test
python priority_queue_test.py --test-free-only
```

## What These Scripts Do

### 1. `get_jwt_tokens.py` - JWT Token Generator
- Automatically logs in as both test users (premium and free)
- Extracts JWT tokens from API responses
- Verifies tokens work by making test API calls
- Updates test script with fresh tokens

### 2. `priority_queue_test.py` - Comprehensive Priority Queue Tester (Main Script)
- **Environment Setup**: Automatically sets up conda environment and poetry dependencies
- **JWT Token Management**: Generates fresh tokens with detailed analysis and debugging
- **Workspace Management**: Creates dedicated test workspaces for premium and free users
- **Sample Data Setup**: Copies tutorial sample data to workspace directories
- **Workflow Submission**: Submits configurable numbers of workflows for each user type
- **Execution Monitoring**: Real-time monitoring with continuous report updates
- **Detailed Analysis**: Provides timing analysis, priority queue effectiveness comparison
- **Multiple Test Modes**: Supports submission-only, free-only isolation testing, and full monitoring

### 3. `run_priority_queue_test.sh` - Shell-based Test Runner
- Legacy shell script that orchestrates the testing process
- Generates tokens → Creates workspaces → Sets up data → Runs tests
- Supports both local and cloud environments
- Uses `test-workflow-tutorial1-post.sh` for workflow submission

## Expected Results

- PRIORITY ASSIGNMENT - Shows tier (free/premium) and priority (1/10)
- WORKFLOW COMPLETED - Shows completion with duration

### AWS Batch Console
- Premium workflows: Assigned to paid job queue, priority=10
- Free workflows: Assigned to free job queue, priority=1
- Premium jobs should complete faster

### Test Logs
- `test_run_YYYYMMDD_HHMMSS.log` - Complete test execution log (shell script)
- `simple_priority_test_results_YYYYMMDD.json` - Detailed JSON results with timing analysis (Python script)
- Continuous monitoring reports updated every 10 seconds until completion

## Test Users

The scripts use these pre-configured test users from terraform.tfvars:

**Premium User:**
- Expected Priority: 10

**Free User:**
- Expected Priority: 1

## Troubleshooting

### "Login failed" errors
- Ensure backend server is running on correct port
- Verify test users exist in database
- Check user passwords are correct

### "Token verification failed" errors
- Check API endpoints are accessible
- Verify user permissions and roles
- Ensure database connection is working

### "No workflows submitted" errors
- Verify test data files exist (sample_mouse2p_image.tiff, etc.)
- Check workspace permissions
- Ensure sufficient storage quota

## Manual Testing

### Using Python Script (Recommended)
```bash
# Just generate tokens (with analysis)
python3 get_jwt_tokens.py --environment local

# Run comprehensive test with custom settings
python3 priority_queue_test.py --premium-count 5 --free-count 5

# Run with existing tokens (skip generation)
python3 priority_queue_test.py --skip-token-gen

# Save tokens to file
python3 get_jwt_tokens.py --output-file tokens.json
```

### Using Shell Scripts (Legacy)
```bash
# Generate tokens
python3 get_jwt_tokens.py --environment local

# Run the shell-based test runner
./run_priority_queue_test.sh

# Or just run workflow submission
./test-workflow-tutorial1-post.sh
```

## Configuration

### Python Script Configuration
```bash
# Cloud testing with custom settings
python3 priority_queue_test.py --environment cloud --api-url https://your-cloud-instance.com --premium-count 10 --free-count 10

# Custom timeout for workflow execution (default: 30 minutes)
python3 priority_queue_test.py --timeout 3600

# Custom workspace IDs (if you have existing workspaces)
python3 priority_queue_test.py --premium-workspace 123 --free-workspace 456
```

### Shell Script Configuration
```bash
# Cloud testing
./run_priority_queue_test.sh --cloud --api-url https://your-cloud-instance.com

# Skip token generation (use existing tokens.json)
./run_priority_queue_test.sh --skip-token-gen
```

### Test User Configuration
The scripts automatically load test user credentials from multiple sources in this priority order:

1. **terraform.tfvars** (automatically detected from `studio/config/terraform/terraform.tfvars`)
2. **.env file** (create from `.env.example` in `studio/scripts/`)
3. **Environment variables**
4. **Hardcoded fallback** (not recommended)

#### Option 1: .env File (Recommended for Development)
```bash
cd studio/scripts
cp .env.example .env
# Edit .env with your test user credentials
```

#### Option 2: Environment Variables
```bash
export PREMIUM_USER_EMAIL="your_premium_user@example.com"
export PREMIUM_USER_PASSWORD="your_password"
export FREE_USER_EMAIL="your_free_user@example.com"
export FREE_USER_PASSWORD="your_password"
```

#### Option 3: terraform.tfvars (Production)
Test users are automatically loaded from the existing terraform configuration.

### Different Port
```bash
# Python script
python3 priority_queue_test.py --base-url http://localhost:8000

# Token generator
python3 get_jwt_tokens.py --api-url http://localhost:8000
```
