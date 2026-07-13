#!/usr/bin/env bash
# CI-only: seed subscription plans and create the e2e users in the fresh
# stack (registration 500s without plan rows; premium needs plan + quota).
set -euo pipefail

COMPOSE="docker compose -f docker-compose.dev.multiuser.yml"

$COMPOSE exec -T -e SUBSCRIPTION_PLANS_CONFIG studio-dev-be \
  poetry run python infrastructure/scripts/seed_subscription_plans.py

verify_email() {
  $COMPOSE exec -T studio-dev-be poetry run python - <<PY
import firebase_admin
from firebase_admin import auth, credentials
cred = credentials.Certificate("studio/config/auth/firebase_private.json")
firebase_admin.initialize_app(cred)
auth.update_user(auth.get_user_by_email("$1").uid, email_verified=True)
PY
}

register() { # name email password
  # "already registered" is fine — the verify step self-heals partial users
  curl -s -X POST http://localhost:8000/api/register \
    -H 'Content-Type: application/json' \
    -d "{\"name\":\"$1\",\"role_id\":20,\"email\":\"$2\",\"password\":\"$3\"}" \
    > /dev/null || true
  verify_email "$2"
}

register "E2E Free" "$TEST_USER_EMAIL" "$TEST_USER_PASSWORD"
register "E2E Premium" "$TEST_PREMIUM_EMAIL" "$TEST_PREMIUM_PASSWORD"

# Premium upgrade needs BOTH the plan row and the storage quota
$COMPOSE exec -T db sh -c \
  'exec mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" -N "$MYSQL_DATABASE"' <<SQL
UPDATE subscription_users
   SET plan_id = 2, expiration = DATE_ADD(UTC_TIMESTAMP(), INTERVAL 1 MONTH)
 WHERE user_id = (SELECT id FROM users WHERE email = '$TEST_PREMIUM_EMAIL');
UPDATE user_storage_usage SET storage_quota_bytes = 214748364800
 WHERE user_id = (SELECT id FROM users WHERE email = '$TEST_PREMIUM_EMAIL');
SQL
