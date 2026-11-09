#!/usr/bin/env bash
# Prints the emails of an environment's registered users, one per line, active only, test and Araya accounts excluded (read-only).
# Usage: infrastructure/scripts/list_users.sh [subscr|development]   (default: subscr, production)
set -euo pipefail

export ENV="${1:-subscr}" AWS_REGION="${AWS_REGION:-ap-northeast-1}"
export AWS_DEFAULT_REGION="$AWS_REGION" AWS_PAGER=""
source "$(dirname "$0")/rds_upgrade_helpers.sh"

PROXY=$(aws rds describe-db-proxies --db-proxy-name "${ENV}-optinist-rds-proxy" \
  --query 'DBProxies[0].Endpoint' --output text)

sql_run "$PROXY" <<'SQL' | sed -n 's/^| \([^ ]*@[^ ]*\) *|$/\1/p'
SELECT DISTINCT email FROM users
 WHERE active = 1
   AND email NOT LIKE '%test%'
   AND email NOT LIKE '%@araya.org'
   AND email NOT LIKE '%.araya.org'
 ORDER BY email;
SQL
