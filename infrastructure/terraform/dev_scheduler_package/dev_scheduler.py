"""
Dev Environment Scheduler Lambda

Starts and stops development environment resources on a schedule to save costs.

Schedule (JST):
- Start: Mon-Fri 08:45 (resources ready by ~09:00)
- Stop:  Mon-Fri 22:00
- Weekends: Stopped (Fri 22:00 -> Mon 08:45)

Manual override (skip next stop):
    aws ssm put-parameter --name /<env>/optinist/schedule-override \
        --value on --type String --overwrite

Manual start (after-hours):
    aws lambda invoke --function-name <env>-dev-scheduler \
        --payload '{"action":"start"}' /dev/stdout

Resources managed:
- RDS instance (destroy with snapshot / restore from snapshot)
- NAT instance (stop/start)
- Background service EC2 instance (stop/start)
- Premium EC2 instances (stop/start)
- Free tier ASG (scale 0/1)
- Lambda schedule rules (disable/enable)
- CloudWatch alarm actions (disable/enable)

RDS destroy/restore approach:
  Instead of stop/start (which suffers from AWS's 7-day auto-restart),
  the scheduler deletes the RDS instance with a final snapshot on stop,
  and restores from that snapshot on start. This prevents unattended
  restarts during extended breaks (Golden Week, year-end holidays, etc.).
  The RDS Proxy target auto-reconnects when the instance is restored
  with the same identifier.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import boto3

# Maximum override duration (safety cap even if user requests more)
MAX_OVERRIDE_HOURS = 12

rds = boto3.client("rds")
ec2 = boto3.client("ec2")
lambda_client = boto3.client("lambda")
autoscaling = boto3.client("autoscaling")
events = boto3.client("events")
cloudwatch = boto3.client("cloudwatch")
ssm = boto3.client("ssm")


def handler(event, context):
    """Main Lambda handler - dispatches to start or stop based on event action."""
    action = event.get("action", "")
    print(f"Dev Scheduler invoked with action: {action}")
    print(f"Event: {json.dumps(event)}")

    if action == "start":
        return start_environment()
    elif action == "stop":
        return stop_environment()
    elif action == "override":
        hours = min(event.get("hours", 4), MAX_OVERRIDE_HOURS)
        return set_override(hours)
    else:
        print(f"Unknown action: {action}")
        return {"statusCode": 400, "body": f"Unknown action: {action}"}


def start_environment():
    """Start all dev environment resources."""
    results = {}

    # 1. Start NAT instance first (needed for private subnet internet access)
    results["nat"] = start_instance(os.environ["NAT_INSTANCE_ID"], "NAT")

    # 2. Restore RDS from snapshot (takes longest to become available ~5-10 min)
    results["rds"] = restore_rds(
        os.environ["RDS_INSTANCE_ID"],
        os.environ["RDS_SNAPSHOT_ID"],
        {
            "instance_class": os.environ["RDS_INSTANCE_CLASS"],
            "subnet_group": os.environ["RDS_SUBNET_GROUP_NAME"],
            "security_group_ids": os.environ["RDS_SECURITY_GROUP_IDS"].split(","),
            "parameter_group": os.environ["RDS_PARAMETER_GROUP_NAME"],
        },
    )

    # 3. Start background instance
    results["background"] = start_instance(
        os.environ["BACKGROUND_INSTANCE_ID"], "Background"
    )

    # 4. Scale up ASG (launches free tier instance)
    results["asg"] = scale_asg(
        os.environ["ASG_NAME"],
        min_size=int(os.environ.get("ASG_MIN_SIZE", "1")),
        desired=int(os.environ.get("ASG_DESIRED_CAPACITY", "1")),
        max_size=int(os.environ.get("ASG_MAX_SIZE", "3")),
    )

    # 5. Enable Lambda schedule rules
    rules = json.loads(os.environ.get("SCHEDULE_RULE_NAMES", "[]"))
    results.update(toggle_event_rules(rules, enable=True))

    # 6. Enable CloudWatch alarm actions
    results["alarms"] = toggle_alarm_actions(
        os.environ.get("ALARM_PREFIX", ""), enable=True
    )

    # 7. Clear override
    clear_override()

    print(f"Start results: {json.dumps(results)}")
    errors = {k: v for k, v in results.items() if str(v).startswith("error")}
    if errors:
        print(f"Start completed with {len(errors)} error(s): {json.dumps(errors)}")
        raise RuntimeError(f"Start completed with errors: {json.dumps(errors)}")
    return {"statusCode": 200, "action": "start", "results": results}


def stop_environment():
    """Stop all dev environment resources."""
    results = {}

    # Check manual override
    if is_override_active():
        print("Manual override is active - skipping stop")
        return {"statusCode": 200, "action": "stop", "status": "skipped_override"}

    # 1. Clean up dynamic premium instances (before disabling rules so
    #    premium_manager can still reach the DB through NAT)
    results["dynamic_premium_cleanup"] = cleanup_dynamic_premium_instances()

    # 2. Disable Lambda schedule rules (prevent re-scaling during shutdown)
    rules = json.loads(os.environ.get("SCHEDULE_RULE_NAMES", "[]"))
    results.update(toggle_event_rules(rules, enable=False))

    # 3. Scale down ASG (terminates free tier instances)
    results["asg"] = scale_asg(os.environ["ASG_NAME"], min_size=0, desired=0)

    # 4. Stop base premium instances (Terraform-managed)
    premium_ids = [
        i for i in os.environ.get("PREMIUM_INSTANCE_IDS", "").split(",") if i
    ]
    for pid in premium_ids:
        results[f"premium_{pid}"] = stop_instance(pid, "Premium")

    # 5. Stop background instance
    results["background"] = stop_instance(
        os.environ["BACKGROUND_INSTANCE_ID"], "Background"
    )

    # 6. Stop NAT instance
    results["nat"] = stop_instance(os.environ["NAT_INSTANCE_ID"], "NAT")

    # 7. Destroy RDS (with final snapshot for later restore)
    results["rds"] = destroy_rds(
        os.environ["RDS_INSTANCE_ID"],
        os.environ["RDS_SNAPSHOT_ID"],
    )

    # 8. Disable CloudWatch alarm actions
    results["alarms"] = toggle_alarm_actions(
        os.environ.get("ALARM_PREFIX", ""), enable=False
    )

    print(f"Stop results: {json.dumps(results)}")
    errors = {k: v for k, v in results.items() if str(v).startswith("error")}
    if errors:
        print(f"Stop completed with {len(errors)} error(s): {json.dumps(errors)}")
        raise RuntimeError(f"Stop completed with errors: {json.dumps(errors)}")
    return {"statusCode": 200, "action": "stop", "results": results}


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

        # Delete old snapshot with same name (AWS requires unique snapshot IDs)
        try:
            rds.describe_db_snapshots(DBSnapshotIdentifier=snapshot_id)
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
                print(
                    f"RDS snapshot {snapshot_id}: status={snap_status}, waiting..."
                )
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


def start_instance(instance_id, label):
    """Start an EC2 instance. Safe to call if already running."""
    try:
        response = ec2.describe_instances(InstanceIds=[instance_id])
        state = response["Reservations"][0]["Instances"][0]["State"]["Name"]
        if state == "running":
            print(f"{label} instance {instance_id}: already running")
            return "already_running"

        ec2.start_instances(InstanceIds=[instance_id])
        print(f"{label} instance {instance_id}: starting (was {state})")
        return "starting"
    except Exception as e:
        print(f"{label} instance {instance_id}: error - {e}")
        return f"error: {e}"


def stop_instance(instance_id, label):
    """Stop an EC2 instance. Safe to call if already stopped."""
    try:
        response = ec2.describe_instances(InstanceIds=[instance_id])
        state = response["Reservations"][0]["Instances"][0]["State"]["Name"]
        if state in ("stopped", "stopping"):
            print(f"{label} instance {instance_id}: already {state}")
            return f"already_{state}"

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
            results[f"{action_name}_{rule}"] = str(e)
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
