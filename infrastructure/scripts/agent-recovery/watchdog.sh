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

# Glob across rotated logs and filter to lines from the last 5 minutes.
# Rotation breaks any "tail -n 1000" approach, so we always glob.
shopt -s nullglob
LOG_FILES=(/var/log/ecs/ecs-agent.log*)
if [ ${#LOG_FILES[@]} -eq 0 ]; then
  exit 0
fi

CUTOFF_EPOCH=$(date -d '5 minutes ago' -u +%s 2>/dev/null || { emit_log "date parse failed — exiting to fail safe"; exit 0; })
MATCH=""
# Use grep -h for combined output; the agent log lines start with an ISO-8601
# timestamp. We post-filter by parsing the leading timestamp ourselves so a
# stale rotated file doesn't trigger a false positive.
while IFS= read -r line; do
  # NOTE: assumes the leading whitespace-separated token is an ISO-8601
  # timestamp. This format is AMI-version-specific (see ecs_optimized_ami_name
  # in compute.tf) — re-run the watchdog smoke test on every AMI bump.
  ts_field=$(printf '%s' "$line" | awk '{print $1}' | tr -d '"')
  ts_epoch=$(date -d "$ts_field" -u +%s 2>/dev/null || echo 0)
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
systemctl start ecs

touch "$SENTINEL"
emit_log "recovery complete instance=$INSTANCE_ID"
