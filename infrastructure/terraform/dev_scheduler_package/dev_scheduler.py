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
- RDS instance (stop/start)
- NAT instance (stop/start)
- Background service EC2 instance (stop/start)
- Premium EC2 instances (stop/start)
- Free tier ASG (scale 0/1)
- Lambda schedule rules (disable/enable)
- CloudWatch alarm actions (disable/enable)
"""

import json
import os

import boto3

rds = boto3.client("rds")
ec2 = boto3.client("ec2")
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
    else:
        print(f"Unknown action: {action}")
        return {"statusCode": 400, "body": f"Unknown action: {action}"}


def start_environment():
    """Start all dev environment resources."""
    results = {}

    # 1. Start NAT instance first (needed for private subnet internet access)
    results["nat"] = start_instance(os.environ["NAT_INSTANCE_ID"], "NAT")

    # 2. Start RDS (takes longest to become available ~5-10 min)
    results["rds"] = start_rds(os.environ["RDS_INSTANCE_ID"])

    # 3. Start background instance
    results["background"] = start_instance(
        os.environ["BACKGROUND_INSTANCE_ID"], "Background"
    )

    # 4. Scale up ASG (launches free tier instance)
    results["asg"] = scale_asg(
        os.environ["ASG_NAME"],
        min_size=int(os.environ.get("ASG_MIN_SIZE", "1")),
        desired=int(os.environ.get("ASG_DESIRED_CAPACITY", "1")),
    )

    # 5. Enable Lambda schedule rules
    rules = json.loads(os.environ.get("SCHEDULE_RULE_NAMES", "[]"))
    for rule in rules:
        try:
            events.enable_rule(Name=rule)
            results[f"enable_{rule}"] = "ok"
            print(f"Enabled rule: {rule}")
        except Exception as e:
            results[f"enable_{rule}"] = str(e)
            print(f"Failed to enable rule {rule}: {e}")

    # 6. Enable CloudWatch alarm actions
    results["alarms"] = toggle_alarm_actions(
        os.environ.get("ALARM_PREFIX", ""), enable=True
    )

    # 7. Clear override
    clear_override()

    print(f"Start results: {json.dumps(results)}")
    return {"statusCode": 200, "action": "start", "results": results}


def stop_environment():
    """Stop all dev environment resources."""
    results = {}

    # Check manual override
    if is_override_active():
        print("Manual override is active - skipping stop")
        return {"statusCode": 200, "action": "stop", "status": "skipped_override"}

    # 1. Disable Lambda schedule rules first (prevent re-scaling during shutdown)
    rules = json.loads(os.environ.get("SCHEDULE_RULE_NAMES", "[]"))
    for rule in rules:
        try:
            events.disable_rule(Name=rule)
            results[f"disable_{rule}"] = "ok"
            print(f"Disabled rule: {rule}")
        except Exception as e:
            results[f"disable_{rule}"] = str(e)
            print(f"Failed to disable rule {rule}: {e}")

    # 2. Scale down ASG (terminates free tier instances)
    results["asg"] = scale_asg(os.environ["ASG_NAME"], min_size=0, desired=0)

    # 3. Stop premium instances
    premium_ids = [
        i for i in os.environ.get("PREMIUM_INSTANCE_IDS", "").split(",") if i
    ]
    for pid in premium_ids:
        results[f"premium_{pid}"] = stop_instance(pid, "Premium")

    # 4. Stop background instance
    results["background"] = stop_instance(
        os.environ["BACKGROUND_INSTANCE_ID"], "Background"
    )

    # 5. Stop NAT instance
    results["nat"] = stop_instance(os.environ["NAT_INSTANCE_ID"], "NAT")

    # 6. Stop RDS
    results["rds"] = stop_rds(os.environ["RDS_INSTANCE_ID"])

    # 7. Disable CloudWatch alarm actions
    results["alarms"] = toggle_alarm_actions(
        os.environ.get("ALARM_PREFIX", ""), enable=False
    )

    print(f"Stop results: {json.dumps(results)}")
    return {"statusCode": 200, "action": "stop", "results": results}


def start_rds(instance_id):
    """Start an RDS instance. Safe to call if already running."""
    try:
        rds.start_db_instance(DBInstanceIdentifier=instance_id)
        print(f"RDS {instance_id}: starting")
        return "starting"
    except rds.exceptions.InvalidDBInstanceStateFault:
        print(f"RDS {instance_id}: already available or transitioning")
        return "already_running"
    except Exception as e:
        print(f"RDS {instance_id}: error - {e}")
        return f"error: {e}"


def stop_rds(instance_id):
    """Stop an RDS instance. Safe to call if already stopped."""
    try:
        rds.stop_db_instance(DBInstanceIdentifier=instance_id)
        print(f"RDS {instance_id}: stopping")
        return "stopping"
    except rds.exceptions.InvalidDBInstanceStateFault:
        print(f"RDS {instance_id}: already stopped or transitioning")
        return "already_stopped"
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


def scale_asg(asg_name, min_size, desired):
    """Update ASG min size and desired capacity."""
    try:
        autoscaling.update_auto_scaling_group(
            AutoScalingGroupName=asg_name,
            MinSize=min_size,
            DesiredCapacity=desired,
        )
        print(f"ASG {asg_name}: set min={min_size}, desired={desired}")
        return f"min={min_size},desired={desired}"
    except Exception as e:
        print(f"ASG {asg_name}: error - {e}")
        return f"error: {e}"


def toggle_alarm_actions(prefix, enable):
    """Enable or disable alarm actions for all alarms matching prefix."""
    if not prefix:
        return "no_prefix"
    try:
        paginator = cloudwatch.get_paginator("describe_alarms")
        alarm_names = []
        for page in paginator.paginate(AlarmNamePrefix=prefix):
            alarm_names.extend(
                [a["AlarmName"] for a in page.get("MetricAlarms", [])]
            )

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


def is_override_active():
    """Check if the manual override SSM parameter is set to 'on'."""
    try:
        param_name = os.environ.get("OVERRIDE_PARAM_NAME", "")
        if not param_name:
            return False
        response = ssm.get_parameter(Name=param_name)
        return response["Parameter"]["Value"].lower() == "on"
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
