#!/usr/bin/env bash
set -e

# ============================================================================
# OptiNiSt Application Setup Script
# ============================================================================
# This script configures OptiNiSt application on EC2 instances by:
# - Reading secrets from AWS Secrets Manager
# - Discovering infrastructure via AWS CLI
# - Creating configuration files
# - Initializing the database
#
# SENSITIVE VALUES ARE FETCHED AT RUNTIME
# ============================================================================

# ============================================================================
# CONFIGURATION CONSTANTS
# ============================================================================

# Logging
LOGFILE="/var/log/app-setup.log"

# Application Paths
APP_CONFIG_BASE="/opt/optinist/optinist-for-cloud/studio/config"
APP_CONFIG_AUTH_DIR="${APP_CONFIG_BASE}/auth"
FIREBASE_PRIVATE_KEY_PATH="${APP_CONFIG_AUTH_DIR}/firebase_private.json"
FIREBASE_CONFIG_PATH="${APP_CONFIG_AUTH_DIR}/firebase_config.json"
APP_ENV_PATH="${APP_CONFIG_BASE}/.env"

# Database Configuration
MYSQL_PORT=3306
DB_INIT_SQL_FILE="/tmp/init_optinist_db.sql"
DB_INIT_FILE_PERMISSIONS=644

# Retry Parameters
DB_CONNECTION_MAX_ATTEMPTS=30
DB_CONNECTION_RETRY_DELAY=10
DB_INIT_MAX_ATTEMPTS=10
DB_INIT_RETRY_DELAY=30
ECS_AGENT_MAX_ATTEMPTS=10
ECS_AGENT_RETRY_DELAY=30

# Default IDs
DEFAULT_ORG_ID=1
DEFAULT_USER_ID=1
ADMIN_ROLE_ID=1

# Role Definitions
ROLE_ADMIN_ID=1
ROLE_ADMIN_NAME="admin"
ROLE_DATA_MANAGER_ID=10
ROLE_DATA_MANAGER_NAME="data manager"
ROLE_OPERATOR_ID=20
ROLE_OPERATOR_NAME="operator"
ROLE_GUEST_OPERATOR_ID=30
ROLE_GUEST_OPERATOR_NAME="guest operator"

# Tax Configuration
TAX_TYPE="sales_tax"
TAX_NAME="Sales Tax"
TAX_RATE=0.10
TAX_IS_ACTIVE=1

# Subscription Configuration
FREE_PLAN_ID=1
FREE_PLAN_NAME="'Free'"
FREE_PLAN_PRICE=0
PREMIUM_PLAN_ID=2
PREMIUM_PLAN_NAME="'Premium'"
PREMIUM_PLAN_PRICE=2999
DEFAULT_BILLING_CYCLE_DAYS=30
CURRENCY_USD=840
STATUS_ACTIVE=1
ADMIN_SUBSCRIPTION_DAYS=365

# Storage Configuration
ADMIN_STORAGE_USAGE_BYTES=0
ADMIN_STORAGE_QUOTA_BYTES=214748364800  # 200 GB

# AWS Region (detect from instance metadata or use default)
AWS_REGION="${AWS_DEFAULT_REGION:-ap-northeast-1}"

# ============================================================================
# LOGGING SETUP
# ============================================================================

exec > >(tee -a "$LOGFILE") 2>&1
echo "$(date): Starting application setup script"

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

# Function for retries
retry_command() {
    local max_attempts=$1
    local delay=$2
    shift 2
    local command="$@"

    for i in $(seq 1 $max_attempts); do
        echo "$(date): Attempting: $command (attempt $i/$max_attempts)"
        if eval "$command"; then
            echo "$(date): Success: $command"
            return 0
        else
            echo "$(date): Failed attempt $i/$max_attempts"
            [ $i -lt $max_attempts ] && sleep $delay
        fi
    done

    echo "$(date): ERROR: Command failed after $max_attempts attempts: $command"
    return 1
}

# Function to read secret from AWS Secrets Manager
get_secret() {
    local secret_name=$1
    local secret_value=$(aws secretsmanager get-secret-value \
        --secret-id "$secret_name" \
        --region "$AWS_REGION" \
        --query 'SecretString' \
        --output text 2>/dev/null)

    if [ $? -ne 0 ] || [ -z "$secret_value" ]; then
        echo "$(date): ERROR: Failed to retrieve secret: $secret_name"
        return 1
    fi

    echo "$secret_value"
}

# Function to extract JSON value
get_json_value() {
    local json=$1
    local key=$2
    echo "$json" | python3 -c "import sys, json; print(json.load(sys.stdin)['$key'])" 2>/dev/null
}

# ============================================================================
# WAIT FOR ECS AGENT
# ============================================================================

echo "$(date): Waiting for ECS agent to be ready"
retry_command $ECS_AGENT_MAX_ATTEMPTS $ECS_AGENT_RETRY_DELAY "curl -s http://localhost:51678/v1/metadata >/dev/null"

# ============================================================================
# FETCH SECRETS FROM AWS SECRETS MANAGER
# ============================================================================

echo "$(date): Fetching secrets from AWS Secrets Manager"

# Firebase Configuration
echo "$(date): Fetching Firebase configuration..."
FIREBASE_CONFIG_JSON=$(get_secret "subscr-optinist/firebase/config")
FIREBASE_PRIVATE_JSON=$(get_secret "subscr-optinist/firebase/private-key")

# Database Configuration
echo "$(date): Fetching database configuration..."
DB_CONFIG_JSON=$(get_secret "subscr-optinist/database/config")
MYSQL_USER=$(get_json_value "$DB_CONFIG_JSON" "username")
MYSQL_PASSWORD=$(get_json_value "$DB_CONFIG_JSON" "password")
MYSQL_DATABASE=$(get_json_value "$DB_CONFIG_JSON" "database")

# Application Configuration
echo "$(date): Fetching application configuration..."
APP_CONFIG_JSON=$(get_secret "subscr-optinist/app/config")
OPTINIST_SECRET_KEY=$(get_json_value "$APP_CONFIG_JSON" "secret_key")
OPTINIST_ORG_NAME=$(get_json_value "$APP_CONFIG_JSON" "org_name")
OPTINIST_ADMIN_NAME=$(get_json_value "$APP_CONFIG_JSON" "admin_name")
OPTINIST_ADMIN_EMAIL=$(get_json_value "$APP_CONFIG_JSON" "admin_email")
OPTINIST_ADMIN_UID=$(get_json_value "$APP_CONFIG_JSON" "admin_uid")

# ============================================================================
# DISCOVER AWS INFRASTRUCTURE
# ============================================================================

echo "$(date): Discovering AWS infrastructure..."

# Find RDS Proxy endpoint
echo "$(date): Finding RDS Proxy..."
RDS_PROXY_ENDPOINT=$(aws rds describe-db-proxies \
    --region "$AWS_REGION" \
    --query "DBProxies[?contains(DBProxyName, 'subscr-optinist')].Endpoint" \
    --output text 2>/dev/null | head -1)

if [ -z "$RDS_PROXY_ENDPOINT" ]; then
    echo "$(date): WARNING: RDS Proxy not found, trying direct RDS endpoint..."
    RDS_PROXY_ENDPOINT=$(aws rds describe-db-instances \
        --region "$AWS_REGION" \
        --query "DBInstances[?contains(DBInstanceIdentifier, 'subscr-optinist')].Endpoint.Address" \
        --output text 2>/dev/null | head -1)
fi

# Find S3 bucket
echo "$(date): Finding S3 bucket..."
S3_BUCKET=$(aws s3api list-buckets \
    --query "Buckets[?contains(Name, 'subscr-optinist-app-storage')].Name" \
    --output text 2>/dev/null | head -1)

if [ -z "$S3_BUCKET" ]; then
    echo "$(date): WARNING: Could not find S3 bucket with expected naming pattern"
    # Try to find any bucket with subscr-optinist tag
    S3_BUCKET=$(aws resourcegroupstaggingapi get-resources \
        --resource-type-filters s3:bucket \
        --tag-filters "Key=Name,Values=*optinist*" \
        --region "$AWS_REGION" \
        --query "ResourceTagMappingList[0].ResourceARN" \
        --output text 2>/dev/null | cut -d':' -f6)
fi

echo "$(date): RDS Proxy Endpoint: $RDS_PROXY_ENDPOINT"
echo "$(date): S3 Bucket: $S3_BUCKET"

# ============================================================================
# CREATE CONFIGURATION FILES
# ============================================================================

echo "$(date): Creating configuration files"
mkdir -p "$APP_CONFIG_AUTH_DIR"

# Create .env file
cat > "$APP_ENV_PATH" << EOF
SECRET_KEY='${OPTINIST_SECRET_KEY}'
USE_FIREBASE_TOKEN=True
MYSQL_SERVER=${RDS_PROXY_ENDPOINT}
MYSQL_DATABASE=${MYSQL_DATABASE}
MYSQL_USER=${MYSQL_USER}
MYSQL_PASSWORD=${MYSQL_PASSWORD}
S3_DEFAULT_BUCKET_NAME=${S3_BUCKET}
EOF

# Create Firebase config files
echo "$FIREBASE_CONFIG_JSON" > "$FIREBASE_CONFIG_PATH"
echo "$FIREBASE_PRIVATE_JSON" > "$FIREBASE_PRIVATE_KEY_PATH"

echo "$(date): Configuration files created successfully"

# ============================================================================
# DATABASE INITIALIZATION
# ============================================================================

echo "$(date): Starting database initialization"

# Install MySQL client for database initialization
echo "$(date): Installing MySQL client"
apt-get update
apt-get install -y mysql-client-core-8.0 python3-pip

# Extract hostname from RDS endpoint (remove port if present)
RDS_HOST=$(echo "$RDS_PROXY_ENDPOINT" | cut -d':' -f1)

# Wait for database to be available
retry_command $DB_CONNECTION_MAX_ATTEMPTS $DB_CONNECTION_RETRY_DELAY "nc -z $RDS_HOST $MYSQL_PORT"

# Initialize database tables and users
echo "$(date): Initializing database tables"
cat > "$DB_INIT_SQL_FILE" << 'INIT_SQL'
-- Ensure user has mysql_native_password authentication for RDS Proxy compatibility
ALTER USER '${MYSQL_USER}'@'%' IDENTIFIED WITH mysql_native_password BY '${MYSQL_PASSWORD}';
FLUSH PRIVILEGES;

-- Create database if it doesn't exist
CREATE DATABASE IF NOT EXISTS ${MYSQL_DATABASE};
USE ${MYSQL_DATABASE};

-- Insert initial data
INSERT IGNORE INTO organization (name) VALUES ('${OPTINIST_ORG_NAME}');
INSERT IGNORE INTO roles (id, role) VALUES
  ($ROLE_ADMIN_ID, '$ROLE_ADMIN_NAME'),
  ($ROLE_DATA_MANAGER_ID, '$ROLE_DATA_MANAGER_NAME'),
  ($ROLE_OPERATOR_ID, '$ROLE_OPERATOR_NAME'),
  ($ROLE_GUEST_OPERATOR_ID, '$ROLE_GUEST_OPERATOR_NAME');

-- Default admin user with S3 bucket info
INSERT IGNORE INTO users (uid, organization_id, name, email, active, attributes)
VALUES ('${OPTINIST_ADMIN_UID}', $DEFAULT_ORG_ID, '${OPTINIST_ADMIN_NAME}', '${OPTINIST_ADMIN_EMAIL}', true, '{"remote_bucket_name": "${S3_BUCKET}"}');

INSERT IGNORE INTO user_roles (user_id, role_id) VALUES ($DEFAULT_USER_ID, $ADMIN_ROLE_ID);

UPDATE users SET attributes = JSON_MERGE_PATCH(IFNULL(attributes,'{}'), '{"remote_bucket_name": "${S3_BUCKET}"}') WHERE id = $DEFAULT_USER_ID;

-- Tax rates initialization
INSERT IGNORE INTO taxes (tax_type, tax_name, tax_rate, is_active, effective_date)
VALUES ('$TAX_TYPE', '$TAX_NAME', $TAX_RATE, $TAX_IS_ACTIVE, CURDATE());

-- Admin user storage quota initialization
INSERT IGNORE INTO user_storage_usage (user_id, storage_usage_bytes, storage_quota_bytes)
VALUES ($DEFAULT_USER_ID, $ADMIN_STORAGE_USAGE_BYTES, $ADMIN_STORAGE_QUOTA_BYTES);

-- Admin user premium subscription
INSERT IGNORE INTO subscription_users (plan_id, user_id, expiration)
VALUES ($PREMIUM_PLAN_ID, $DEFAULT_USER_ID, DATE_ADD(NOW(), INTERVAL $ADMIN_SUBSCRIPTION_DAYS DAY));

INIT_SQL

# Substitute variables in SQL file using Python for safe escaping
# This handles special characters in passwords and other values that could break sed or SQL
# Export variables so Python can access them safely from the environment
export MYSQL_USER MYSQL_PASSWORD MYSQL_DATABASE
export OPTINIST_ORG_NAME OPTINIST_ADMIN_UID OPTINIST_ADMIN_NAME OPTINIST_ADMIN_EMAIL
export S3_BUCKET DB_INIT_SQL_FILE

python3 << 'PYSUBST'
import os
import sys

# Read the SQL template
sql_file = os.environ.get('DB_INIT_SQL_FILE')
if not sql_file:
    print("ERROR: DB_INIT_SQL_FILE not set", file=sys.stderr)
    sys.exit(1)

with open(sql_file, 'r') as f:
    content = f.read()

# Variables to substitute (read from environment)
var_names = [
    'MYSQL_USER',
    'MYSQL_PASSWORD',
    'MYSQL_DATABASE',
    'OPTINIST_ORG_NAME',
    'OPTINIST_ADMIN_UID',
    'OPTINIST_ADMIN_NAME',
    'OPTINIST_ADMIN_EMAIL',
    'S3_BUCKET'
]

# Perform substitutions with proper SQL escaping
for var_name in var_names:
    value = os.environ.get(var_name, '')
    # Escape single quotes for SQL (double them)
    safe_value = value.replace("'", "''")
    # Replace the placeholder ${VAR_NAME}
    placeholder = '${' + var_name + '}'
    content = content.replace(placeholder, safe_value)

# Write the result
with open(sql_file, 'w') as f:
    f.write(content)

print("SQL variable substitution completed successfully")
PYSUBST

chmod $DB_INIT_FILE_PERMISSIONS "$DB_INIT_SQL_FILE"

# Wait for database to be ready and execute initialization
max_attempts=$DB_INIT_MAX_ATTEMPTS
attempt=1
while [ $attempt -le $max_attempts ]; do
  echo "$(date): Attempting to initialize database (attempt $attempt/$max_attempts)"
  if mysql -h "$RDS_HOST" -P $MYSQL_PORT -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" < "$DB_INIT_SQL_FILE"; then
    echo "$(date): Database initialization successful"
    break
  else
    echo "$(date): Database initialization attempt $attempt failed, waiting to retry..."
    sleep $DB_INIT_RETRY_DELAY
    attempt=$((attempt+1))
  fi
done

if [ $attempt -gt $max_attempts ]; then
  echo "$(date): ERROR: Failed to initialize the database after $max_attempts attempts"
fi

# ============================================================================
# FIREBASE ADMIN EMAIL VERIFICATION
# ============================================================================

echo "$(date): Verifying admin email in Firebase"

# Install dependencies for Firebase Admin SDK
echo "$(date): Installing firebase-admin..."
python3 -m pip install firebase-admin

# Run verification script
echo "$(date): Running Firebase verification script..."
python3 -c "
from firebase_admin import auth, credentials, initialize_app
import sys

try:
    initialize_app(credentials.Certificate('$FIREBASE_PRIVATE_KEY_PATH'))
except ValueError:
    pass  # App already initialized

try:
    admin_uid = '$OPTINIST_ADMIN_UID'
    auth.update_user(admin_uid, email_verified=True)
    print(f'Successfully verified email for user: {admin_uid}')
except Exception as e:
    print(f'ERROR: Firebase email verification failed: {e}', file=sys.stderr)
    sys.exit(1)
" || echo "$(date): WARNING: Firebase email verification script encountered an issue."

echo "$(date): Application setup completed successfully"
