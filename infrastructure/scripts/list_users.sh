#!/usr/bin/env bash
# Prints the emails of an environment's registered users, one per line, active only, test and Araya accounts excluded, other test-like addresses listed on stderr (read-only).
# Usage: infrastructure/scripts/list_users.sh [subscr|development]   (default: subscr, production)
set -euo pipefail

export ENV="${1:-subscr}" AWS_REGION="${AWS_REGION:-ap-northeast-1}"
export AWS_DEFAULT_REGION="$AWS_REGION" AWS_PAGER=""
source "$(dirname "$0")/rds_upgrade_helpers.sh"

PROXY=$(aws rds describe-db-proxies --db-proxy-name "${ENV}-optinist-rds-proxy" \
  --query 'DBProxies[0].Endpoint' --output text)

OUT=$(sql_run "$PROXY" <<'SQL'
SELECT DISTINCT email FROM users
 WHERE active = 1
   AND email NOT LIKE '%@test.com'
   AND email NOT LIKE '%@araya.org'
   AND email NOT LIKE '%.araya.org'
 ORDER BY email;
SELECT 'END_OF_LIST' AS marker;
SQL
)
# SSM returns only the first 24,000 characters of stdout; paginate the query if this trips.
grep -q END_OF_LIST <<<"$OUT" || { echo "ABORT: output truncated by SSM, the list is incomplete" >&2; exit 1; }
awk -F' *[|] *' '$2 ~ /@/ { if (tolower($2) ~ /test/) print "excluded as a test address: " $2 > "/dev/stderr"; else { print $2; n++ } }
  END { if (!n) { print "ABORT: no deliverable address in the output" > "/dev/stderr"; exit 1 } }' <<<"$OUT"
