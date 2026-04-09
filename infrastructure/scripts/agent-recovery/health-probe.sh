#!/bin/bash
# On-instance ECS agent health probe — see AGENT_RECOVERY_ARCHITECTURE.md.
#
# Polls the ECS agent introspection endpoint at localhost:51678. If
# AgentConnected has been false for more than 5 minutes, marks the
# instance Unhealthy in the ASG so it gets terminated and replaced.
# This is what makes plain EC2 health checks meaningful — without it,
# they would only catch hardware/OS failure.
#
# Loaded into ecs-user-data.sh via Terraform templatefile().
set -u
set -o pipefail
# Provides AWS_REGION; written by ecs-user-data.sh from a Terraform variable.
# shellcheck disable=SC1091
[ -r /etc/agent-recovery/env ] && . /etc/agent-recovery/env

# IMDSv2 helper. Returns metadata value on stdout, empty string on failure.
imds_get() {
  local path="$1"
  local token
  token=$(curl -s -m 2 -X PUT \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 60" \
    http://169.254.169.254/latest/api/token 2>/dev/null || true)
  if [ -z "$token" ]; then
    return 0
  fi
  curl -s -m 2 -H "X-aws-ec2-metadata-token: $token" \
    "http://169.254.169.254/latest/meta-data/$path" 2>/dev/null || true
}

INSTANCE_ID=$(imds_get instance-id); INSTANCE_ID="${INSTANCE_ID:-unknown}"
REGION="${AWS_REGION:-$(imds_get placement/region)}"
STATE_FILE=/var/run/agent-recovery/agent-disconnect-since
ARMED_FILE=/var/run/agent-recovery/probe-armed
DISCONNECT_THRESHOLD_SECONDS=300

# Lifecycle guard: never act during ASG-driven transitions.
LIFECYCLE_STATE=$(/opt/agent-recovery/lifecycle-state.sh)
case "$LIFECYCLE_STATE" in
  Terminating*|Pending*)
    rm -f "$STATE_FILE"
    exit 0
    ;;
esac

# Skip until ecs.service is up
if ! systemctl is-active --quiet ecs.service; then
  rm -f "$STATE_FILE"
  exit 0
fi

# Read the ECS agent introspection endpoint (loopback-only, port 51678).
# An unreachable socket counts as a disconnect.
META=$(curl -s -m 2 http://localhost:51678/v1/metadata 2>/dev/null || echo "")
CONNECTED=""
if [ -n "$META" ]; then
  # /v1/metadata has no AgentConnected field; a non-empty ContainerInstanceArn
  # is the local proxy — only set after successful control-plane registration.
  if echo "$META" | grep -qE '"ContainerInstanceArn"[[:space:]]*:[[:space:]]*"[^"]+"'; then
    CONNECTED=1
  fi
fi

if [ -n "$CONNECTED" ]; then
  rm -f "$STATE_FILE"
  touch "$ARMED_FILE"
  exit 0
fi

# Disconnected — but if we've never observed a successful connect on this
# boot, we're still in agent warmup, not stranded.
if [ ! -f "$ARMED_FILE" ]; then
  rm -f "$STATE_FILE"
  exit 0
fi

NOW=$(date -u +%s)
if [ ! -f "$STATE_FILE" ]; then
  echo "$NOW" > "$STATE_FILE"
  exit 0
fi

SINCE=$(cat "$STATE_FILE" 2>/dev/null || echo "$NOW")
if [ $((NOW - SINCE)) -ge $DISCONNECT_THRESHOLD_SECONDS ]; then
  logger -t agent-recovery "agent disconnected for >=${DISCONNECT_THRESHOLD_SECONDS}s — marking instance Unhealthy"
  aws autoscaling set-instance-health \
    --instance-id "$INSTANCE_ID" \
    --health-status Unhealthy \
    --region "$REGION" || true
  rm -f "$STATE_FILE"
fi
