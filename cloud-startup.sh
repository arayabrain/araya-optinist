#!/bin/bash
set -e  # Exit immediately if a command exits with a non-zero status

# --- Subscription Plan Constants ---
# These values are used as fallbacks when SUBSCRIPTION_PLANS_CONFIG is not provided.
# Using constants improves readability and makes future updates easier.

# 'Free' plan details
readonly FREE_PLAN_ID=1
readonly FREE_PLAN_NAME="'Free'"
readonly FREE_PLAN_PRICE=0

# 'Premium' plan details
readonly PREMIUM_PLAN_ID=2
readonly PREMIUM_PLAN_NAME="'Premium'"
readonly PREMIUM_PLAN_PRICE=2999

# Common attributes for subscription plans
readonly DEFAULT_BILLING_CYCLE_DAYS=30
readonly CURRENCY_USD=840  # ISO 4217 numeric code for USD
readonly STATUS_ACTIVE=1   # Plan status: 1 = Active

# Map infrastructure environment variables (DB_*) to application expected variables (MYSQL_*)
# This allows the application's config.py to find the correct environment variables
# while maintaining infrastructure naming conventions
export MYSQL_SERVER="${DB_HOST}"
export MYSQL_USER="${DB_USER}"
export MYSQL_PASSWORD="${DB_PASSWORD}"
export MYSQL_DATABASE="${DB_NAME}"

echo 'Starting container'
echo 'Attempting to connect to RDS'
# Log environment variables for debugging
echo "DB_HOST: ${MYSQL_SERVER}"
echo "DB_USER: ${MYSQL_USER}"
echo "DB_NAME: ${MYSQL_DATABASE}"

# Wait for RDS to be available
# This is necessary because RDS might still be initializing when container starts
# Tries 30 times with 2 second intervals (total 60 seconds timeout)
max_tries=30
counter=0
until mysql --skip-ssl -h "${MYSQL_SERVER}" -u "${MYSQL_USER}" -p"${MYSQL_PASSWORD}" "${MYSQL_DATABASE}" -e 'SELECT 1;'
do
    sleep 2
    [[ counter -eq $max_tries ]] && echo "Failed to connect to Database" && exit 1
    echo "Attempt $counter: Waiting for Database..."
    ((counter++))
done

echo 'Database connection successful'

# Ensure user has mysql_native_password authentication for RDS Proxy compatibility
# This is needed because RDS Proxy with MySQL 8.0 requires mysql_native_password
# when using MYSQL_NATIVE_PASSWORD client authentication type
echo 'Ensuring user authentication plugin is mysql_native_password...'
mysql --skip-ssl -h "$MYSQL_SERVER" -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" <<-EOSQL
    ALTER USER '${MYSQL_USER}'@'%' IDENTIFIED WITH mysql_native_password BY '${MYSQL_PASSWORD}';
    FLUSH PRIVILEGES;
EOSQL

# Create database if it doesn't exist
# This ensures the application's database exists before proceeding
mysql --skip-ssl -h "$MYSQL_SERVER" -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" <<-EOSQL
    CREATE DATABASE IF NOT EXISTS ${MYSQL_DATABASE};
    USE ${MYSQL_DATABASE};
EOSQL

if ! mysql --skip-ssl -h "$MYSQL_SERVER" -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" -e "USE ${MYSQL_DATABASE}"; then
    echo "Failed to create/access database ${MYSQL_DATABASE}"
    exit 1
fi

echo "Database ${MYSQL_DATABASE} ready"

# Create frontend configuration dynamically
echo "Creating frontend .env.production configuration..."
cat > /app/frontend/.env.production << ENV_EOF
REACT_APP_SERVER_HOST=${FRONTEND_SERVER_HOST:-localhost}
REACT_APP_SERVER_PORT=${FRONTEND_SERVER_PORT:-8000}
REACT_APP_SERVER_PROTO=${FRONTEND_SERVER_PROTO:-http}
REACT_APP_EXPDB_METADATA_EDITABLE=${FRONTEND_EXPDB_METADATA_EDITABLE:-true}
ENV_EOF

echo "Frontend configuration created:"
cat /app/frontend/.env.production

# Run database migrations using alembic
# This ensures all database tables and schemas are up to date
cd /app

# Run Alembic upgrade - if migrations fail, the container will exit
# This causes ECS to mark the deployment as failed and revert to the previous version
echo "Running Alembic upgrade..."
if ! alembic upgrade head 2>&1; then
    echo "ERROR: Database migration failed!"
    echo "Container will exit to prevent data loss and trigger deployment rollback."
    echo "Please investigate the migration error before redeploying."
    exit 1
fi
echo "Database migrations completed successfully"

# Initialize subscription plans from environment variable
echo "Initializing subscription plans from SUBSCRIPTION_PLANS_CONFIG..."
if [ ! -z "$SUBSCRIPTION_PLANS_CONFIG" ]; then
    # Generate SQL from JSON using Python, then execute with mysql CLI
    SQL_STATEMENTS=$(echo "$SUBSCRIPTION_PLANS_CONFIG" | python3 -c "
import sys
import json

try:
    plans = json.load(sys.stdin)

    # Generate SQL statements for each plan
    for plan in plans:
        # Escape single quotes for MySQL (matching deployment.tf pattern)
        name = plan['name'].replace(\"'\", r\"\\'\")
        features_json = json.dumps(plan['features']).replace(\"'\", r\"\\'\")
        stripe_product_id = plan.get('stripe_product_id', '').replace(\"'\", r\"\\'\")
        stripe_price_id = plan.get('stripe_price_id', '').replace(\"'\", r\"\\'\")

        # Generate INSERT ... ON DUPLICATE KEY UPDATE statement
        print(f'''
INSERT INTO subscription_plans
    (id, name, price, billing_cycle, features, currency, status, stripe_product_id, stripe_price_id, created_at)
VALUES
    ({plan['id']}, '{name}', {plan['price']}, {plan['billing_cycle']}, '{features_json}', {plan['currency']}, {plan['status']}, '{stripe_product_id}', '{stripe_price_id}', NOW())
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    price = VALUES(price),
    billing_cycle = VALUES(billing_cycle),
    features = VALUES(features),
    currency = VALUES(currency),
    status = VALUES(status),
    stripe_product_id = VALUES(stripe_product_id),
    stripe_price_id = VALUES(stripe_price_id);
''')

except json.JSONDecodeError as e:
    print(f'-- Error parsing JSON: {e}', file=sys.stderr)
    sys.exit(1)
except KeyError as e:
    print(f'-- Missing required field in subscription plan: {e}', file=sys.stderr)
    sys.exit(1)
" 2>&1)

    if [ $? -eq 0 ]; then
        echo "Generated SQL for subscription plans, executing..."
        echo "$SQL_STATEMENTS" | mysql --skip-ssl -h "$MYSQL_SERVER" -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" ${MYSQL_DATABASE}

        if [ $? -eq 0 ]; then
            echo "Subscription plans initialized successfully from SUBSCRIPTION_PLANS_CONFIG"
        else
            echo "ERROR: Failed to execute subscription plan SQL"
            exit 1
        fi
    else
        echo "ERROR: Failed to generate SQL from SUBSCRIPTION_PLANS_CONFIG"
        echo "$SQL_STATEMENTS"
        exit 1
    fi
else
    echo "SUBSCRIPTION_PLANS_CONFIG not provided, using fallback values..."
    mysql --skip-ssl -h "$MYSQL_SERVER" -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" ${MYSQL_DATABASE} <<-EOSQL
        INSERT IGNORE INTO subscription_plans (id, name, price, billing_cycle, currency, status)
        VALUES
            (${FREE_PLAN_ID}, ${FREE_PLAN_NAME}, ${FREE_PLAN_PRICE}, ${DEFAULT_BILLING_CYCLE_DAYS}, ${CURRENCY_USD}, ${STATUS_ACTIVE}),
            (${PREMIUM_PLAN_ID}, ${PREMIUM_PLAN_NAME}, ${PREMIUM_PLAN_PRICE}, ${DEFAULT_BILLING_CYCLE_DAYS}, ${CURRENCY_USD}, ${STATUS_ACTIVE});
EOSQL
fi

# Initialize tax rates
echo "Initializing tax rates..."
mysql --skip-ssl -h "$MYSQL_SERVER" -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" ${MYSQL_DATABASE} <<-EOSQL
    INSERT IGNORE INTO taxes (tax_type, tax_name, tax_rate, is_active, effective_date)
    VALUES ('sales_tax', 'Sales Tax', 0.10, 1, CURDATE());
EOSQL

# Initialize roles
echo "Initializing roles..."
mysql --skip-ssl -h "$MYSQL_SERVER" -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" ${MYSQL_DATABASE} <<-EOSQL
    INSERT IGNORE INTO roles (id, role) VALUES (1, 'Admin'), (20, 'Operator');
EOSQL

# Initialize admin user and organization
# Only proceeds if all required environment variables are set
# This section handles first-time setup of the application
if [ ! -z "$INITIAL_FIREBASE_UID" ] && [ ! -z "$INITIAL_USER_NAME" ] && [ ! -z "$INITIAL_USER_EMAIL" ]; then
    echo "Checking for existing admin user..."
    # Check if user already exists to prevent duplicate creation
    USER_EXISTS=$(mysql --skip-ssl -h "$MYSQL_SERVER" -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" -N -s -e \
        "SELECT COUNT(*) FROM ${MYSQL_DATABASE}.users WHERE uid='$INITIAL_FIREBASE_UID';")

    if [ "$USER_EXISTS" -eq "0" ]; then
        # Create default organization first (required due to foreign key constraint)
        echo "Creating default organization..."
        mysql --skip-ssl -h "$MYSQL_SERVER" -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" ${MYSQL_DATABASE} <<-EOSQL
            INSERT IGNORE INTO organization (id, name) VALUES (1, 'Default Organization');
EOSQL

        # Create the initial admin user
        echo "Creating initial admin user..."
        mysql --skip-ssl -h "$MYSQL_SERVER" -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" ${MYSQL_DATABASE} <<-EOSQL
            INSERT INTO users (uid, organization_id, name, email, active)
            VALUES ('$INITIAL_FIREBASE_UID', 1, '$INITIAL_USER_NAME', '$INITIAL_USER_EMAIL', true);
EOSQL
        echo "Initial admin user created successfully"

        # Verify admin email in Firebase
        echo "Verifying admin email in Firebase..."
        python3 -c "
from firebase_admin import auth, credentials, initialize_app
try:
    initialize_app(credentials.Certificate('/app/studio/config/auth/firebase_private.json'))
except ValueError:
    pass  # Already initialized
auth.update_user('$INITIAL_FIREBASE_UID', email_verified=True)
print('Admin email verified successfully')
" || echo "Warning: Could not verify admin email in Firebase"

        # Assign admin role to the initial user
        echo "Assigning admin role to initial user..."
        mysql --skip-ssl -h "$MYSQL_SERVER" -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" ${MYSQL_DATABASE} <<-EOSQL
            INSERT IGNORE INTO user_roles (user_id, role_id)
            SELECT id, 1 FROM users WHERE uid='$INITIAL_FIREBASE_UID';
EOSQL
        echo "Admin role assigned successfully"

        # Initialize storage usage for admin user
        if [ ! -z "$ADMIN_STORAGE_QUOTA_BYTES" ]; then
            echo "Initializing admin user storage quota..."
            mysql --skip-ssl -h "$MYSQL_SERVER" -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" ${MYSQL_DATABASE} <<-EOSQL
                INSERT IGNORE INTO user_storage_usage (user_id, storage_usage_bytes, storage_quota_bytes)
                VALUES (1, 0, $ADMIN_STORAGE_QUOTA_BYTES);
EOSQL
            echo "Admin user storage quota initialized: $ADMIN_STORAGE_QUOTA_BYTES bytes"
        else
            echo "ADMIN_STORAGE_QUOTA_BYTES not provided, skipping storage quota initialization"
        fi

        # Set admin user to premium plan
        echo "Setting admin user to premium plan..."
        mysql --skip-ssl -h "$MYSQL_SERVER" -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" ${MYSQL_DATABASE} <<-EOSQL
            INSERT IGNORE INTO subscription_users (plan_id, user_id, expiration)
            VALUES (2, 1, DATE_ADD(NOW(), INTERVAL 365 DAY));
EOSQL
        echo "Admin user set to premium plan"
    else
        echo "Admin user already exists, skipping creation"
    fi

    # Ensure admin role is assigned (for both new and existing admin users)
    echo "Ensuring admin role is assigned to admin user..."
    mysql --skip-ssl -h "$MYSQL_SERVER" -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" ${MYSQL_DATABASE} <<-EOSQL
        INSERT IGNORE INTO user_roles (user_id, role_id)
        SELECT id, 1 FROM users WHERE uid='$INITIAL_FIREBASE_UID';
EOSQL
    echo "Admin role assignment verified"
fi

# Verify backend configuration
# Ensures required environment variables are set before starting the application
echo "Host: $BACKEND_HOST"
echo "Port: $BACKEND_PORT"
if [ -z "$BACKEND_HOST" ] || [ -z "$BACKEND_PORT" ]; then
    echo "Please provide 'BACKEND_HOST' and 'BACKEND_PORT' environment variables"
    exit 1
fi

# Configure Uvicorn worker processes
# Workers handle concurrent requests. Recommended formula: (2 × CPU cores) + 1
# t3.large has 2 vCPUs, so optimal range is 2-5 workers
# Set via environment variable UVICORN_WORKERS, defaults to 5 for production use
UVICORN_WORKERS=${UVICORN_WORKERS:-5}
echo "Uvicorn workers: $UVICORN_WORKERS"

# Start the application in background
echo "Starting application..."
poetry run python main.py --host="$BACKEND_HOST" --port="$BACKEND_PORT" --workers="$UVICORN_WORKERS" &
APP_PID=$!

# Allow initial startup time matching ECS health check startPeriod
echo "Waiting for initial startup..."
sleep 30

# Single initial health check before load balancer check
echo "Verifying initial health..."
if ! curl -v "http://${BACKEND_HOST}:${BACKEND_PORT}/health"; then
    echo "Initial health check failed"
    # Don't exit - let ECS handle it
fi

# Load balancer health check function
# Verifies that the application is accessible through the load balancer
check_load_balancer() {
    if [ -n "$AWS_SERVICE_URL" ]; then
        echo "Checking load balancer status..."
        readonly MAX_TRIES=30
        readonly WAIT_SECONDS=10
        local counter=0

        # Try for 5 minutes (30 attempts * 10 seconds)
        until curl -s -o /dev/null --max-time ${WAIT_SECONDS} "$AWS_SERVICE_URL"
        do
            sleep ${WAIT_SECONDS}
            [[ $counter -eq $MAX_TRIES ]] && echo "Load balancer not ready after 5 minutes" && return 1
            echo "Attempt $counter: Waiting for load balancer..."
            ((counter++))
        done
        echo "Load balancer is ready"
        return 0
    else
        echo "AWS_SERVICE_URL not provided, skipping load balancer check"
        return 0
    fi
}

# Run load balancer check in background
# This allows parallel checking while the application is starting
check_load_balancer &
LB_CHECK_PID=$!

# Wait for all background processes to complete
# This ensures the container keeps running as long as the application is running
wait $APP_PID
wait $LB_CHECK_PID
