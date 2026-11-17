#!/bin/bash
# Rolls every service in an ECS cluster onto the image its task definition
# names (force-new-deployment), waits until all are stable, then triggers the
# docker hygiene run. Shared by null_resource.deploy_to_ecs (terraform apply)
# and `ecr_build_push.sh --deploy`.
#
# Usage: ecs_rollout.sh --cluster <name> --region <region>
#                       [--asg <name>]... [--hygiene-association <id>]
#   --asg                  : an Auto Scaling group whose ELB health check would
#                            replace a host while its task restarts (repeatable)
#   --hygiene-association  : SSM association run once the services are stable
#
# Free and public run one task per host, so a rollout stops each old task,
# waits out its target group's deregistration delay, then starts the new one.
# Three things keep that from taking much longer:
# - a rollout already in progress (terraform apply rolls a service whose task
#   definition changed before the image is built) is left to finish first;
#   forcing on top of it restarts it
# - the groups' HealthCheck process is suspended until the restarted targets
#   pass their checks (or the script fails or is interrupted): a restarted
#   task is past its host's grace period, and a target failing a few checks
#   would get the whole host replaced mid-rollout
# - one `aws ecs wait` stops at 10 minutes, shorter than a public rollout, so
#   the wait is repeated up to about an hour
# Only this script suspends HealthCheck, so a group found suspended is held by
# another rollout, running or killed: this one waits for it to be lifted as it
# waits for a rollout in progress, and past that bound takes it over.
# It exits 1 when a service does not settle within that hour, when a target
# group still has no healthy target at the end of the target wait, or when a
# group's HealthCheck could not be resumed.
#
# Runs under macOS bash 3.2: no arrays, no associative arrays.
set -uo pipefail

PRE_WAIT_SECONDS=1800
PRE_WAIT_POLL_SECONDS=30
STABLE_DEADLINE_SECONDS=3700
# One `aws ecs wait services-stable` (40 polls x 15 s) plus the pause after it
STABLE_WAIT_SECONDS=615
# A wait that fails sooner did not time out: the call itself failed
WAIT_FAILED_AT_ONCE_SECONDS=60
# The services' health check grace period
TARGET_HEALTH_WAIT_SECONDS=900
TARGET_HEALTH_POLL_SECONDS=15

# Rolling: more than one deployment, or one that has not reached steady state.
# A starting rollout has only its PRIMARY left once the old tasks stop
ROLLING_QUERY='services[?length(deployments) > `1` || length(deployments[?rolloutState==`IN_PROGRESS`]) > `0`].serviceName'

usage() {
    echo "Usage: ecs_rollout.sh --cluster <name> --region <region> [--asg <name>]... [--hygiene-association <id>]"
}

CLUSTER=""
REGION=""
ASGS=""
HYGIENE_ASSOCIATION=""

while [ $# -gt 0 ]; do
    case "$1" in
        --cluster | --region | --asg | --hygiene-association)
            if [ $# -lt 2 ] || [ -z "$2" ]; then
                echo "ERROR: $1 needs a value"
                usage
                exit 1
            fi
            case "$1" in
                --cluster) CLUSTER="$2" ;;
                --region) REGION="$2" ;;
                --asg) ASGS="${ASGS} $2" ;;
                --hygiene-association) HYGIENE_ASSOCIATION="$2" ;;
            esac
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

if [ -z "$CLUSTER" ] || [ -z "$REGION" ]; then
    echo "ERROR: --cluster and --region are required"
    exit 1
fi

# ===========================================
# Host health checks: off for the rollout, back on at any exit
# ===========================================
# HealthCheck, not ReplaceUnhealthy: with only replacement suspended the group
# still marks a host Unhealthy, the mark never clears, and the host is
# replaced the moment replacement resumes, recovered or not
SUSPENDED_ASGS=""   # held by this run: resumed at exit
WAITING_ASGS=""     # held by another rollout: waited for, then taken over
RESUME_FAILED_ASGS=""

# Prints 1 if the group's HealthCheck is suspended, 0 if not; fails if unreadable
health_check_suspended() {
    aws autoscaling describe-auto-scaling-groups \
        --auto-scaling-group-names "$1" --region "$REGION" \
        --query 'length(AutoScalingGroups[0].SuspendedProcesses[?ProcessName==`HealthCheck`])' \
        --output text
}

suspend_group() {
    if aws autoscaling suspend-processes --auto-scaling-group-name "$1" \
        --scaling-processes HealthCheck --region "$REGION"; then
        SUSPENDED_ASGS="${SUSPENDED_ASGS} $1"
        echo "  $1: HealthCheck suspended for the rollout"
    else
        echo "WARNING: could not suspend HealthCheck on $1; a host may be replaced mid-rollout"
    fi
}

suspend_health_checks() {
    local asg state
    for asg in $ASGS; do
        if ! state=$(health_check_suspended "$asg"); then
            echo "WARNING: could not read ${asg}; its health checks stay on during the rollout"
            continue
        fi
        if [ "$state" != "0" ]; then
            echo "  ${asg}: HealthCheck already suspended, by another rollout still running or by one that was killed; waiting for it to be lifted"
            WAITING_ASGS="${WAITING_ASGS} ${asg}"
            continue
        fi
        suspend_group "$asg"
    done
}

# Suspends, for this run, each waited-for group whose HealthCheck is back on.
# A read that fails keeps the group waiting
take_lifted_groups() {
    local asg state still=""
    for asg in $WAITING_ASGS; do
        if state=$(health_check_suspended "$asg") && [ "$state" = "0" ]; then
            echo "  ${asg}: HealthCheck lifted by the other rollout"
            suspend_group "$asg"
        else
            still="${still} ${asg}"
        fi
    done
    WAITING_ASGS="$still"
}

take_over_waiting_groups() {
    local asg
    for asg in $WAITING_ASGS; do
        echo "WARNING: ${asg}: HealthCheck still suspended after $(( PRE_WAIT_SECONDS / 60 )) minutes; taking it over, so this rollout resumes it"
        suspend_group "$asg"
    done
    WAITING_ASGS=""
}

resume_health_checks() {
    local asg
    for asg in $SUSPENDED_ASGS; do
        if aws autoscaling resume-processes --auto-scaling-group-name "$asg" \
            --scaling-processes HealthCheck --region "$REGION"; then
            echo "  ${asg}: HealthCheck resumed"
        else
            RESUME_FAILED_ASGS="${RESUME_FAILED_ASGS} ${asg}"
            echo "ERROR: HealthCheck is still suspended on ${asg}. Resume it with:"
            echo "  aws autoscaling resume-processes --auto-scaling-group-name ${asg} --scaling-processes HealthCheck --region ${REGION}"
        fi
    done
    SUSPENDED_ASGS=""
}

# INT / TERM exit through the EXIT trap, so an interrupted rollout resumes too.
# The INT trap also matters because the AWS CLI exits normally on Ctrl-C, and
# bash alone would then carry on with the next command
trap resume_health_checks EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

SERVICES=$(aws ecs list-services --cluster "$CLUSTER" --region "$REGION" \
    --query 'serviceArns[]' --output text)
if [ -z "$SERVICES" ]; then
    echo "ERROR: no ECS services found in ${CLUSTER}"
    exit 1
fi

if [ -n "$ASGS" ]; then
    echo "Suspending host health checks for the rollout..."
    suspend_health_checks
fi

# ===========================================
# Let a rollout already in progress finish
# ===========================================
# Bounded: a rollout that never settles (an image whose tasks exit) is the
# one a new image has to replace, so the force goes ahead after the bound
PRE_WAIT_DEADLINE=$(( $(date +%s) + PRE_WAIT_SECONDS ))
ROLLING=""
while :; do
    if [ -n "$WAITING_ASGS" ]; then
        take_lifted_groups
    fi
    if ROLLING=$(aws ecs describe-services --cluster "$CLUSTER" --services $SERVICES \
        --region "$REGION" --query "$ROLLING_QUERY" --output text); then
        if [ -z "$ROLLING" ] && [ -z "$WAITING_ASGS" ]; then
            break
        fi
        if [ -n "$ROLLING" ]; then
            echo "A rollout is in progress on: ${ROLLING}; waiting for it to finish before forcing a new one"
        fi
        if [ -n "$WAITING_ASGS" ]; then
            echo "HealthCheck is still suspended on${WAITING_ASGS}; waiting for the rollout that holds it"
        fi
    else
        ROLLING="unknown"
        echo "Could not read the services' deployments; retrying"
    fi
    if [ $(( $(date +%s) + PRE_WAIT_POLL_SECONDS )) -gt "$PRE_WAIT_DEADLINE" ]; then
        if [ -n "$ROLLING" ]; then
            echo "WARNING: still rolling after $(( PRE_WAIT_SECONDS / 60 )) minutes; forcing the new deployment anyway"
        fi
        take_over_waiting_groups
        break
    fi
    sleep "$PRE_WAIT_POLL_SECONDS"
done

# ===========================================
# Force the new deployment on every service
# ===========================================
echo "Forcing a new deployment on all services in ${CLUSTER}..."
for svc in $SERVICES; do
    echo "  -> ${svc##*/}"
    if ! aws ecs update-service --cluster "$CLUSTER" --service "$svc" \
        --force-new-deployment --region "$REGION" >/dev/null; then
        echo "ERROR: force-new-deployment failed on ${svc##*/}"
        exit 1
    fi
done

# ===========================================
# Wait until every service is stable
# ===========================================
echo "Waiting for all services to stabilize (up to about an hour)..."
STABLE_DEADLINE=$(( $(date +%s) + STABLE_DEADLINE_SECONDS ))
while :; do
    WAIT_STARTED=$(date +%s)
    if aws ecs wait services-stable --cluster "$CLUSTER" --services $SERVICES \
        --region "$REGION"; then
        break
    fi
    # A wait that times out takes its full 10 minutes; one that fails at once
    # (access denied, expired credentials, a missing service) would only fail
    # again
    if [ $(( $(date +%s) - WAIT_STARTED )) -lt "$WAIT_FAILED_AT_ONCE_SECONDS" ]; then
        echo "ERROR: the wait for the services failed at once (see the error above); not retrying."
        exit 1
    fi
    # Start another wait only if it and the sleep can finish by the deadline
    if [ $(( $(date +%s) + STABLE_WAIT_SECONDS )) -gt "$STABLE_DEADLINE" ]; then
        echo "ERROR: services not stable within about an hour; the new image may not be serving."
        aws ecs describe-services --cluster "$CLUSTER" --services $SERVICES \
            --region "$REGION" \
            --query 'services[?!(length(deployments) == `1` && runningCount == desiredCount)].[serviceName, events[0].message]' \
            --output text || true
        echo "Check those services in the ECS console."
        exit 1
    fi
    echo "Still rolling out, waiting again..."
    sleep 15
done
echo "All services are stable."

# ===========================================
# Wait for the new targets, then host health checks back on
# ===========================================
# services-stable counts running tasks, not target health. Free restarts every
# task at once and public replaces half its hosts at a time, so the last
# tasks are still starting when the services count as stable. Bounded by the
# services' 900 s grace: a target still failing then (or a host the group
# registered that has no task) is one the ELB check is there to replace. A
# group with no healthy target at all is a tier not serving the new image,
# which fails the rollout as a service that never settles does
NOT_SERVING_GROUPS=""
if TARGET_GROUPS=$(aws ecs describe-services --cluster "$CLUSTER" --services $SERVICES \
    --region "$REGION" --query 'services[].loadBalancers[].targetGroupArn' \
    --output text); then
    if [ -n "$TARGET_GROUPS" ]; then
        echo "Waiting for the restarted targets to pass their health checks..."
        TARGET_DEADLINE=$(( $(date +%s) + TARGET_HEALTH_WAIT_SECONDS ))
        while :; do
            PENDING_GROUPS=""
            PENDING_ARNS=""
            for tg in $TARGET_GROUPS; do
                # initial: still being checked; unhealthy: failing. Draining
                # and unused targets are leaving or not serving
                if ! NOT_READY=$(aws elbv2 describe-target-health --target-group-arn "$tg" \
                    --region "$REGION" \
                    --query 'TargetHealthDescriptions[?TargetHealth.State==`initial` || TargetHealth.State==`unhealthy`].Target.Id' \
                    --output text) || [ -n "$NOT_READY" ]; then
                    tg_name="${tg#*targetgroup/}"
                    PENDING_GROUPS="${PENDING_GROUPS} ${tg_name%%/*}"
                    PENDING_ARNS="${PENDING_ARNS} ${tg}"
                fi
            done
            if [ -z "$PENDING_GROUPS" ]; then
                echo "All targets are healthy."
                break
            fi
            if [ $(( $(date +%s) + TARGET_HEALTH_POLL_SECONDS )) -gt "$TARGET_DEADLINE" ]; then
                echo "WARNING: targets in${PENDING_GROUPS} still not healthy after $(( TARGET_HEALTH_WAIT_SECONDS / 60 )) minutes; leaving them to their groups' health checks"
                # Some targets failing is the group's to replace; none healthy
                # is a tier down. A read that fails decides nothing
                for tg in $PENDING_ARNS; do
                    if COUNTS=$(aws elbv2 describe-target-health --target-group-arn "$tg" \
                        --region "$REGION" \
                        --query '[length(TargetHealthDescriptions[?TargetHealth.State==`healthy`]), length(TargetHealthDescriptions[?TargetHealth.State==`initial` || TargetHealth.State==`unhealthy`])]' \
                        --output text); then
                        read -r healthy not_ready <<< "$COUNTS"
                        if [ "$healthy" = "0" ] && [ "${not_ready:-0}" != "0" ]; then
                            tg_name="${tg#*targetgroup/}"
                            NOT_SERVING_GROUPS="${NOT_SERVING_GROUPS} ${tg_name%%/*}"
                        fi
                    fi
                done
                break
            fi
            sleep "$TARGET_HEALTH_POLL_SECONDS"
        done
    fi
else
    echo "WARNING: could not read the services' target groups; not waiting for their targets"
fi

if [ -n "$SUSPENDED_ASGS" ]; then
    echo "Resuming host health checks..."
    resume_health_checks
fi

if [ -n "$NOT_SERVING_GROUPS" ]; then
    echo "ERROR: no healthy target in${NOT_SERVING_GROUPS}; that tier is not serving the new image."
    echo "Check those target groups and their services' tasks in the console."
    exit 1
fi

# A deploy that leaves a group suspended fails: until someone resumes it, the
# group replaces no dead host, and the next rollout waits 30 minutes for it
if [ -n "$RESUME_FAILED_ASGS" ]; then
    echo "ERROR: HealthCheck is still suspended on${RESUME_FAILED_ASGS}; resume it with the command above."
    exit 1
fi

# Each host now holds the image this rollout replaced; prune it now rather
# than at the next scheduled run
if [ -n "$HYGIENE_ASSOCIATION" ]; then
    echo "Triggering docker hygiene on all hosts..."
    aws ssm start-associations-once --association-ids "$HYGIENE_ASSOCIATION" \
        --region "$REGION" \
        || echo "Warning: could not trigger docker hygiene; it runs on its 6 h schedule"
fi
