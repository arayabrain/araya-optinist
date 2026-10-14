"""
Free Manager Lambda Function

PRIMARY RESPONSIBILITIES:
- Monitor active free tier user count
- Own the free ASG's capacity in both directions by user count: scale out
  when the count asks for more instances, scale in when it asks for fewer
- Choose which instance a scale-in removes: never one running a workflow,
  then the one with the fewest active users
- Actively rebalance idle users across ALL instances (multi-instance algorithm)
- Wait for new instances to become ready, within the invocation's remaining time
- Preserve users with running workflows on their current instance

ARCHITECTURE NOTES:
- This Lambda is the only writer that lowers the free ASG's desired capacity.
  The cpu-high / memory-high alarms may still add an instance; nothing else
  removes one
- Triggered by CloudWatch Events (every 5 minutes)
- Works in conjunction with ALB sticky sessions (5 minutes)
- Users migrate to new instances within 5 minutes after rebalancing
- Timeout: 15 minutes (allows for 7-min instance launch + rebalancing)

IMPROVEMENTS IN THIS VERSION:
- Multi-instance rebalancing: Distributes users evenly across
    ALL instances, not just most/least
- Retry logic: Waits for new instances to launch before rebalancing, for as
    long as the invocation has left (scale_up_wait_budget)
- Effectiveness checking: Verifies distribution is balanced after rebalancing
- Idle threshold: Reduced to 5 minutes (from 10) for faster migration eligibility

Required Environment Variables:
- RDS_HOST: Database host (format: host:port)
- RDS_USER: Database username
- RDS_PASSWORD: Database password
- RDS_DATABASE: Database name
- CLUSTER_NAME: ECS cluster name
- FREE_SERVICE_NAME: ECS service name for free tier
    (e.g., subscr-optinist-cloud-service)
- ASG_NAME: Auto Scaling Group name for free tier instances
- FREE_IDLE_THRESHOLD_MINUTES: Minutes of inactivity to consider user idle (default: 5)
- LOAD_ALARM_NAMES: comma-separated CloudWatch alarms that add capacity
    (cpu-high, memory-high); no scale-in while one is in ALARM or for 15
    minutes after it clears

The instance count floor and ceiling are read from the ASG's own
MinSize/MaxSize, not from configuration held here, so capacity can be
adjusted on the group without redeploying this function.
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Literal

import boto3

if TYPE_CHECKING:
    from mypy_boto3_autoscaling import AutoScalingClient
    from mypy_boto3_cloudwatch import CloudWatchClient
    from mypy_boto3_ec2 import EC2Client
    from mypy_boto3_ecs import ECSClient

# Add parent directory to path for shared imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from aws_constants import ECSTaskStatus  # noqa: E402

# Import utility functions
from free_user_utils import (  # noqa: E402
    count_active_free_users,
    get_idle_users_for_instance,
    get_users_per_instance,
    get_workload_per_instance,
    is_distribution_balanced,
    migrate_user_to_instance,
)


def get_required_env_var(var_name: str, default_value: str | None = None) -> str:
    """
    Safely get required environment variable with helpful error messages.

    Args:
        var_name: Name of the environment variable
        default_value: Optional default value if not provided

    Returns:
        Environment variable value

    Raises:
        ValueError: If required environment variable is missing and no default provided
    """
    value = os.environ.get(var_name, default_value)
    if value is None or value == "":
        raise ValueError(
            f"Missing required environment variable: {var_name}. "
            f"Check your Terraform configuration and Lambda environment settings."
        )
    return value


# Users one instance is provisioned to serve. Only the instance count scales,
# so this is the divisor that turns an active user count into a target count.
# Evaluated on every scheduled run at any user count: 0 users ask for MinSize,
# which is how the group returns to its baseline after a busy period.
USERS_PER_INSTANCE = 5

# Scale-out wait ceiling (hook ~5 min + boot ~5 min + task ~7 min), bounded
# further by the invocation's remaining time: scale_up_wait_budget
MAX_WAIT_TIME = 1020
CHECK_INTERVAL = 60

# Seconds left when the wait loop must stop, so this invocation's finally
# clears the lock
WAIT_MARGIN = 60

# Schedule interval = metric period of the surplus history (one
# TargetInstances datapoint per run)
SCHEDULE_INTERVAL = 300

# A surplus of 1 goes after this many consecutive runs; 2+ goes at once
SURPLUS_OF_ONE_RUNS = 3

# No scale-in while a load alarm is in ALARM, nor for this long after:
# load_alarm_holds_capacity
ALARM_QUIET_PERIOD = 900

# Initialize AWS clients
ecs_client: "ECSClient" = boto3.client("ecs")
autoscaling_client: "AutoScalingClient" = boto3.client("autoscaling")
cloudwatch_client: "CloudWatchClient" = boto3.client("cloudwatch")
ec2_client: "EC2Client" = boto3.client("ec2")


def handler(event, context):
    """
    Main Lambda handler for Free Manager - supports dual triggers.

    Triggered by:
    1. CloudWatch Event (every 5 minutes) - full monitoring and scaling
    2. ASG lifecycle events - immediate ECS sync

    Returns:
        Dictionary with status and actions taken
    """
    print("Free Manager Lambda triggered")
    print(f"Event: {json.dumps(event)}")

    try:
        # Detect trigger type
        event_source = event.get("source", "")

        if event_source == "aws.autoscaling":
            # ASG event - quick sync only
            print("Triggered by ASG event - performing ECS sync")
            return handle_asg_event(event, context)
        else:
            # Scheduled event - full monitoring
            print("Triggered by schedule - performing full monitoring")
            return handle_scheduled_monitoring(event, context)

    except Exception as e:
        print(f"Error in Free Manager Lambda: {e}")
        import traceback

        traceback.print_exc()
        # Fail the invocation so the error surfaces on the Lambda Errors metric,
        # which the free-manager-errors alarm watches. Returning a 500 body
        # would read as a success to Lambda and leave the alarm blind.
        raise


def handle_scheduled_monitoring(event, context):
    """
    Handle periodic monitoring (every 5 minutes).

    Responsibilities:
    - Resync the ECS desired count to the ASG
    - Count active users
    - Scale ASG if needed
    - Rebalance users across instances
    - Publish metrics
    """
    try:
        # Get configuration
        activity_threshold_minutes = int(
            get_required_env_var("FREE_IDLE_THRESHOLD_MINUTES", "10")
        )

        # Backstop for the ASG-event sync, which fires once per launch or
        # terminate and is not retried. Runs first: a task-less instance has
        # to be corrected whether or not there is anyone to scale for.
        # Raise-only - see sync_ecs_to_asg().
        #
        # Held rather than raised: the count, the metric and the scaling
        # decision are all still worth doing. Re-raised at the end so the
        # failure still reaches the Errors metric.
        ecs_sync: Dict[str, Any] = {"changed": False}
        sync_error: Exception | None = None
        try:
            ecs_sync = sync_ecs_to_asg()
        except Exception as e:
            print(f"ECS resync failed, continuing with the rest of the run: {e}")
            sync_error = e

        # Count active free tier users
        active_user_count = count_active_free_users(
            activity_threshold_minutes=activity_threshold_minutes
        )
        print(f"Active free tier users: {active_user_count}")

        # Publish metric to CloudWatch
        publish_active_user_metric(active_user_count)

        # Every run computes a target, at any count: 0 users must bring the
        # group back to MinSize
        scheduled_at = scheduled_period(event)
        result = scale_and_rebalance(
            active_user_count=active_user_count,
            context=context,
            scheduled_at=scheduled_at,
        )

        result["ecs_resynced"] = ecs_sync["changed"]

        if sync_error is not None:
            raise sync_error

        # Published last, by a run that got this far only: a failed run
        # leaves no datapoint, which the surplus history reads as a hole
        publish_target_metric(result["desired_instances"], timestamp=scheduled_at)

        return {"statusCode": 200, "body": json.dumps(result)}

    except Exception as e:
        print(f"Error in scheduled monitoring: {e}")
        import traceback

        traceback.print_exc()
        raise


def handle_asg_event(event, context):
    """
    Handle ASG lifecycle events - sync ECS to ASG immediately.

    This provides immediate response when ASG scales (manual or alarm-driven),
    without waiting for the next 5-minute scheduled run.
    """
    print("\n" + "=" * 70)
    print("ASG EVENT HANDLER")
    print("=" * 70)

    try:
        # Extract event details
        detail = event.get("detail", {})
        asg_name = detail.get("AutoScalingGroupName", "")
        event_type = event.get("detail-type", "")

        print(f"Event Type: {event_type}")
        print(f"ASG Name: {asg_name}")

        # Verify this is our ASG
        expected_asg = get_required_env_var("ASG_NAME")
        if asg_name != expected_asg:
            print(f"Event is for different ASG ({asg_name}), ignoring")
            return {
                "statusCode": 200,
                "body": json.dumps({"message": "Event ignored - different ASG"}),
            }

        # Every event syncs the same way: ECS follows the ASG up, and down to
        # the instances still in the group (a completed termination has left
        # it, so the count is one lower).
        synced = sync_ecs_to_asg()

        if synced["changed"]:
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "message": "ECS synced to ASG",
                        "asg_desired": synced["asg_desired"],
                        "ecs_previous": synced["ecs_previous"],
                        "ecs_new": synced["ecs_new"],
                    }
                ),
            }
        else:
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {"message": "Already in sync", "capacity": synced["asg_desired"]}
                ),
            }

    except Exception as e:
        print(f"Error handling ASG event: {e}")
        import traceback

        traceback.print_exc()
        raise


def is_scaling_in_progress() -> bool:
    """Check if a scaling operation is currently in progress.

    Returns:
        True if scaling is in progress, False otherwise
    """
    try:
        response = cloudwatch_client.get_metric_data(
            MetricDataQueries=[
                {
                    "Id": "scaling_lock",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": (
                                "OptiNiSt/FreeManager/" f"{os.environ['ENV_PREFIX']}"
                            ),
                            "MetricName": "ScalingInProgress",
                        },
                        "Period": 900,  # 15 minutes
                        "Stat": "Maximum",
                    },
                    "ReturnData": True,
                }
            ],
            StartTime=datetime.now(timezone.utc) - timedelta(minutes=15),
            EndTime=datetime.now(timezone.utc),
        )

        values = response["MetricDataResults"][0].get("Values", [])
        if values and values[0] > 0:
            print("⚠️  Scaling operation already in progress, skipping")
            return True
        return False

    except Exception as e:
        print(f"Warning: Could not check scaling lock: {e}")
        return False  # Fail open - allow scaling if check fails


def surplus_of_one_has_persisted(now: datetime | None = None) -> bool:
    """
    Whether the group has been one instance above the target for the last
    SURPLUS_OF_ONE_RUNS completed periods.

    A surplus of one is the boundary case -- 5 users against 6 -- and
    removing it at once would thrash. Instead it goes when it has lasted
    three consecutive completed periods. The history is the TargetInstances
    metric this function publishes on every run, read against the group's
    own GroupDesiredCapacity, period by period: no state of its own.

    The periods are the schedule interval, aligned to multiples of it (the
    query's bounds are, and CloudWatch cuts its periods from the start
    bound), and the current, incomplete period is excluded: this run's own
    datapoint is seconds old and may or may not be readable yet, and
    counting it would make the wait 10 or 15 minutes by chance. Each of the
    three periods must be present in both series -- a run that failed
    published nothing, and the wait starts over.

    Args:
        now: the run's period start from scheduled_period(), so the window
            is the same whether the run fired early or late in its period;
            None (a direct invocation) uses the clock

    Returns:
        True when the SURPLUS_OF_ONE_RUNS completed periods before ``now``
        all show the desired capacity at least one above the target

    Raises:
        Exception: if the metrics cannot be read. Not caught -- an
            unreadable history must not be taken for either answer.
    """
    env_prefix = os.environ["ENV_PREFIX"]
    asg_name = get_required_env_var("ASG_NAME")
    now = now or datetime.now(timezone.utc)
    end = datetime.fromtimestamp(
        int(now.timestamp()) // SCHEDULE_INTERVAL * SCHEDULE_INTERVAL, timezone.utc
    )
    start = end - timedelta(seconds=SCHEDULE_INTERVAL * SURPLUS_OF_ONE_RUNS)
    expected = [
        end - timedelta(seconds=SCHEDULE_INTERVAL * k)
        for k in range(1, SURPLUS_OF_ONE_RUNS + 1)
    ]
    response = cloudwatch_client.get_metric_data(
        MetricDataQueries=[
            {
                "Id": "target",
                "MetricStat": {
                    "Metric": {
                        "Namespace": f"OptiNiSt/FreeUsers/{env_prefix}",
                        "MetricName": "TargetInstances",
                    },
                    "Period": SCHEDULE_INTERVAL,
                    # The highest target a period asked for: the surplus is
                    # measured against the most demanding run in it.
                    "Stat": "Maximum",
                },
                "ReturnData": True,
            },
            {
                "Id": "desired",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/AutoScaling",
                        "MetricName": "GroupDesiredCapacity",
                        "Dimensions": [
                            {"Name": "AutoScalingGroupName", "Value": asg_name}
                        ],
                    },
                    "Period": SCHEDULE_INTERVAL,
                    # The lowest desired capacity in the period: a scale-in
                    # already under way counts against the surplus.
                    "Stat": "Minimum",
                },
                "ReturnData": True,
            },
        ],
        StartTime=start,
        EndTime=end,
        ScanBy="TimestampDescending",
    )

    series = {
        r["Id"]: {
            int(t.timestamp()): v
            for t, v in zip(r.get("Timestamps", []), r.get("Values", []))
        }
        for r in response["MetricDataResults"]
    }
    target = series.get("target", {})
    desired = series.get("desired", {})
    gaps: List[int | None] = []
    for period in expected:
        key = int(period.timestamp())
        if key in target and key in desired:
            gaps.append(int(desired[key] - target[key]))
        else:
            gaps.append(None)
    print(
        f"Surplus history over the {SURPLUS_OF_ONE_RUNS} completed periods before "
        f"{end:%H:%M}Z, newest first (desired - target, None = no datapoint): {gaps}"
    )
    return all(g is not None and g >= 1 for g in gaps)


def load_alarm_holds_capacity(now: datetime | None = None) -> bool:
    """
    Whether a load alarm that adds capacity is in ALARM, or left it less
    than ALARM_QUIET_PERIOD ago.

    The cpu-high / memory-high alarms may add an instance the user count
    did not ask for. While one of them says the load needs it, this
    function does not take it away. Nor for a quiet period after it
    clears: the surplus history has been accumulating all through the
    ALARM stretch, so without the quiet period the instance would go on
    the first run after OK, and a load that needs more than one instance
    but less than two would cycle between the alarm adding and this
    function removing. The quiet period gives the load time to show on
    the larger group before the user count takes the instance back.

    Args:
        now: the clock, for tests; defaults to the current time

    Returns:
        True if any alarm named in LOAD_ALARM_NAMES is in ALARM or changed
        state less than ALARM_QUIET_PERIOD ago

    Raises:
        Exception: if the alarms cannot be read. Not caught -- see
            surplus_of_one_has_persisted().
    """
    names = [n for n in os.environ.get("LOAD_ALARM_NAMES", "").split(",") if n]
    if not names:
        return False
    now = now or datetime.now(timezone.utc)
    response = cloudwatch_client.describe_alarms(AlarmNames=names)
    holding = []
    for alarm in response.get("MetricAlarms", []):
        if alarm.get("StateValue") == "ALARM":
            holding.append(f"{alarm['AlarmName']} (ALARM)")
            continue
        changed = alarm.get("StateTransitionedTimestamp")
        if changed is None:
            continue
        since = (now - changed).total_seconds()
        if since < ALARM_QUIET_PERIOD:
            holding.append(
                f"{alarm['AlarmName']} ({alarm.get('StateValue')} for {int(since)}s)"
            )
    if holding:
        print(f"Load alarm(s) holding capacity: {holding}")
    return bool(holding)


def set_scaling_lock(in_progress: bool) -> None:
    """Set or clear the scaling lock.

    Args:
        in_progress: True to set lock, False to clear it
    """
    try:
        cloudwatch_client.put_metric_data(
            Namespace=f"OptiNiSt/FreeManager/{os.environ['ENV_PREFIX']}",
            MetricData=[
                {
                    "MetricName": "ScalingInProgress",
                    "Value": 1.0 if in_progress else 0.0,
                    "Unit": "None",
                }
            ],
        )
        status = "SET" if in_progress else "CLEARED"
        print(f"Scaling lock {status}")
    except Exception as e:
        print(f"Warning: Could not set scaling lock: {e}")


def sync_ecs_to_asg() -> Dict[str, Any]:
    """
    Point the ECS service's desired count at the ASG.

    The ASG is the source of truth for free-tier capacity. An instance the ASG
    launched but ECS placed no task on cannot answer /health, so it is an
    unhealthy target serving nobody. Terraform no longer corrects this
    (desired_count is ignored there), so this is the only thing that does.

    Direction, the same on every call (launch and terminate events, every
    scheduled run):

    - raise to the ASG's desired capacity when ECS is below it;
    - lower to the number of instances still in the group, counting every
      lifecycle state, and never below the ASG's desired capacity.

    The floor is the instance count, not "one less per terminate event". The
    events of a multi-instance scale-in complete within the same second and
    are handled concurrently; two handlers that each read 4 and write 3 leave
    ECS one too high for good. Counting what is still in the group is
    idempotent, so the last handler, and any later scheduled run, converge it.

    Why the floor counts instances that are still terminating: on scale-in
    the ASG deregisters the instance and waits out the target group's
    deregistration delay (300 s by default) *before* the terminate hook, and
    ECS still sees the container instance as ACTIVE throughout. Lowering the
    desired count while it is in the group would let ECS pick which task to
    stop by AZ balance -- possibly a survivor's -- leaving no healthy free
    target until a replacement boots. Once the instance has left the group,
    its task is already gone, so lowering to the remaining count stops nothing.

    Raising is always safe, and is the only direction a missed sync can need:
    the state it leaves behind is an instance without a task. A missed lower
    costs a pending task until the next scheduled run.

    Returns:
        Dictionary with asg_desired, ecs_previous and whether it changed
    """
    asg_name = get_required_env_var("ASG_NAME")
    cluster_name = get_required_env_var("CLUSTER_NAME")
    service_name = get_required_env_var("FREE_SERVICE_NAME")

    asg_response = autoscaling_client.describe_auto_scaling_groups(
        AutoScalingGroupNames=[asg_name]
    )
    if not asg_response["AutoScalingGroups"]:
        raise ValueError(f"ASG {asg_name} not found")

    asg = asg_response["AutoScalingGroups"][0]
    asg_desired = asg["DesiredCapacity"]
    in_group = len(asg.get("Instances", []))
    print(f"ASG desired capacity: {asg_desired}, instances in the group: {in_group}")

    ecs_response = ecs_client.describe_services(
        cluster=cluster_name, services=[service_name]
    )
    if not ecs_response["services"]:
        raise ValueError(f"ECS service {service_name} not found")

    ecs_desired = ecs_response["services"][0]["desiredCount"]
    print(f"ECS desired count: {ecs_desired}")

    if asg_desired == ecs_desired:
        print(f"Already in sync at {asg_desired}")
        return {
            "asg_desired": asg_desired,
            "ecs_previous": ecs_desired,
            "changed": False,
        }

    target = asg_desired

    if asg_desired < ecs_desired:
        # Lower to what is still in the group, never below the ASG target.
        floor = max(asg_desired, in_group)
        if ecs_desired <= floor:
            print(
                f"ECS ({ecs_desired}) is above the ASG ({asg_desired}) but not "
                f"above the {in_group} instance(s) still in the group; leaving "
                f"it until they have left rather than choosing a task to stop "
                f"mid-deregistration"
            )
            return {
                "asg_desired": asg_desired,
                "ecs_previous": ecs_desired,
                "changed": False,
            }
        target = floor

    print(f"Syncing ECS from {ecs_desired} to {target}")
    ecs_client.update_service(
        cluster=cluster_name, service=service_name, desiredCount=target
    )
    print(f"Successfully synced ECS to {target}")

    return {
        "asg_desired": asg_desired,
        "ecs_previous": ecs_desired,
        "ecs_new": target,
        "changed": True,
    }


def calculate_desired_instances(
    active_user_count: int,
    asg_min_size: int,
    asg_max_size: int,
) -> int:
    """
    Calculate the target instance count for the given active user count.

    One instance per USERS_PER_INSTANCE users, clamped to the ASG's own
    bounds. The bounds come from the group itself rather than from
    configuration held here, so the capacity the ASG is configured to allow
    is the capacity this function can ask for -- `SetDesiredCapacity` rejects
    anything outside them.

    Args:
        active_user_count: Current number of active free tier users
        asg_min_size: ASG MinSize
        asg_max_size: ASG MaxSize

    Returns:
        Target instance count, within [asg_min_size, asg_max_size]
    """
    by_user_count = (active_user_count + USERS_PER_INSTANCE - 1) // USERS_PER_INSTANCE
    return min(max(asg_min_size, by_user_count), asg_max_size)


def scale_up_wait_budget(context: Any) -> int:
    """
    Seconds the scale-out wait loop may run in this invocation.

    MAX_WAIT_TIME alone exceeds the function's 900 s timeout. A loop bounded
    only by it is killed by the platform on a slow launch, and a killed
    invocation never runs the finally that clears the scaling lock, so the
    lock strands for its whole 15-minute metric window. Bounding by the
    remaining time, less a margin for the clean-up, keeps the finally
    reachable.

    Args:
        context: Lambda context; anything without
            get_remaining_time_in_millis() (local runs) falls back to the
            constant

    Returns:
        Wait budget in seconds, at least 0
    """
    remaining = getattr(context, "get_remaining_time_in_millis", None)
    if not callable(remaining):
        return MAX_WAIT_TIME
    try:
        remaining_seconds = int(remaining()) // 1000
    except (TypeError, ValueError):
        return MAX_WAIT_TIME
    return max(0, min(MAX_WAIT_TIME, remaining_seconds - WAIT_MARGIN))


def scheduled_period(event: Dict[str, Any]) -> datetime | None:
    """
    The period a scheduled run belongs to, from the event's own fire time.

    EventBridge stamps a scheduled event with ``time``, the fire time, and
    fire times are exactly the schedule interval apart. Floored to the
    interval, that is the period the run's TargetInstances datapoint is
    published into and the period the surplus history is read up to -- so
    which period a run lands in does not depend on delivery delay, a cold
    start or a slow database connection. The clock alone could put a run
    that fires seconds before a period boundary on either side of it.

    Args:
        event: the Lambda event

    Returns:
        The period start, or None when the event carries no usable ``time``
        (a direct invocation), in which case callers use the clock
    """
    fired = event.get("time") if isinstance(event, dict) else None
    if not isinstance(fired, str):
        return None
    try:
        fired_at = datetime.fromisoformat(fired.replace("Z", "+00:00"))
    except ValueError:
        print(f"Unreadable event time {fired!r}; using the clock")
        return None
    if fired_at.tzinfo is None:
        fired_at = fired_at.replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(
        int(fired_at.timestamp()) // SCHEDULE_INTERVAL * SCHEDULE_INTERVAL,
        timezone.utc,
    )


def scale_and_rebalance(
    active_user_count: int,
    context: Any = None,
    scheduled_at: datetime | None = None,
) -> Dict[str, Any]:
    """
    Scale the free ASG to the user count and rebalance idle users.

    Both directions. Scale-out raises the ASG and ECS and waits for the new
    instances; scale-in terminates instances this function chooses, so a
    running workflow is never the one removed.

    The target is returned as ``desired_instances`` on every path, the
    lock-held one included; the caller publishes it once the run has
    succeeded.

    Args:
        active_user_count: Current number of active free tier users
        context: Lambda context, for the scale-out wait budget
        scheduled_at: the run's period start (scheduled_period); None uses
            the clock

    Returns:
        Dictionary with scaling and rebalancing results
    """
    print("\n" + "=" * 70)
    print("SCALE AND REBALANCE")
    print("=" * 70)
    print(f"Active users: {active_user_count}")

    cluster_name = get_required_env_var("CLUSTER_NAME")
    service_name = get_required_env_var("FREE_SERVICE_NAME")

    # Get current service state
    service_info = get_service_info(cluster_name, service_name)
    current_desired = service_info["desired_count"]
    current_running = service_info["running_count"]
    current_ecs_desired = service_info["ecs_desired_count"]
    asg_min_size = service_info["min_size"]
    asg_max_size = service_info["max_size"]

    print("\nCurrent state from get_service_info:")
    print(f"- Desired capacity (ASG): {current_desired}")
    print(f"- Running tasks (ECS): {current_running}")
    print(f"- ASG bounds: min={asg_min_size}, max={asg_max_size}")

    desired_instances = calculate_desired_instances(
        active_user_count, asg_min_size, asg_max_size
    )

    print(f"\nCalculated desired instances: {desired_instances}")
    print(
        f"Formula: min(max({asg_min_size}, ceil({active_user_count} / "
        f"{USERS_PER_INSTANCE})), {asg_max_size})"
    )

    # The cap is otherwise silent: the target is clamped and nothing says the
    # user count asked for more than the group allows.
    by_user_count = (active_user_count + USERS_PER_INSTANCE - 1) // USERS_PER_INSTANCE
    if by_user_count > asg_max_size:
        print(
            f"WARNING: {active_user_count} active users want {by_user_count} "
            f"instances, above MaxSize {asg_max_size}. Capacity is capped; "
            f"raise asg_max_size to serve them."
        )

    # Check if scaling is already in progress
    if is_scaling_in_progress():
        return {
            "status": "scaling_in_progress",
            "message": "Another scaling operation is in progress, skipping",
            "active_users": active_user_count,
            "desired_instances": desired_instances,
        }

    result = {
        "active_users": active_user_count,
        "current_instances": current_running,
        "desired_instances": desired_instances,
        "scaling_action": "none",
        "rebalanced_users": [],
    }

    # Scale up if needed
    if desired_instances > current_desired:
        print(f"Scaling up from {current_desired} to {desired_instances} instances")

        # Set scaling lock before starting
        set_scaling_lock(True)

        try:
            # Passed even on the scale-up branch: the branch is chosen by the
            # ASG's desired capacity, so an alarm that already lowered the ASG
            # can leave ECS above the target while this reads as a scale-up.
            scale_service(
                cluster_name,
                service_name,
                desired_instances,
                current_ecs_desired=current_ecs_desired,
            )
            result["scaling_action"] = "scale_up"

            # Wait for instances to launch and rebalance with retry logic
            max_wait_time = scale_up_wait_budget(context)
            check_interval = CHECK_INTERVAL
            start_time = time.time()
            attempt = 0

            print(
                f"\nWaiting for new instances to launch and become ECS-ready "
                f"(up to {max_wait_time}s this invocation, includes lifecycle "
                f"hooks)"
            )

            rebalanced = []
            rebalancing_successful = False

            while time.time() - start_time < max_wait_time:
                attempt += 1
                elapsed = int(time.time() - start_time)

                print(
                    f"\n[Attempt {attempt}] Checking instance readiness "
                    f"(elapsed: {elapsed}s / {max_wait_time}s)"
                )

                # Check if desired number of instances are ready
                available_instances = get_available_instance_ids(
                    cluster_name, service_name
                )
                print(
                    f"Found {len(available_instances)}/{desired_instances} "
                    f"running instances: {available_instances}"
                )

                if len(available_instances) >= desired_instances:
                    print("All instances ready! Attempting rebalancing")

                    # Try multi-instance rebalancing
                    rebalanced = rebalance_idle_users_multi(available_instances)

                    # Check if rebalancing was effective
                    distribution = get_users_per_instance()
                    if is_distribution_balanced(distribution, tolerance=1):
                        print("Rebalancing successful - distribution is balanced!")
                        rebalancing_successful = True
                        break
                    else:
                        print("Distribution still imbalanced, will retry")

                else:
                    print(
                        f"Only {len(available_instances)}/{desired_instances} "
                        f"instances ready, waiting {check_interval}s before next check"
                    )

                # Wait before next check (unless this is the last iteration)
                if time.time() - start_time + check_interval < max_wait_time:
                    time.sleep(check_interval)
                else:
                    print(
                        f"\nWait budget reached ({max_wait_time}s). "
                        f"Rebalancing will be retried on next Lambda run (every 5 min)."
                    )
                    break

            # Update result after loop completes
            result["rebalanced_users"] = rebalanced
            result["rebalancing_successful"] = rebalancing_successful
            result["rebalancing_attempts"] = attempt

        finally:
            # Always clear the lock when done
            set_scaling_lock(False)

    elif desired_instances < current_desired:
        # Scale down:
        # - nothing while a load alarm holds capacity
        # - a surplus of 2+ at once; a surplus of 1 once it has persisted
        surplus = current_desired - desired_instances
        result["scaling_action"] = "none"
        if load_alarm_holds_capacity():
            print(
                f"No scale down - a load alarm is in ALARM or left it less than "
                f"{ALARM_QUIET_PERIOD}s ago, the surplus of {surplus} stays"
            )
        elif surplus >= 2 or surplus_of_one_has_persisted(now=scheduled_at):
            print(
                f"Scaling down from {current_desired} to {desired_instances} instances"
            )
            terminated = scale_in(service_info["instances"], surplus)
            result["terminated_instances"] = terminated
            if terminated:
                result["scaling_action"] = "scale_down"
        else:
            print("No scale down - a surplus of 1 that has not persisted yet")
    else:
        # No scaling needed, but check if rebalancing needed
        print("No scaling needed, checking if rebalancing is beneficial")

        available_instances = get_available_instance_ids(cluster_name, service_name)
        distribution = get_users_per_instance()

        # Build complete distribution including instances with 0 users
        # (get_users_per_instance only returns instances that have users)
        complete_distribution = {inst_id: 0 for inst_id in available_instances}
        complete_distribution.update(distribution)

        print(f"Complete distribution across all instances: {complete_distribution}")

        if not is_distribution_balanced(complete_distribution, tolerance=1):
            print("Distribution is imbalanced, attempting rebalancing")
            rebalanced = rebalance_idle_users_multi(available_instances)
            result["rebalanced_users"] = rebalanced
            result["rebalancing_reason"] = "imbalance_without_scaling"
        else:
            print("Distribution is already balanced, no action needed")

    return result


def get_service_info(cluster_name: str, service_name: str) -> Dict[str, Any]:
    """
    Get current ASG and ECS service information.

    For free tier, we check both the ASG desired capacity and ECS service
    status. The ASG desired capacity is the source of truth for scaling, and
    its MinSize/MaxSize are the bounds any new capacity must fall within.

    Args:
        cluster_name: ECS cluster name
        service_name: ECS service name

    Returns:
        Dictionary with info (desired_count, running_count, pending_count,
        min_size, max_size)
    """
    print("\n" + "=" * 60)
    print("GET SERVICE INFO")
    print("=" * 60)

    asg_name = get_required_env_var("ASG_NAME")

    # Get ASG information
    print(f"Querying ASG: {asg_name}")
    asg_response = autoscaling_client.describe_auto_scaling_groups(
        AutoScalingGroupNames=[asg_name]
    )

    if not asg_response["AutoScalingGroups"]:
        raise Exception(f"Auto Scaling Group {asg_name} not found")

    asg = asg_response["AutoScalingGroups"][0]

    print("\nASG Status:")
    print(f"Desired Capacity: {asg['DesiredCapacity']}")
    print(f"Min Size: {asg['MinSize']}")
    print(f"Max Size: {asg['MaxSize']}")
    print(f"Instances: {len(asg['Instances'])}")

    for inst in asg["Instances"]:
        print(
            f"- {inst['InstanceId']}: {inst['LifecycleState']}, "
            f"Health={inst['HealthStatus']}"
        )

    # Get ECS service information
    print(f"\nQuerying ECS Service: {service_name}")
    print(f"Cluster: {cluster_name}")
    ecs_response = ecs_client.describe_services(
        cluster=cluster_name, services=[service_name]
    )

    if not ecs_response["services"]:
        raise Exception(f"Service {service_name} not found in cluster {cluster_name}")

    service = ecs_response["services"][0]

    print("\nECS Service Status:")
    print(f"Desired Count: {service['desiredCount']}")
    print(f"Running Count: {service['runningCount']}")
    print(f"Pending Count: {service['pendingCount']}")

    # List tasks
    print("\nQuerying ECS tasks")
    tasks_response = ecs_client.list_tasks(
        cluster=cluster_name,
        serviceName=service_name,
        desiredStatus=ECSTaskStatus.RUNNING,
    )
    task_arns = tasks_response.get("taskArns", [])
    print(f"Running tasks: {len(task_arns)}")

    # Return ASG capacity and bounds (source of truth for scaling)
    # but also include ECS running/pending counts
    result: Dict[str, Any] = {
        "desired_count": asg["DesiredCapacity"],
        "running_count": service["runningCount"],
        "pending_count": service["pendingCount"],
        "min_size": asg["MinSize"],
        "max_size": asg["MaxSize"],
        # ECS's own desired count, distinct from desired_count above (the
        # ASG's). scale_service() needs it to tell a raise from a lower.
        "ecs_desired_count": service["desiredCount"],
        # The group's own view of its instances, for scale-in to choose from.
        "instances": [
            {
                "id": inst["InstanceId"],
                "az": inst.get("AvailabilityZone", ""),
                "lifecycle_state": inst["LifecycleState"],
                "protected": inst.get("ProtectedFromScaleIn", False),
            }
            for inst in asg["Instances"]
        ],
    }

    print("\nReturning:")
    print(f"desired_count (from ASG): {result['desired_count']}")
    print(f"running_count (from ECS): {result['running_count']}")
    print(f"pending_count (from ECS): {result['pending_count']}")
    print(f"min_size (from ASG): {result['min_size']}")
    print(f"max_size (from ASG): {result['max_size']}")
    print("=" * 60)

    return result


def scale_service(
    cluster_name: str,
    service_name: str,
    desired_count: int,
    current_ecs_desired: int | None = None,
) -> None:
    """
    Update ASG desired capacity to scale the free tier instances out.

    Unlike premium tier which uses individual EC2 instances, free tier
    uses an Auto Scaling Group (ASG) with ECS. We need to scale the ASG
    directly, not just the ECS service desired count.

    This is manual ASG management to avoid the runaway scaling issue that
    occurs with ECS managed scaling (where instance startup CPU spikes
    trigger additional scaling events).

    Scale-in does not come through here: lowering the desired capacity
    leaves the choice of instance to the group's termination policy, which
    cannot see workloads. See scale_in().

    Args:
        cluster_name: ECS cluster name (used for logging only)
        service_name: ECS service name
        desired_count: New desired capacity for the ASG
        current_ecs_desired: Current ECS desired count, to decide direction
    """
    asg_name = get_required_env_var("ASG_NAME")

    print(f"Scaling ASG {asg_name} to desired capacity: {desired_count}")

    try:
        # Set ASG desired capacity directly
        autoscaling_client.set_desired_capacity(
            AutoScalingGroupName=asg_name,
            DesiredCapacity=desired_count,
            HonorCooldown=False,  # Immediate scaling, no cooldown wait
        )

        print(
            f"Successfully set ASG {asg_name} desired capacity to " f"{desired_count}"
        )

        # ECS is raised here but never lowered here. On scale-in the terminate
        # event lowers it, once the instance is gone and ECS has no choice of
        # task to make - see sync_ecs_to_asg().
        if current_ecs_desired is not None and desired_count < current_ecs_desired:
            print(
                f"Leaving ECS at {current_ecs_desired}; the terminate event "
                f"lowers it to {desired_count}"
            )
            return

        ecs_client.update_service(
            cluster=cluster_name, service=service_name, desiredCount=desired_count
        )

        print(
            f"Successfully updated ECS service {service_name} to "
            f"{desired_count} tasks"
        )

    except Exception as e:
        print(f"Error scaling ASG: {e}")
        raise


def select_instances_to_terminate(
    instances: List[Dict[str, Any]],
    users_per_instance: Dict[str, int],
    workload_per_instance: Dict[str, int],
    count: int,
) -> List[str]:
    """
    Choose which instances a scale-in removes.

    Excluded outright: any instance with a running workflow, and any that is
    not InService or is protected. If fewer than ``count`` remain, the
    remainder is left for a later run rather than taken from the excluded
    ones -- a running workflow is never interrupted to shed capacity.

    Order among the rest:

    1. fewest active users -- an instance with none loses nobody's session;
    2. the availability zone holding the most of the group's instances --
       a decrement that unbalances the zones invites the group to
       rebalance, which terminates an instance of its own choosing;
    3. the most recently launched -- sticky sessions accumulate on
       long-lived instances, as the group's own NewestInstance policy
       assumes.

    Args:
        instances: get_service_info()["instances"], each with ``id``,
            ``az``, ``lifecycle_state``, ``protected`` and ``launch_time``
        users_per_instance: active users keyed by instance id
        workload_per_instance: running workflows keyed by instance id
        count: how many to choose

    Returns:
        Instance ids in termination order, at most ``count``
    """
    candidates = []
    for inst in instances:
        if inst["lifecycle_state"] != "InService" or inst.get("protected"):
            print(f"- {inst['id']}: {inst['lifecycle_state']}, not a candidate")
            continue
        workflows = workload_per_instance.get(inst["id"], 0)
        if workflows > 0:
            print(f"- {inst['id']}: {workflows} running workflow(s), kept")
            continue
        candidates.append(inst)

    # One pick at a time, zones recounted after each (InService only, as the
    # group counts them): two picks from one zone is the imbalance to avoid
    remaining = [inst for inst in instances if inst["lifecycle_state"] == "InService"]
    chosen: List[str] = []
    while candidates and len(chosen) < count:
        az_counts: Dict[str, int] = {}
        for inst in remaining:
            az_counts[inst["az"]] = az_counts.get(inst["az"], 0) + 1

        candidates.sort(
            key=lambda inst: (
                users_per_instance.get(inst["id"], 0),
                -az_counts.get(inst["az"], 0),
                -(inst.get("launch_time") or 0),
            )
        )
        pick = candidates.pop(0)
        remaining = [inst for inst in remaining if inst["id"] != pick["id"]]
        chosen.append(pick["id"])
        print(
            f"- {pick['id']}: {users_per_instance.get(pick['id'], 0)} active "
            f"user(s), {pick['az']}, selected"
        )

    if len(chosen) < count:
        print(
            f"Only {len(chosen)} of {count} instance(s) can be removed safely; "
            f"the rest waits for workflows to finish"
        )
    return chosen


def scale_in(instances: List[Dict[str, Any]], count: int) -> List[str]:
    """
    Remove ``count`` instances from the free ASG, chosen by workload.

    Each is terminated with TerminateInstanceInAutoScalingGroup and
    ShouldDecrementDesiredCapacity, so the desired capacity follows the
    terminations one by one and the group never picks an instance itself.
    ECS is not lowered here: the terminate event does that once the
    instance is gone (sync_ecs_to_asg), as for any other termination.

    A termination the API refuses (a scaling activity already in progress,
    say) is skipped and logged; the next run recomputes the target and
    tries again.

    Args:
        instances: get_service_info()["instances"]
        count: how many to remove

    Returns:
        Instance ids whose termination was accepted
    """
    asg_name = get_required_env_var("ASG_NAME")

    # Both reads raise on failure: an empty map would read as "no users" /
    # "no workflow" and misorder the choice
    users_per_instance = get_users_per_instance(
        activity_threshold_minutes=int(
            get_required_env_var("FREE_IDLE_THRESHOLD_MINUTES", "10")
        ),
        raise_on_error=True,
    )
    workload_per_instance = get_workload_per_instance()
    print(f"Active users per instance: {users_per_instance}")
    print(f"Running workflows per instance: {workload_per_instance}")

    # Copy: launch times are added here, the caller's list stays as read
    instances = [dict(inst) for inst in instances]
    in_service = [i["id"] for i in instances if i["lifecycle_state"] == "InService"]
    launch_times: Dict[str, float] = {}
    if in_service:
        ec2_response = ec2_client.describe_instances(InstanceIds=in_service)
        for reservation in ec2_response["Reservations"]:
            for ec2_instance in reservation["Instances"]:
                launch_times[ec2_instance["InstanceId"]] = ec2_instance[
                    "LaunchTime"
                ].timestamp()
    for inst in instances:
        inst["launch_time"] = launch_times.get(inst["id"])

    chosen = select_instances_to_terminate(
        instances, users_per_instance, workload_per_instance, count
    )

    terminated = []
    for instance_id in chosen:
        try:
            autoscaling_client.terminate_instance_in_auto_scaling_group(
                InstanceId=instance_id, ShouldDecrementDesiredCapacity=True
            )
            print(f"Terminating {instance_id} in {asg_name}, desired capacity -1")
            terminated.append(instance_id)
        except Exception as e:
            print(
                f"Could not terminate {instance_id}, leaving it for the next run: {e}"
            )

    return terminated


def rebalance_idle_users_multi(available_instances: List[str]) -> List[str]:
    """
    Rebalance idle users across ALL available instances (multi-instance algorithm).

    This improved algorithm:
    1. Calculates target users per instance (even distribution)
    2. Identifies all overloaded and underloaded instances
    3. Migrates users from overloaded to underloaded instances in round-robin fashion
    4. Ensures balanced distribution across all instances,
        not just most/least loaded pair

    Idle users are defined as currently having no active workflows
    (active_workflow_count = 0).

    Args:
        available_instances: List of available instance IDs to distribute users across

    Returns:
        List of user IDs that were migrated
    """
    print("\n" + "=" * 70)
    print("MULTI-INSTANCE REBALANCING")
    print("=" * 70)
    print(f"Available instances: {available_instances}")

    if len(available_instances) < 2:
        print(
            f"Cannot rebalance: Only {len(available_instances)} instance(s) available"
        )
        return []

    # Get current user distribution
    print("\nGetting current user distribution")
    users_per_instance = get_users_per_instance()

    # Build complete instance map (includes instances with 0 users)
    instance_user_counts = {inst_id: 0 for inst_id in available_instances}
    instance_user_counts.update(users_per_instance)

    print(f"User distribution: {instance_user_counts}")

    total_users = sum(instance_user_counts.values())
    if total_users == 0:
        print("No active users to rebalance")
        return []

    # Calculate target users per instance (even distribution)
    target_per_instance = total_users // len(available_instances)
    print(
        f"Target distribution: {target_per_instance} users per instance "
        f"({total_users} users / {len(available_instances)} instances)"
    )

    # Identify overloaded instances (above target + tolerance)
    overloaded = [
        (inst, count)
        for inst, count in instance_user_counts.items()
        if count > target_per_instance + 1  # Allow 1 user difference
    ]

    # Identify underloaded instances (below target)
    underloaded = [
        (inst, count)
        for inst, count in instance_user_counts.items()
        if count < target_per_instance
    ]

    if not overloaded or not underloaded:
        print("Instances are already balanced")
        return []

    print(f"\nOverloaded instances: {overloaded}")
    print(f"Underloaded instances: {underloaded}")

    # Sort by severity
    overloaded.sort(key=lambda x: x[1], reverse=True)  # Most loaded first
    underloaded.sort(key=lambda x: x[1])  # Least loaded first

    migrated = []
    underloaded_idx = 0  # Round-robin index for destination instances

    # Migrate users from each overloaded instance
    for source_inst, source_count in overloaded:
        users_to_move = source_count - target_per_instance

        print(
            f"\nProcessing overloaded instance {source_inst}: "
            f"{source_count} users (need to move {users_to_move})"
        )

        # Get idle users from this instance
        idle_users = get_idle_users_for_instance(source_inst)

        if not idle_users:
            print(f"No idle users on instance {source_inst}, skipping")
            continue

        # Limit to number of users we need to move
        idle_users_to_migrate = idle_users[:users_to_move]
        print(
            f"Found {len(idle_users)} idle users, "
            f"migrating {len(idle_users_to_migrate)}"
        )

        # Distribute to underloaded instances in round-robin fashion
        for user_id in idle_users_to_migrate:
            if underloaded_idx >= len(underloaded):
                print("All underloaded instances have reached target, stopping")
                break

            dest_inst, _ = underloaded[underloaded_idx]

            # Attempt migration
            if migrate_user_to_instance(user_id, dest_inst):
                migrated.append(user_id)

                # Update counts
                instance_user_counts[source_inst] -= 1
                instance_user_counts[dest_inst] += 1

                # Move to next underloaded instance if current reached target
                if instance_user_counts[dest_inst] >= target_per_instance:
                    underloaded_idx += 1

    print(f"\n{len(migrated)} users migrated successfully")
    print(f"New distribution: {instance_user_counts}")

    return migrated


def rebalance_idle_users() -> List[str]:
    """
    Rebalance idle users to underutilized instances.

    This function:
    1. Discovers all available ECS instances
    2. Gets current user distribution across instances
    3. Identifies idle users on overloaded instances
    4. Migrates idle users to underutilized instances

    Idle users are defined as currently having no active workflows
    (active_workflow_count = 0).

    Returns:
        List of user IDs that were migrated
    """
    print("\n" + "=" * 70)
    print("REBALANCING IDLE USERS")
    print("=" * 70)

    # Get all available ECS instances
    cluster_name = get_required_env_var("CLUSTER_NAME")
    service_name = get_required_env_var("FREE_SERVICE_NAME")

    print(f"\nDiscovering available instances from cluster: {cluster_name}")
    available_instances = get_available_instance_ids(cluster_name, service_name)

    print(f"\nFound {len(available_instances)} available instances")

    if len(available_instances) < 2:
        print(
            f"\nCannot rebalance: Only {len(available_instances)} "
            f"instance(s) available, need at least 2"
        )
        print("=" * 70)
        return []

    print(f"Available instances for rebalancing: {available_instances}")

    # Get current user distribution
    print("\nGetting current user distribution from database")
    users_per_instance = get_users_per_instance()

    print(f"User distribution from DB: {users_per_instance}")

    if not users_per_instance:
        print("\nNo active users to rebalance")
        print("=" * 70)
        return []

    # Build complete instance map (includes instances with 0 users)
    instance_user_counts = {inst_id: 0 for inst_id in available_instances}
    instance_user_counts.update(users_per_instance)

    print(f"Complete instance map: {instance_user_counts}")

    # Find most loaded and least loaded instances
    sorted_instances = sorted(
        instance_user_counts.items(), key=lambda x: x[1], reverse=True
    )

    most_loaded_instance = sorted_instances[0][0]
    least_loaded_instance = sorted_instances[-1][0]

    most_loaded_count = sorted_instances[0][1]
    least_loaded_count = sorted_instances[-1][1]

    print(
        f"Most loaded: {most_loaded_instance} ({most_loaded_count} users), "
        f"Least loaded: {least_loaded_instance} ({least_loaded_count} users)"
    )

    # Only rebalance if there's significant imbalance
    if most_loaded_count - least_loaded_count < 2:
        print("Instances are reasonably balanced, no rebalancing needed")
        return []

    # Get idle users from most loaded instance
    idle_users = get_idle_users_for_instance(most_loaded_instance)

    if not idle_users:
        print(f"No idle users on instance {most_loaded_instance}")
        return []

    print(f"Found {len(idle_users)} idle users on {most_loaded_instance}")

    # Migrate up to 50% of idle users (or enough to balance)
    users_to_migrate = min(
        len(idle_users), (most_loaded_count - least_loaded_count) // 2
    )

    migrated = []
    for user_id in idle_users[:users_to_migrate]:
        if migrate_user_to_instance(user_id, least_loaded_instance):
            migrated.append(user_id)

    print(f"Successfully migrated {len(migrated)} users")
    return migrated


def get_available_instance_ids(cluster_name: str, service_name: str) -> List[str]:
    """
    Get list of RUNNING EC2 instance IDs from ECS cluster.

    Only returns instances that are:
    1. Registered with ECS cluster (ACTIVE container instances)
    2. Have ECS agent connected
    3. Are in 'running' state in EC2 (not pending/stopping/stopped)

    This ensures we only try to rebalance users to instances that are
    actually ready to handle traffic.

    Args:
        cluster_name: ECS cluster name
        service_name: ECS service name (unused, kept for compatibility)

    Returns:
        List of EC2 instance IDs that are fully running and ready
    """
    print("=" * 60)
    print("INSTANCE DISCOVERY DEBUG")
    print("=" * 60)
    print(f"Cluster: {cluster_name}")
    print(f"Service: {service_name}")

    try:
        # List all container instances in the cluster
        # Check all statuses to find available instances
        print("\nStep 1: Listing container instances in ECS cluster")

        all_container_arns = []
        container_statuses: List[Literal["ACTIVE", "DRAINING", "REGISTERING"]] = [
            "ACTIVE",
            "DRAINING",
            "REGISTERING",
        ]
        for container_status in container_statuses:
            response = ecs_client.list_container_instances(
                cluster=cluster_name,
                status=container_status,
                filter="attribute:tier == free",
            )
            arns = response.get("containerInstanceArns", [])
            if arns:
                print(f"Found {len(arns)} {container_status} container instances")
                all_container_arns.extend(arns)

        print(f"Total container instances found: {len(all_container_arns)}")

        if not all_container_arns:
            print("No container instances found in cluster")
            return []

        # Describe container instances to get EC2 instance IDs
        print("\nStep 2: Describing container instances")
        instances_response = ecs_client.describe_container_instances(
            cluster=cluster_name, containerInstances=all_container_arns
        )

        print("\nContainer instance details:")
        all_ecs_instances = []
        for inst in instances_response["containerInstances"]:
            ec2_id = inst["ec2InstanceId"]
            status = inst["status"]
            agent_connected = inst["agentConnected"]
            running_tasks = inst["runningTasksCount"]

            print(f"- {ec2_id}:")
            print(f"Status: {status}")
            print(f"Agent Connected: {agent_connected}")
            print(f"Running Tasks: {running_tasks}")

            all_ecs_instances.append(
                {"id": ec2_id, "status": status, "agent": agent_connected}
            )

        # Get EC2 instance IDs that can accept tasks
        # Include ACTIVE, DRAINING (can still run tasks), and REGISTERING
        # Exclude instances with disconnected agents
        ecs_instance_ids = [
            inst["ec2InstanceId"]
            for inst in instances_response["containerInstances"]
            if inst["status"] in ["ACTIVE", "DRAINING", "REGISTERING"]
            and inst["agentConnected"]
        ]

        print(
            f"\nFiltered for (ACTIVE/DRAINING/REGISTERING) + agentConnected: "
            f"{len(ecs_instance_ids)} instances"
        )
        print(f"Instance IDs: {ecs_instance_ids}")

        if not ecs_instance_ids:
            print("No ECS instances with connected agents")
            print(f"All instances: {all_ecs_instances}")
            return []

        # Check EC2 state to ensure instances are actually running
        print("\nStep 3: Checking EC2 instance states")
        ec2_response = ec2_client.describe_instances(InstanceIds=ecs_instance_ids)

        running_instances = []
        pending_instances = []
        other_states = []

        for reservation in ec2_response["Reservations"]:
            for instance in reservation["Instances"]:
                instance_id = instance["InstanceId"]
                state = instance["State"]["Name"]

                print(f"- {instance_id}: EC2 state = {state}")

                if state == "running":
                    running_instances.append(instance_id)
                elif state == "pending":
                    pending_instances.append(instance_id)
                else:
                    other_states.append((instance_id, state))

        print("\n" + "=" * 60)
        print("INSTANCE DISCOVERY SUMMARY")
        print("=" * 60)
        print(f"Total ECS-registered: {len(ecs_instance_ids)}")
        print(f"Running instances: {len(running_instances)} " f"{running_instances}")
        print(f"Pending instances: {len(pending_instances)} " f"{pending_instances}")
        if other_states:
            print(f"Other states: {other_states}")
        print("=" * 60)

        # Only return running instances
        return running_instances

    except Exception as e:
        print(f"ERROR in get_available_instance_ids: {e}")
        import traceback

        traceback.print_exc()
        return []


def publish_active_user_metric(active_user_count: int) -> None:
    """
    Publish active free tier user count to CloudWatch.

    Args:
        active_user_count: Number of active free tier users
    """
    try:
        cloudwatch_client.put_metric_data(
            Namespace=f"OptiNiSt/FreeUsers/{os.environ['ENV_PREFIX']}",
            MetricData=[
                {
                    "MetricName": "ActiveLogins",
                    "Value": active_user_count,
                    "Unit": "Count",
                }
            ],
        )
        print(f"Published CloudWatch metric: ActiveLogins={active_user_count}")
    except Exception as e:
        print(f"Failed to publish CloudWatch metric: {e}")
        # Don't fail the Lambda if metric publishing fails


def publish_target_metric(
    target_instances: int, timestamp: datetime | None = None
) -> None:
    """
    Publish the instance count the user count asks for.

    Next to ActiveLogins, so it can be plotted against the group's own
    GroupDesiredCapacity and GroupInServiceInstances: a gap that persists is
    a missed scale-in (above) or a scale-in this function could not make
    safely, or capacity an alarm added (below).

    Called at the end of a successful scheduled run only. The surplus
    history counts a period with no datapoint as a hole, so a run that
    failed must not have published.

    Args:
        target_instances: the clamped target from calculate_desired_instances
        timestamp: the run's period start (scheduled_period), so the
            datapoint lands in the period the run belongs to whatever the
            clock says; None lets CloudWatch stamp it with the clock
    """
    try:
        datapoint: Dict[str, Any] = {
            "MetricName": "TargetInstances",
            "Value": target_instances,
            "Unit": "Count",
        }
        if timestamp is not None:
            datapoint["Timestamp"] = timestamp
        cloudwatch_client.put_metric_data(
            Namespace=f"OptiNiSt/FreeUsers/{os.environ['ENV_PREFIX']}",
            MetricData=[datapoint],
        )
        print(
            f"Published CloudWatch metric: TargetInstances={target_instances}"
            + (f" at {timestamp:%H:%M}Z" if timestamp else "")
        )
    except Exception as e:
        print(f"Failed to publish CloudWatch metric: {e}")
        # Don't fail the Lambda if metric publishing fails


# For testing
if __name__ == "__main__":
    # Simulate CloudWatch Event trigger
    test_event = {"source": "aws.events", "detail-type": "Scheduled Event"}
    test_context: Dict[str, Any] = {}

    result = handler(test_event, test_context)
    print(f"Test result: {json.dumps(result, indent=2)}")
