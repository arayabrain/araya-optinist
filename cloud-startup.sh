#!/bin/bash
set -e  # Exit immediately if a command exits with a non-zero status

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
