#!/bin/bash
# Reclaims the docker data that fills an ECS host's root volume: stopped
# containers from earlier task definitions and the images they pinned.
# Installed by the docker-hygiene SSM document and run on its schedule and
# after each deploy. Idempotent; safe to run by hand.
#
# Not `set -e`: a failed prune must still reach the final df report. No
# double curly braces anywhere: SSM reads them as a document parameter.
set -uo pipefail

LOG="${DOCKER_HYGIENE_LOG:-/var/log/docker_hygiene.log}"
ECS_AGENT_TASKS_URL=http://localhost:51678/v1/tasks
CONTAINER_MIN_AGE=10m
ROOT_USED_WARN_PERCENT=80

tasks_steady() {
    # Steady = the ECS agent answers, at least one task is RUNNING and none is
    # mid-start. An image prune during a start can delete the image a task is
    # about to use; the agent answers nothing on a host that is still booting.
    local tasks running pending
    tasks=$(curl -sf --max-time 5 "$ECS_AGENT_TASKS_URL") || {
        echo "ecs agent introspection unavailable"
        return 1
    }
    # grep -o | wc -l rather than grep -c: zero matches must count as 0, not fail
    running=$(printf '%s' "$tasks" | grep -o '"KnownStatus":"RUNNING"' | wc -l | tr -d ' ')
    pending=$(printf '%s' "$tasks" | grep -o '"KnownStatus":"[A-Z_]*"' | grep -v -e RUNNING -e STOPPED | wc -l | tr -d ' ')
    echo "tasks: running=$running pending=$pending"
    [ "$running" -ge 1 ] && [ "$pending" -eq 0 ]
}

root_used_percent() {
    df -P / | awk 'NR==2 { sub("%", "", $5); print $5 }'
}

main() {
    echo "=== docker hygiene $(date -u +%Y-%m-%dT%H:%M:%SZ) on $(hostname) ==="
    if ! docker info >/dev/null 2>&1; then
        echo "docker not running; nothing to do"
        return 0
    fi

    echo "--- before"
    df -h /
    docker system df

    echo "--- container prune (stopped, created more than $CONTAINER_MIN_AGE ago)"
    docker container prune -f --filter "until=$CONTAINER_MIN_AGE"

    if tasks_steady; then
        # Re-check right before the prune: a task start between the two
        # reads is the only window in which an image can be pulled and pruned
        if tasks_steady; then
            echo "--- image prune (all images not used by a container)"
            docker image prune -af
        else
            echo "image prune skipped: task state changed"
        fi
    else
        echo "image prune skipped: no steady running task"
    fi

    echo "--- after"
    df -h /
    docker system df

    local used
    used=$(root_used_percent)
    if [ "${used:-0}" -ge "$ROOT_USED_WARN_PERCENT" ]; then
        echo "WARNING: root volume ${used}% used after hygiene (threshold ${ROOT_USED_WARN_PERCENT}%)"
        return 2
    fi
    echo "root volume ${used}% used"
    return 0
}

{ main; } 2>&1 | tee -a "$LOG"
STATUS="${PIPESTATUS[0]}"

# Keep the log bounded; a failed rewrite on a full disk leaves the old log
if tail -n 2000 "$LOG" > "$LOG.tmp" 2>/dev/null; then
    mv "$LOG.tmp" "$LOG"
else
    rm -f "$LOG.tmp"
fi

exit "$STATUS"
