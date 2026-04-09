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
INSTANCE_ID=$(curl -s -m 2 http://169.254.169.254/latest/meta-data/instance-id || echo unknown)
REGION=$(curl -s -m 2 http://169.254.169.254/latest/meta-data/placement/region || echo ap-northeast-1)
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
