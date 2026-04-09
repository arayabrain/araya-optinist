#!/bin/bash
# Stale ECS agent watchdog — see AGENT_RECOVERY_ARCHITECTURE.md.
#
# Greps /var/log/ecs/ecs-agent.log* for known stale-agent error strings
# within the last 5 minutes. On match, runs the documented manual
# recovery sequence: stop ecs -> docker rm -f ecs-agent -> rm agent.db
# -> start ecs. Rate-limited to 1 recovery / hour / instance via a sentinel in
# /var/run (tmpfs — auto-clears on boot).
#
# Loaded into ecs-user-data.sh via Terraform templatefile(); literal
# $${...} would need escaping if ever introduced.
set -u
set -o pipefail
LOG_GROUP="/ecs/agent-recovery"
INSTANCE_ID=$(curl -s -m 2 http://169.254.169.254/latest/meta-data/instance-id || echo unknown)
REGION=$(curl -s -m 2 http://169.254.169.254/latest/meta-data/placement/region || echo ap-northeast-1)
SENTINEL=/var/run/agent-recovery/last-recovery
# Probe-armed sentinel: written by health-probe.sh once it's seen
# AgentConnected=true at least once on this boot. Used here to debounce the
# "no log files" warning during agent warmup.
ARMED_FILE=/var/run/agent-recovery/probe-armed
RATE_LIMIT_SECONDS=3600

emit_log() {
  local msg="$1"
  local stream="$INSTANCE_ID"
  local ts=$(date -u +%s%3N)
  # Best-effort log emission; never block recovery on logging failure.
  aws logs create-log-group --log-group-name "$LOG_GROUP" --region "$REGION" 2>/dev/null || true
  aws logs create-log-stream --log-group-name "$LOG_GROUP" --log-stream-name "$stream" --region "$REGION" 2>/dev/null || true
  aws logs put-log-events \
    --log-group-name "$LOG_GROUP" \
    --log-stream-name "$stream" \
    --log-events "timestamp=$ts,message=$(printf '%s' "$msg" | tr '\n' ' ')" \
    --region "$REGION" >/dev/null 2>&1 || true
  logger -t agent-recovery "$msg"
}

# Heartbeat — emitted every run so the heartbeat-missing alarm stays quiet.
emit_log "watchdog tick instance=$INSTANCE_ID"

# Lifecycle guard: never act during ASG-driven transitions.
LIFECYCLE_STATE=$(/opt/agent-recovery/lifecycle-state.sh)
case "$LIFECYCLE_STATE" in
  Terminating*|Pending*)
    emit_log "skip lifecycle_state=$LIFECYCLE_STATE"
    exit 0
    ;;
esac

# Skip until ecs.service is up — avoids racing the host's own boot.
if ! systemctl is-active --quiet ecs.service; then
  emit_log "skip ecs.service=$(systemctl is-active ecs.service 2>/dev/null || echo unknown)"
  exit 0
fi

# Extract epoch seconds from an ECS agent log line. Handles both the legacy
# leading-ISO-8601 form and the `level=... time=... msg=...` form. Returns 0
# on parse failure.
parse_log_timestamp() {
  local line="$1"
  local ts=""
  if [ -z "$line" ]; then
    echo 0
    return
  fi
  ts=$(printf '%s' "$line" | grep -oE 'time=("?)[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]+Z\1' \
       | head -n1 | sed -E 's/^time=//; s/^"//; s/"$//')
  if [ -z "$ts" ]; then
    ts=$(printf '%s' "$line" | awk '{print $1}' | tr -d '"')
  fi
  if [ -z "$ts" ]; then
    echo 0
    return
  fi
  date -d "$ts" -u +%s 2>/dev/null || echo 0
}

# Format-drift self-test: if the first log line no longer parses, the AMI's
# log format has shifted and every subsequent match would be silently
# rejected. Exit non-zero so the heartbeat-missing alarm catches it.
SAMPLE_LINE=""
if [ -r /var/log/ecs/ecs-agent.log ]; then
  SAMPLE_LINE=$(head -n1 /var/log/ecs/ecs-agent.log 2>/dev/null || true)
fi
if [ -n "$SAMPLE_LINE" ]; then
  if [ "$(parse_log_timestamp "$SAMPLE_LINE")" -eq 0 ]; then
    emit_log "format drift detected — first line did not yield a timestamp: $SAMPLE_LINE"
    exit 2
  fi
fi

# Glob across rotated logs; rotation breaks any "tail -n N" approach.
shopt -s nullglob
LOG_FILES=(/var/log/ecs/ecs-agent.log*)
if [ ${#LOG_FILES[@]} -eq 0 ]; then
  # Tolerate the warmup window before the agent has written its first log line.
  if [ ! -f "$ARMED_FILE" ]; then
    exit 0
  fi
  emit_log "ecs.service active but /var/log/ecs/ecs-agent.log* missing — agent not logging"
  exit 2
fi

CUTOFF_EPOCH=$(date -d '5 minutes ago' -u +%s 2>/dev/null || { emit_log "date parse failed — exiting to fail safe"; exit 0; })
MATCH=""
# Post-filter each grep hit by its own timestamp so stale rotated lines
# don't trigger false positives.
while IFS= read -r line; do
  ts_epoch=$(parse_log_timestamp "$line")
  if [ "$ts_epoch" -ge "$CUTOFF_EPOCH" ]; then
    MATCH="$line"
    break
  fi
done < <(grep -hE "InvalidInstanceException|Missing container instance arn" "${LOG_FILES[@]}" 2>/dev/null || true)

if [ -z "$MATCH" ]; then
  exit 0
fi

# Rate-limit: max 1 recovery per hour per instance.
if [ -f "$SENTINEL" ]; then
  LAST=$(stat -c %Y "$SENTINEL" 2>/dev/null || echo 0)
  NOW=$(date -u +%s)
  if [ $((NOW - LAST)) -lt $RATE_LIMIT_SECONDS ]; then
    emit_log "rate-limited match=\"$MATCH\""
    exit 0
  fi
fi

emit_log "match=\"$MATCH\" — running recovery sequence"

# Documented manual recovery sequence — order matters.
systemctl stop ecs || true
docker rm -f ecs-agent 2>/dev/null || true
rm -f /var/lib/ecs/data/agent.db
# Clear the probe-armed sentinel: the restarted agent will go through its
# own warmup, and the probe should re-arm only after observing it healthy.
rm -f "$ARMED_FILE"
systemctl start ecs

touch "$SENTINEL"
emit_log "recovery complete instance=$INSTANCE_ID"
