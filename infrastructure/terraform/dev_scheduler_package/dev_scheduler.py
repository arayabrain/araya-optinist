"""
Dev Environment Scheduler Lambda

Starts and stops development environment resources on a schedule to save costs.

Schedule (JST):
- Start: Mon-Fri 08:00 (resources ready by ~08:15)
- Stop:  Mon-Fri 22:00
- Weekends: Stopped (Fri 22:00 -> Mon 08:00)

Commands (replace <env> with environment name, e.g. "development"):

  Manual start (after-hours / weekends):
    aws lambda invoke --function-name <env>-dev-scheduler \
        --cli-binary-format raw-in-base64-out \
        --payload '{"action":"start"}' /dev/stdout

  Manual stop (fast resume):
    aws lambda invoke --function-name <env>-dev-scheduler \
        --cli-binary-format raw-in-base64-out \
        --payload '{"action":"stop","stop_mode":"stop"}' /dev/stdout

  Manual destroy (before long holidays, max savings):
    aws lambda invoke --function-name <env>-dev-scheduler \
        --cli-binary-format raw-in-base64-out \
        --payload '{"action":"stop","stop_mode":"destroy"}' /dev/stdout

  Override (skip next scheduled stop, max 12 hours):
    aws lambda invoke --function-name <env>-dev-scheduler \
        --cli-binary-format raw-in-base64-out \
        --payload '{"action":"override","hours":4}' /dev/stdout

Long holidays / no-development periods:
  To prevent automatic start during extended breaks:
    Option 1 - Disable schedule in EventBridge console:
      Disable rules: <env>-dev-schedule-start, <env>-dev-schedule-verify-start
      Re-enable when development resumes.
    Option 2 - Terraform:
      terraform apply -var="enable_dev_schedule=false"
      (Set back to true when development resumes.)
  Before the break, run a manual destroy to avoid idle costs:
    aws lambda invoke --function-name <env>-dev-scheduler \
        --cli-binary-format raw-in-base64-out \
        --payload '{"action":"stop","stop_mode":"destroy"}' /dev/stdout

Resources managed:
- RDS instance (stop or destroy+snapshot, configurable via stop_mode)
- NAT instance (stop/start)
- Background service EC2 instance (stop/start)
- Premium EC2 instances (stop/start)
- Free tier ASG (scale 0/1)
- ECS services (desired_count 0/1 — prevents failed placement noise)
- Lambda schedule rules (disable/enable)
- CloudWatch alarm actions (disable/enable)

RDS shutdown modes (stop_mode):
  "stop"    - Calls rds stop-db-instance. Fast resume (~2 min).
              EBS still billed. Subject to AWS 7-day auto-restart.
              Recommended for regular weeknight shutdowns.
  "destroy" - Deletes instance with a final snapshot, restores on start.
              Maximum cost savings, slower resume (~10 min).
              Immune to 7-day auto-restart. Use for extended breaks
              (Golden Week, year-end holidays, etc.).

  The mode is set via the event payload (stop_mode field) or the
  DEFAULT_STOP_MODE environment variable. Defaults to "destroy"
  for backward compatibility.
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone

import boto3
from botocore.config import Config

# Maximum override duration (safety cap even if user requests more)
MAX_OVERRIDE_HOURS = 12

# Retry configuration for per-stage retries
MAX_RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 5  # seconds, doubles each attempt

# Timeout for synchronous Lambda invocations (premium_manager cleanup).
# premium_manager has a 600s timeout; allow slightly more for overhead.
LAMBDA_INVOKE_TIMEOUT = 610  # seconds

# RDS instance statuses that are expected to reach `available` shortly.
# ensure_rds_proxy_target polls through these; anything else (and not
# `available`) is treated as a hard failure.
TRANSIENT_RDS_STATUSES = {
    "creating",
    "modifying",
    "backing-up",
    "starting",
    "configuring-enhanced-monitoring",
    "configuring-iam-database-auth",
    "configuring-log-exports",
    "renaming",
    "rebooting",
    "resetting-master-credentials",
    "upgrading",
    "maintenance",
}

# Poll a transient RDS instance for up to this long before deferring proxy
# registration to the verify-start re-invocation.
PROXY_REGISTER_POLL_SECONDS = 90
PROXY_REGISTER_POLL_INTERVAL = 10

rds = boto3.client("rds")
ec2 = boto3.client("ec2")
ecs = boto3.client("ecs")
lambda_client = boto3.client(
    "lambda",
    config=Config(read_timeout=LAMBDA_INVOKE_TIMEOUT, retries={"max_attempts": 0}),
)
autoscaling = boto3.client("autoscaling")
events = boto3.client("events")
cloudwatch = boto3.client("cloudwatch")
ssm = boto3.client("ssm")


def with_retry(fn, *args, max_attempts=MAX_RETRY_ATTEMPTS, **kwargs):
    """Execute a function with retry and exponential backoff.

    Retries if the result string starts with "error". Returns the last result
    if all attempts fail.
    """
    last_result = None
    for attempt in range(1, max_attempts + 1):
        result = fn(*args, **kwargs)
        if not str(result).startswith("error"):
            return result
        last_result = result
        if attempt < max_attempts:
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            print(
                f"Retry {attempt}/{max_attempts} for {fn.__name__} "
                f"after {delay}s (got: {result})"
            )
            time.sleep(delay)
    print(f"All {max_attempts} attempts failed for {fn.__name__}: {last_result}")
    return last_result


def handler(event, context):
    """Main Lambda handler - dispatches to start or stop based on event action."""
    # str() coerces non-string payloads (e.g. {"action": 123}) so .strip()
    # never raises AttributeError on a numeric/bool input.
    action = str(event.get("action") or "").strip()
    print(f"Dev Scheduler invoked with action: {action!r}")
    print(f"Event: {json.dumps(event)}")

    if not action:
        print("No action provided — ignoring invocation")
        return {"statusCode": 400, "body": "No action provided"}

    if action == "start":
        return start_environment()
    elif action == "stop":
        # Distinguish missing/empty (use default) from present-but-invalid
        # (e.g. 0, False) — `or` would silently fall through to the default
        # for any falsy non-string and skip validation.
        stop_mode_raw = event.get("stop_mode")
        if stop_mode_raw in (None, ""):
            stop_mode = os.environ.get("DEFAULT_STOP_MODE", "destroy")
        else:
            stop_mode = str(stop_mode_raw)
        if stop_mode not in ("stop", "destroy"):
            return {"statusCode": 400, "body": f"Unknown stop_mode: {stop_mode}"}
        return stop_environment(stop_mode=stop_mode)
    elif action == "override":
        # Distinguish missing/None (use default) from 0 (clamp to 1) — `or 4`
        # would silently coerce 0 to 4, breaking the documented "min 1 hour".
        hours_raw = event.get("hours")
        if hours_raw is None:
            hours_raw = 4
        try:
            hours = int(hours_raw)
        except (TypeError, ValueError):
            return {"statusCode": 400, "body": "hours must be an integer"}
        hours = min(max(hours, 1), MAX_OVERRIDE_HOURS)
        return set_override(hours)
    else:
        print(f"Unknown action: {action}")
        return {"statusCode": 400, "body": f"Unknown action: {action}"}


def start_environment():
    """Start all dev environment resources.

    Each stage is retried up to MAX_RETRY_ATTEMPTS times on failure.
    A verification EventBridge rule re-invokes this 15 min later to catch
    timeouts or crashes (all operations are idempotent).
    """
    results = {}

    # Detect the +15 min verify re-invocation. Used by the proxy step (to
    # promote `deferred_still_creating` to an error) and the delayed-rules
    # step. Requires BOTH NAT running AND RDS already available — NAT alone
    # is unreliable because an operator may have manually started it
    # outside the scheduler, which would otherwise trip a false positive
    # (premature delayed-rule enable, deferred-proxy promoted to a page).
    nat_id = os.environ["NAT_INSTANCE_ID"]
    is_verify = False
    try:
        nat_resp = ec2.describe_instances(InstanceIds=[nat_id])
        nat_reservations = nat_resp.get("Reservations", [])
        nat_state_before = (
            nat_reservations[0]["Instances"][0]["State"]["Name"]
            if nat_reservations and nat_reservations[0].get("Instances")
            else "not_found"
        )
        if nat_state_before == "running":
            try:
                rds_resp = rds.describe_db_instances(
                    DBInstanceIdentifier=os.environ["RDS_INSTANCE_ID"]
                )
                rds_status_before = rds_resp["DBInstances"][0]["DBInstanceStatus"]
                is_verify = rds_status_before == "available"
                if is_verify:
                    print(
                        "Verify-start invocation detected "
                        "(NAT running, RDS available)"
                    )
                else:
                    print(
                        f"NAT running but RDS is {rds_status_before} — "
                        f"treating as initial start (likely manual NAT start)"
                    )
            except rds.exceptions.DBInstanceNotFoundFault:
                print("NAT running but RDS not found — treating as initial start")
    except Exception as e:
        print(f"Verify-start detection error: {e}")

    # try/finally so clear_override() always runs — a raised exception
    # would otherwise leave the override flag set and skip the next stop.
    try:
        # 1. Start NAT instance first (needed for private subnet internet access)
        results["nat"] = with_retry(start_instance, nat_id, "NAT")

        # 2. Restore RDS from snapshot (takes longest to become available ~5-10 min)
        results["rds"] = with_retry(
            restore_rds,
            os.environ["RDS_INSTANCE_ID"],
            os.environ["RDS_SNAPSHOT_ID"],
            {
                "instance_class": os.environ["RDS_INSTANCE_CLASS"],
                "subnet_group": os.environ["RDS_SUBNET_GROUP_NAME"],
                "security_group_ids": os.environ["RDS_SECURITY_GROUP_IDS"].split(","),
                "parameter_group": os.environ["RDS_PARAMETER_GROUP_NAME"],
            },
        )

        # 2b. Re-register the proxy target. Must run on every start path
        #     (see ensure_rds_proxy_target). On verify-start, a deferred
        #     result means the restore is stuck — promote it to an error.
        if not str(results["rds"]).startswith("error"):
            proxy_result = with_retry(
                ensure_rds_proxy_target, os.environ["RDS_INSTANCE_ID"]
            )
            if is_verify and proxy_result == "deferred_still_creating":
                proxy_result = (
                    "error: RDS still not available at verify-start, "
                    "proxy registration could not complete"
                )
            results["rds_proxy"] = proxy_result

        # 3. Start background instance
        results["background"] = with_retry(
            start_instance, os.environ["BACKGROUND_INSTANCE_ID"], "Background"
        )

        # 4. Start premium instances (Terraform-managed base instances)
        premium_ids = [
            i for i in os.environ.get("PREMIUM_INSTANCE_IDS", "").split(",") if i
        ]
        for pid in premium_ids:
            results[f"premium_{pid}"] = with_retry(start_instance, pid, "Premium")

        # 5. Scale up ASG (launches free tier instance)
        results["asg"] = with_retry(
            scale_asg,
            os.environ["ASG_NAME"],
            min_size=int(os.environ.get("ASG_MIN_SIZE", "1")),
            desired=int(os.environ.get("ASG_DESIRED_CAPACITY", "1")),
            max_size=int(os.environ.get("ASG_MAX_SIZE", "3")),
        )

        # 6. Restore ECS service desired counts (so tasks start scheduling
        #    once container instances register)
        ecs_services = json.loads(os.environ.get("ECS_SERVICE_NAMES", "[]"))
        if ecs_services:
            results.update(
                update_ecs_services(
                    os.environ["CLUSTER_NAME"], ecs_services, desired_count=1
                )
            )

        # 7. Enable Lambda schedule rules (except delayed rules)
        rules = json.loads(os.environ.get("SCHEDULE_RULE_NAMES", "[]"))
        results.update(toggle_event_rules(rules, enable=True))

        # 8. Enable delayed rules (premium_manager etc.) only on verify-start.
        #    Keeps premium_manager from acting on stale DB state while
        #    instances are still booting.
        delayed_rules = json.loads(os.environ.get("DELAYED_RULE_NAMES", "[]"))
        if delayed_rules:
            if is_verify:
                print("Verify invocation — enabling delayed rules")
                results.update(toggle_event_rules(delayed_rules, enable=True))
            else:
                print(
                    "Initial start — delayed rules will be enabled on verify (+15 min)"
                )

        # 9. Enable CloudWatch alarm actions
        results["alarms"] = with_retry(
            toggle_alarm_actions, os.environ.get("ALARM_PREFIX", ""), enable=True
        )
    finally:
        # 10. Clear override (always)
        clear_override()

    print(f"Start results: {json.dumps(results)}")
    errors = {k: v for k, v in results.items() if str(v).startswith("error")}
    if errors:
        print(f"Start completed with {len(errors)} error(s): {json.dumps(errors)}")
        raise RuntimeError(f"Start completed with errors: {json.dumps(errors)}")
    return {"statusCode": 200, "action": "start", "results": results}


def stop_environment(stop_mode="destroy"):
    """Stop all dev environment resources.

    Args:
        stop_mode: "stop" (fast resume, RDS stopped) or "destroy" (max savings,
                   RDS deleted with snapshot). See module docstring for details.

    Each stage is retried up to MAX_RETRY_ATTEMPTS times on failure.
    A verification EventBridge rule re-invokes this 15 min later to catch
    timeouts or crashes (all operations are idempotent).
    """
    print(f"Stopping environment with stop_mode={stop_mode}")
    results = {}

    # Check manual override
    if is_override_active():
        print("Manual override is active - skipping stop")
        return {"statusCode": 200, "action": "stop", "status": "skipped_override"}

    # 1. Clean up dynamic premium instances before disabling rules so
    #    premium_manager can still reach the DB through NAT. Skip if NAT
    #    is already down — cleanup would just time out.
    nat_id = os.environ["NAT_INSTANCE_ID"]
    try:
        nat_resp = ec2.describe_instances(InstanceIds=[nat_id])
        nat_reservations = nat_resp.get("Reservations", [])
        nat_state = (
            nat_reservations[0]["Instances"][0]["State"]["Name"]
            if nat_reservations and nat_reservations[0].get("Instances")
            else "not_found"
        )
    except Exception as e:
        print(f"Failed to check NAT state: {e}")
        nat_state = "unknown"

    if nat_state == "running":
        results["dynamic_premium_cleanup"] = with_retry(
            cleanup_dynamic_premium_instances, max_attempts=1
        )
    else:
        print(
            f"NAT is {nat_state} — skipping dynamic premium cleanup "
            f"(already completed on initial stop)"
        )
        results["dynamic_premium_cleanup"] = "skipped_nat_down"

    # 2. Disable Lambda schedule rules (prevent re-scaling during shutdown)
    rules = json.loads(os.environ.get("SCHEDULE_RULE_NAMES", "[]"))
    delayed_rules = json.loads(os.environ.get("DELAYED_RULE_NAMES", "[]"))
    results.update(toggle_event_rules(rules + delayed_rules, enable=False))

    # 3. Scale down ECS services (prevents failed placement noise when
    #    instances are stopped and no capacity is available)
    ecs_services = json.loads(os.environ.get("ECS_SERVICE_NAMES", "[]"))
    if ecs_services:
        results.update(
            update_ecs_services(
                os.environ["CLUSTER_NAME"], ecs_services, desired_count=0
            )
        )

    # 4. Scale down ASG (terminates free tier instances)
    results["asg"] = with_retry(
        scale_asg, os.environ["ASG_NAME"], min_size=0, desired=0
    )

    # 5. Stop base premium instances (Terraform-managed)
    premium_ids = [
        i for i in os.environ.get("PREMIUM_INSTANCE_IDS", "").split(",") if i
    ]
    for pid in premium_ids:
        results[f"premium_{pid}"] = with_retry(stop_instance, pid, "Premium")

    # 6. Stop background instance
    results["background"] = with_retry(
        stop_instance, os.environ["BACKGROUND_INSTANCE_ID"], "Background"
    )

    # 7. Stop NAT instance
    results["nat"] = with_retry(stop_instance, os.environ["NAT_INSTANCE_ID"], "NAT")

    # 8. RDS: destroy (delete with snapshot) or stop based on mode
    if stop_mode == "destroy":
        results["rds"] = with_retry(
            destroy_rds,
            os.environ["RDS_INSTANCE_ID"],
            os.environ["RDS_SNAPSHOT_ID"],
        )
    else:
        results["rds"] = with_retry(
            stop_rds,
            os.environ["RDS_INSTANCE_ID"],
        )

    # 9. Disable CloudWatch alarm actions
    results["alarms"] = with_retry(
        toggle_alarm_actions, os.environ.get("ALARM_PREFIX", ""), enable=False
    )

    print(f"Stop results: {json.dumps(results)}")
    errors = {k: v for k, v in results.items() if str(v).startswith("error")}
    if errors:
        print(f"Stop completed with {len(errors)} error(s): {json.dumps(errors)}")
        raise RuntimeError(f"Stop completed with errors: {json.dumps(errors)}")
    return {
        "statusCode": 200,
        "action": "stop",
        "stop_mode": stop_mode,
        "results": results,
    }


def destroy_rds(instance_id, snapshot_id):
    """Delete an RDS instance with a final snapshot for later restore.

    Idempotent: returns early if the instance is already deleted.
    Deletes any existing snapshot with the same name first (required by AWS).
    """
    try:
        # Check if instance exists
        try:
            resp = rds.describe_db_instances(DBInstanceIdentifier=instance_id)
            status = resp["DBInstances"][0]["DBInstanceStatus"]
            print(f"RDS {instance_id}: current status = {status}")
        except rds.exceptions.DBInstanceNotFoundFault:
            print(f"RDS {instance_id}: already deleted")
            return "already_deleted"

        # Avoid re-issuing delete_db_instance — AWS rejects it with
        # InvalidDBInstanceState once deletion is in progress.
        if status == "deleting":
            print(f"RDS {instance_id}: already deleting")
            return "already_deleting"

        # Delete old snapshot with same name (AWS requires unique snapshot IDs).
        # Symmetric to the instance `deleting` short-circuit above: if the
        # snapshot is already mid-delete (e.g. waiter timed out on a prior
        # attempt and with_retry re-entered), don't re-issue delete — AWS
        # rejects with InvalidDBSnapshotState. Just wait for it to finish.
        try:
            snap_resp = rds.describe_db_snapshots(DBSnapshotIdentifier=snapshot_id)
            snap_status = snap_resp["DBSnapshots"][0]["Status"]
            if snap_status == "deleting":
                print(f"RDS snapshot {snapshot_id}: already deleting, waiting...")
            else:
                print(f"RDS snapshot {snapshot_id}: deleting old snapshot")
                rds.delete_db_snapshot(DBSnapshotIdentifier=snapshot_id)
            waiter = rds.get_waiter("db_snapshot_deleted")
            waiter.wait(
                DBSnapshotIdentifier=snapshot_id,
                WaiterConfig={"Delay": 10, "MaxAttempts": 15},
            )
            print(f"RDS snapshot {snapshot_id}: old snapshot deleted")
        except rds.exceptions.DBSnapshotNotFoundFault:
            print(f"RDS snapshot {snapshot_id}: no old snapshot to delete")

        # Delete instance with final snapshot
        rds.delete_db_instance(
            DBInstanceIdentifier=instance_id,
            FinalDBSnapshotIdentifier=snapshot_id,
            DeleteAutomatedBackups=False,
        )
        print(f"RDS {instance_id}: deleting (snapshot -> {snapshot_id})")
        return "deleting"
    except Exception as e:
        print(f"RDS {instance_id}: error - {e}")
        return f"error: {e}"


def stop_rds(instance_id):
    """Stop an RDS instance. Fast resume (~2 min) but EBS still billed.

    Idempotent. Returns "not_found" if the instance was destroyed.
    """
    try:
        try:
            resp = rds.describe_db_instances(DBInstanceIdentifier=instance_id)
            status = resp["DBInstances"][0]["DBInstanceStatus"]
            print(f"RDS {instance_id}: current status = {status}")
        except rds.exceptions.DBInstanceNotFoundFault:
            print(f"RDS {instance_id}: not found (may have been destroyed)")
            return "not_found"

        if status in ("stopped", "stopping"):
            print(f"RDS {instance_id}: already {status}")
            return f"already_{status}"

        if status != "available":
            print(f"RDS {instance_id}: cannot stop in status {status}")
            return f"error: cannot stop in status {status}"

        rds.stop_db_instance(DBInstanceIdentifier=instance_id)
        print(f"RDS {instance_id}: stopping")
        return "stopping"
    except Exception as e:
        print(f"RDS {instance_id}: error - {e}")
        return f"error: {e}"


def restore_rds(instance_id, snapshot_id, config):
    """Restore an RDS instance from a snapshot.

    Idempotent: returns early if the instance already exists.
    If the instance exists but is stopped, starts it as a fallback.
    """
    try:
        # Check if instance already exists
        try:
            resp = rds.describe_db_instances(DBInstanceIdentifier=instance_id)
            status = resp["DBInstances"][0]["DBInstanceStatus"]
            print(f"RDS {instance_id}: already exists (status={status})")
            if status == "deleting":
                # Cross-mode race: a prior stop is mid-delete. Let with_retry
                # back off; verify-start will pick it up cleanly.
                return f"error: instance is {status}, cannot restore yet"
            if status == "stopped":
                print(f"RDS {instance_id}: starting stopped instance (fallback)")
                rds.start_db_instance(DBInstanceIdentifier=instance_id)
                return "starting_existing"
            return "already_exists"
        except rds.exceptions.DBInstanceNotFoundFault:
            pass  # Instance doesn't exist, proceed to restore

        # Verify snapshot exists and is available
        try:
            snap_resp = rds.describe_db_snapshots(DBSnapshotIdentifier=snapshot_id)
            snap_status = snap_resp["DBSnapshots"][0]["Status"]
            if snap_status != "available":
                print(f"RDS snapshot {snapshot_id}: status={snap_status}, waiting...")
                waiter = rds.get_waiter("db_snapshot_available")
                waiter.wait(
                    DBSnapshotIdentifier=snapshot_id,
                    WaiterConfig={"Delay": 15, "MaxAttempts": 20},
                )
                print(f"RDS snapshot {snapshot_id}: now available")
        except rds.exceptions.DBSnapshotNotFoundFault:
            print(f"RDS snapshot {snapshot_id}: not found")
            return "error: snapshot_not_found"

        # Restore from snapshot
        rds.restore_db_instance_from_db_snapshot(
            DBInstanceIdentifier=instance_id,
            DBSnapshotIdentifier=snapshot_id,
            DBInstanceClass=config["instance_class"],
            DBSubnetGroupName=config["subnet_group"],
            VpcSecurityGroupIds=config["security_group_ids"],
            DBParameterGroupName=config["parameter_group"],
            StorageType="gp3",
            Port=3306,
            EnableCloudwatchLogsExports=["error", "general", "slowquery"],
            MultiAZ=False,
            PubliclyAccessible=False,
        )
        print(f"RDS {instance_id}: restoring from snapshot {snapshot_id}")
        return "restoring"
    except Exception as e:
        print(f"RDS {instance_id}: error - {e}")
        return f"error: {e}"


def ensure_rds_proxy_target(instance_id):
    """Ensure the RDS instance is registered as a target of the RDS Proxy.

    The proxy auto-deregisters its target when the instance is deleted but
    does NOT auto-register when a new instance with the same identifier
    appears. Must run on every start path.

    Returns:
        "already_registered"      — target already in the proxy target group
        "registered"              — newly registered this call
        "skipped_no_proxy"        — RDS_PROXY_NAME env var not set
        "deferred_still_creating" — instance still transient at deadline;
                                    initial start tolerates this and
                                    verify-start promotes it to an error
        "error: ..."              — instance missing, in a permanent broken
                                    state, or register_db_proxy_targets raised
    """
    proxy_name = os.environ.get("RDS_PROXY_NAME")
    if not proxy_name:
        return "skipped_no_proxy"
    try:
        # Fast path: already in the target group. Walk the Marker pagination
        # so a target on a later page isn't missed (today there's one target
        # per proxy, but the API is paginated and a future migration may add
        # more).
        existing_targets = []
        marker = None
        while True:
            kwargs = {"DBProxyName": proxy_name, "TargetGroupName": "default"}
            if marker:
                kwargs["Marker"] = marker
            page = rds.describe_db_proxy_targets(**kwargs)
            existing_targets.extend(page.get("Targets", []))
            marker = page.get("Marker")
            if not marker:
                break
        if any(t.get("RdsResourceId") == instance_id for t in existing_targets):
            print(f"RDS Proxy {proxy_name}: target {instance_id} already registered")
            return "already_registered"

        # Wait for `available`. RDS rejects register_db_proxy_targets on
        # non-available instances with InvalidDBInstanceState.
        deadline = time.monotonic() + PROXY_REGISTER_POLL_SECONDS
        while True:
            try:
                resp = rds.describe_db_instances(DBInstanceIdentifier=instance_id)
                status = resp["DBInstances"][0]["DBInstanceStatus"]
            except rds.exceptions.DBInstanceNotFoundFault:
                return f"error: instance {instance_id} not found"

            if status == "available":
                break

            if status not in TRANSIENT_RDS_STATUSES:
                # Permanent broken state (failed, incompatible-*, storage-full, …)
                print(
                    f"RDS Proxy {proxy_name}: instance {instance_id} in "
                    f"non-transient state {status!r}, cannot register"
                )
                return f"error: instance {instance_id} in state {status}"

            if time.monotonic() >= deadline:
                # WARNING prefix so a CloudWatch metric filter can catch
                # repeated occurrences across cold-starts.
                print(
                    f"WARNING RDS Proxy {proxy_name}: instance {instance_id} "
                    f"still {status} after {PROXY_REGISTER_POLL_SECONDS}s, "
                    f"deferring registration to verify-start"
                )
                return "deferred_still_creating"

            print(
                f"RDS Proxy {proxy_name}: instance {instance_id} is {status}, "
                f"polling..."
            )
            time.sleep(PROXY_REGISTER_POLL_INTERVAL)

        rds.register_db_proxy_targets(
            DBProxyName=proxy_name,
            DBInstanceIdentifiers=[instance_id],
        )
        print(f"RDS Proxy {proxy_name}: registered target {instance_id}")
        return "registered"
    except Exception as e:
        print(f"RDS Proxy {proxy_name}: registration error - {e}")
        return f"error: {e}"


def start_instance(instance_id, label):
    """Start an EC2 instance. Safe to call if already running.

    Handles terminated/missing instances gracefully — logs a warning instead of
    retrying a dead instance indefinitely.
    """
    try:
        response = ec2.describe_instances(InstanceIds=[instance_id])
        reservations = response.get("Reservations", [])
        if not reservations or not reservations[0].get("Instances"):
            print(
                f"{label} instance {instance_id}: NOT FOUND — "
                f"likely terminated/replaced. Update PREMIUM_INSTANCE_IDS or "
                f"run terraform apply to recreate."
            )
            return "not_found"
        state = reservations[0]["Instances"][0]["State"]["Name"]
        if state == "running":
            print(f"{label} instance {instance_id}: already running")
            return "already_running"
        if state == "terminated":
            print(
                f"{label} instance {instance_id}: TERMINATED — "
                f"cannot start. Run terraform apply to recreate."
            )
            return f"error: instance {instance_id} is terminated"

        ec2.start_instances(InstanceIds=[instance_id])
        print(f"{label} instance {instance_id}: starting (was {state})")
        return "starting"
    except Exception as e:
        print(f"{label} instance {instance_id}: error - {e}")
        return f"error: {e}"


def stop_instance(instance_id, label):
    """Stop an EC2 instance. Safe to call if already stopped or missing."""
    try:
        response = ec2.describe_instances(InstanceIds=[instance_id])
        reservations = response.get("Reservations", [])
        if not reservations or not reservations[0].get("Instances"):
            print(f"{label} instance {instance_id}: not found — skipping")
            return "not_found"
        state = reservations[0]["Instances"][0]["State"]["Name"]
        if state in ("stopped", "stopping"):
            print(f"{label} instance {instance_id}: already {state}")
            return f"already_{state}"
        if state == "terminated":
            print(f"{label} instance {instance_id}: already terminated")
            return "already_terminated"

        ec2.stop_instances(InstanceIds=[instance_id])
        print(f"{label} instance {instance_id}: stopping (was {state})")
        return "stopping"
    except Exception as e:
        print(f"{label} instance {instance_id}: error - {e}")
        return f"error: {e}"


def scale_asg(asg_name, min_size, desired, max_size=None):
    """Update ASG min size, desired capacity, and optionally max size."""
    try:
        params = {
            "AutoScalingGroupName": asg_name,
            "MinSize": min_size,
            "DesiredCapacity": desired,
        }
        if max_size is not None:
            params["MaxSize"] = max_size

        autoscaling.update_auto_scaling_group(**params)
        msg = f"min={min_size},desired={desired}"
        if max_size is not None:
            msg += f",max={max_size}"
        print(f"ASG {asg_name}: set {msg}")
        return msg
    except Exception as e:
        print(f"ASG {asg_name}: error - {e}")
        return f"error: {e}"


def update_ecs_services(cluster_name, service_names, desired_count):
    """Update desired count for ECS services.

    Setting desired_count=0 on stop prevents failed placement attempts when
    no container instances are available. Setting desired_count=1 on start
    resumes task scheduling.
    """
    results = {}
    for service_name in service_names:
        try:
            ecs.update_service(
                cluster=cluster_name,
                service=service_name,
                desiredCount=desired_count,
            )
            print(f"ECS service {service_name}: set desired_count={desired_count}")
            results[f"ecs_{service_name}"] = f"desired_count={desired_count}"
        except Exception as e:
            print(f"ECS service {service_name}: error - {e}")
            results[f"ecs_{service_name}"] = f"error: {e}"
    return results


def cleanup_dynamic_premium_instances():
    """Invoke premium_manager Lambda to terminate dynamic premium instances.

    This runs before disabling Lambda rules so premium_manager can still
    reach the database through NAT.
    """
    function_name = os.environ.get("PREMIUM_MANAGER_FUNCTION_NAME", "")
    if not function_name:
        print("PREMIUM_MANAGER_FUNCTION_NAME not set, skipping dynamic cleanup")
        return "skipped"

    premium_ids = [
        i for i in os.environ.get("PREMIUM_INSTANCE_IDS", "").split(",") if i
    ]

    try:
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(
                {
                    "action": "cleanup_all_dynamic",
                    "base_instance_ids": premium_ids,
                }
            ),
        )
        payload = json.loads(response["Payload"].read())
        print(f"Dynamic premium cleanup result: {json.dumps(payload)}")

        if response.get("FunctionError"):
            return f"error: {payload}"
        return payload
    except Exception as e:
        print(f"Error invoking premium_manager cleanup: {e}")
        return f"error: {e}"


def toggle_event_rules(rules, enable):
    """Enable or disable a list of EventBridge rules."""
    results = {}
    action_name = "enable" if enable else "disable"
    action_func = events.enable_rule if enable else events.disable_rule
    for rule in rules:
        try:
            action_func(Name=rule)
            results[f"{action_name}_{rule}"] = "ok"
            print(f"{action_name.title()}d rule: {rule}")
        except Exception as e:
            results[f"{action_name}_{rule}"] = f"error: {e}"
            print(f"Failed to {action_name} rule {rule}: {e}")
    return results


def toggle_alarm_actions(prefix, enable):
    """Enable or disable alarm actions for all alarms matching prefix."""
    if not prefix:
        return "no_prefix"
    try:
        paginator = cloudwatch.get_paginator("describe_alarms")
        alarm_names = []
        for page in paginator.paginate(AlarmNamePrefix=prefix):
            alarm_names.extend([a["AlarmName"] for a in page.get("MetricAlarms", [])])

        if not alarm_names:
            print(f"No alarms found with prefix: {prefix}")
            return "no_alarms"

        for i in range(0, len(alarm_names), 100):
            batch = alarm_names[i : i + 100]
            if enable:
                cloudwatch.enable_alarm_actions(AlarmNames=batch)
            else:
                cloudwatch.disable_alarm_actions(AlarmNames=batch)

        action = "enabled" if enable else "disabled"
        print(f"{action} actions for {len(alarm_names)} alarms")
        return f"{action}_{len(alarm_names)}_alarms"
    except Exception as e:
        print(f"Alarm toggle error: {e}")
        return f"error: {e}"


def set_override(hours):
    """Set the override with a TTL. Automatically expires after the specified hours."""
    try:
        param_name = os.environ.get("OVERRIDE_PARAM_NAME", "")
        if not param_name:
            return {"statusCode": 400, "body": "OVERRIDE_PARAM_NAME not configured"}

        expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)
        expires_str = expires_at.strftime("%Y-%m-%dT%H:%M:%SZ")

        ssm.put_parameter(
            Name=param_name, Value=expires_str, Type="String", Overwrite=True
        )
        print(f"Override set for {hours}h, expires at {expires_str}")
        return {
            "statusCode": 200,
            "action": "override",
            "hours": hours,
            "expires_at": expires_str,
        }
    except Exception as e:
        print(f"Set override error: {e}")
        return {"statusCode": 500, "body": str(e)}


def is_override_active():
    """Check if the override is active.

    Value is a UTC timestamp that hasn't expired yet.
    """
    try:
        param_name = os.environ.get("OVERRIDE_PARAM_NAME", "")
        if not param_name:
            return False
        response = ssm.get_parameter(Name=param_name)
        value = response["Parameter"]["Value"].strip()

        if value.lower() in ("off", ""):
            return False

        # Parse expiry timestamp
        try:
            expires_at = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
            now = datetime.now(timezone.utc)
            if now < expires_at:
                remaining = expires_at - now
                print(f"Override active, expires in {remaining}")
                return True
            else:
                print(f"Override expired at {value}, clearing")
                clear_override()
                return False
        except ValueError:
            # Legacy format: treat "on" as active with safety cap
            if value.lower() == "on":
                print(
                    f"Legacy override format 'on' detected. "
                    f"Converting to {MAX_OVERRIDE_HOURS}h TTL."
                )
                set_override(MAX_OVERRIDE_HOURS)
                return True
            print(f"Unknown override value: {value}, ignoring")
            return False

    except ssm.exceptions.ParameterNotFound:
        return False
    except Exception as e:
        print(f"Override check error: {e}")
        return False


def clear_override():
    """Reset the override parameter to 'off'."""
    try:
        param_name = os.environ.get("OVERRIDE_PARAM_NAME", "")
        if param_name:
            ssm.put_parameter(
                Name=param_name, Value="off", Type="String", Overwrite=True
            )
            print("Override cleared")
    except Exception as e:
        print(f"Clear override error: {e}")
