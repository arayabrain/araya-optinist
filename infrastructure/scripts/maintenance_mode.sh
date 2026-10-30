#!/usr/bin/env bash
# Maintenance page toggle: a fixed 503 page for every path, at ALB rule
# priority 10 on the HTTPS listener. Kept out of Terraform so an apply during
# the window cannot remove it. See MAINTENANCE_PROCEDURES.md.
#
# Usage: ./maintenance_mode.sh <environment> on|off|status
#   environment: development or subscr

set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-1}"
PRIORITY="10"
PAGE="$(cd "$(dirname "$0")" && pwd)/maintenance_page.html"
USAGE="usage: $0 <environment> on|off|status"
ENVIRONMENT="${1:?$USAGE}"
ACTION="${2:?$USAGE}"

LB_ARN=$(aws elbv2 describe-load-balancers --region "$REGION" \
  --names "${ENVIRONMENT}-optinist-lb" \
  --query 'LoadBalancers[0].LoadBalancerArn' --output text)
# The :80 listener redirects; the one that forwards is the HTTPS listener.
LISTENER_ARN=$(aws elbv2 describe-listeners --region "$REGION" \
  --load-balancer-arn "$LB_ARN" \
  --query 'Listeners[?DefaultActions[0].Type==`forward`].ListenerArn' \
  --output text)
if [ -z "$LISTENER_ARN" ] || [ "$(echo "$LISTENER_ARN" | wc -w)" -ne 1 ]; then
  echo "Expected exactly one forwarding listener, got: '$LISTENER_ARN'" >&2
  exit 1
fi

rule_field() {
  aws elbv2 describe-rules --region "$REGION" --listener-arn "$LISTENER_ARN" \
    --query "Rules[?Priority=='$PRIORITY'] | [0].$1" --output text
}

RULE_ARN=$(rule_field RuleArn)
[ "$RULE_ARN" = "None" ] && RULE_ARN=""
if [ -n "$RULE_ARN" ]; then
  BODY=$(rule_field 'Actions[0].FixedResponseConfig.MessageBody')
  case "$BODY" in
    *"<!-- optinist-maintenance -->"*) ;;
    *)
      echo "Priority $PRIORITY holds a rule that is not the maintenance page: $RULE_ARN" >&2
      exit 1
      ;;
  esac
fi

case "$ACTION" in
  on)
    if [ -n "$RULE_ARN" ]; then
      echo "Maintenance page already on ($ENVIRONMENT)"
      exit 0
    fi
    ACTIONS=$(python3 - "$PAGE" <<'PY'
import json
import sys

body = open(sys.argv[1], encoding="utf-8").read().strip()
if len(body) > 1024:
    sys.exit(f"maintenance_page.html is {len(body)} chars; the ALB limit is 1024")
print(json.dumps([{
    "Type": "fixed-response",
    "FixedResponseConfig": {
        "StatusCode": "503",
        "ContentType": "text/html",
        "MessageBody": body,
    },
}]))
PY
)
    aws elbv2 create-rule --region "$REGION" --listener-arn "$LISTENER_ARN" \
      --priority "$PRIORITY" \
      --conditions 'Field=path-pattern,Values=/*' \
      --actions "$ACTIONS" \
      --tags Key=Name,Value=maintenance-page >/dev/null
    echo "Maintenance page ON ($ENVIRONMENT)"
    ;;
  off)
    if [ -z "$RULE_ARN" ]; then
      echo "Maintenance page already off ($ENVIRONMENT)"
      exit 0
    fi
    aws elbv2 delete-rule --region "$REGION" --rule-arn "$RULE_ARN"
    echo "Maintenance page OFF ($ENVIRONMENT)"
    ;;
  status)
    if [ -n "$RULE_ARN" ]; then
      echo "on"
    else
      echo "off"
    fi
    ;;
  *)
    echo "$USAGE" >&2
    exit 1
    ;;
esac
