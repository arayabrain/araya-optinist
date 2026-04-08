#!/bin/bash
# On-instance ECS agent health probe — see AGENT_RECOVERY_ARCHITECTURE.md.
#
# Polls the ECS agent introspection endpoint at localhost:51678. If
# AgentConnected has been false for more than 5 minutes, marks the
# instance Unhealthy in the ASG so it gets terminated and replaced.
# This is what makes plain EC2 health checks meaningful — without it,
# they would only catch hardware/OS failure.
#
# Loaded into ecs-user-data.sh via Terraform templatefile(); literal
# $${...} would need escaping if ever introduced.
set -u
set -o pipefail
INSTANCE_ID=$(curl -s -m 2 http://169.254.169.254/latest/meta-data/instance-id || echo unknown)
REGION=$(curl -s -m 2 http://169.254.169.254/latest/meta-data/placement/region || echo ap-northeast-1)
STATE_FILE=/var/run/agent-recovery/agent-disconnect-since
DISCONNECT_THRESHOLD_SECONDS=300

# Lifecycle guard — same as watchdog.
LIFECYCLE_STATE=$(/opt/agent-recovery/lifecycle-state.sh)
case "$LIFECYCLE_STATE" in
  Terminating*|Pending*)
    rm -f "$STATE_FILE"
    exit 0
    ;;
esac

# Read the ECS agent introspection endpoint (default port 51678, bound to
# loopback only — see AWS ECS agent introspection docs). If the agent is
# not even answering on the local socket, treat that as a disconnect too.
META=$(curl -s -m 2 http://localhost:51678/v1/metadata 2>/dev/null || echo "")
CONNECTED=""
if [ -n "$META" ]; then
  # Naive but dependency-free JSON parse: AgentConnected is a bool literal.
  if echo "$META" | grep -q '"AgentConnected"[[:space:]]*:[[:space:]]*true'; then
    CONNECTED=1
  fi
fi

if [ -n "$CONNECTED" ]; then
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
