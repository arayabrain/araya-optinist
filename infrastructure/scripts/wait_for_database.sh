#!/bin/bash
# ============================================================================
# wait_for_database.sh
# ============================================================================
# Waits until the database accepts a connection, within a bound. Run by
# cloud-startup.sh (as /app/scripts/wait_for_database.sh) before the
# migrations, since RDS may still be starting when the container does.
#
# Reads MYSQL_SERVER, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE and
# MYSQL_SSL_MODE. Exits 0 once a connection succeeds, 1 when the bound runs
# out.
#
# Bounds in seconds, overridable for tests only:
#
#   DB_WAIT_SECONDS             whole wait (600); the ECS services' health
#                               check grace must cover it
#   DB_ATTEMPT_TIMEOUT_SECONDS  one attempt (10); an RDS Proxy with no
#                               target holds a connection for about 2 minutes
#   DB_RETRY_INTERVAL_SECONDS   pause between attempts (10)
# ============================================================================
set -e

wait_seconds=${DB_WAIT_SECONDS:-600}
attempt_timeout=${DB_ATTEMPT_TIMEOUT_SECONDS:-10}
retry_interval=${DB_RETRY_INTERVAL_SECONDS:-10}

# Build SSL options based on MYSQL_SSL_MODE
ssl_opts=()
if [ -n "${MYSQL_SSL_MODE}" ]; then
    case "${MYSQL_SSL_MODE}" in
        DISABLED)
            ssl_opts=(--skip-ssl)
            ;;
        *)
            ssl_opts=(--ssl)
            ;;
    esac
fi

deadline=$((SECONDS + wait_seconds))
attempt=0

# `$?` at the top of the body is the attempt's status; 124 is a timeout.
until timeout "$attempt_timeout" \
    mysql "${ssl_opts[@]}" -h "${MYSQL_SERVER}" -u "${MYSQL_USER}" -p"${MYSQL_PASSWORD}" "${MYSQL_DATABASE}" -e 'SELECT 1;'
do
    status=$?
    # Not `((attempt++))`: an arithmetic command that evaluates to 0 returns
    # 1, and `set -e` exits on it.
    attempt=$((attempt + 1))
    # Stop now if the next attempt could not finish before the deadline.
    if ((SECONDS + retry_interval + attempt_timeout > deadline)); then
        echo "Failed to connect to Database after $attempt attempts"
        exit 1
    fi
    if ((status == 124)); then
        echo "Attempt $attempt: no answer within ${attempt_timeout} s, waiting for Database..."
    else
        echo "Attempt $attempt: Waiting for Database..."
    fi
    sleep "$retry_interval"
done

echo 'Database connection successful'
