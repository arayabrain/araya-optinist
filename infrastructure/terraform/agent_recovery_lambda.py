"""ASG <-> ECS container-instance reconciliation.

For each configured ECS cluster, list every ASG-managed EC2 instance and
verify it is registered as a container instance with `agentConnected=true`.
Counts instances that are running in the ASG but missing from (or
disconnected in) the ECS control plane and emits the count as a metric.

Alarm-only: this Lambda never calls set-instance-health or terminate. It
exists to page humans when the on-host watchdog is silent.
"""

import os

import boto3

ecs = boto3.client("ecs")
asg = boto3.client("autoscaling")
cw = boto3.client("cloudwatch")

CLUSTERS = [c.strip() for c in os.environ["CLUSTERS"].split(",") if c.strip()]
ASG_NAMES = [a.strip() for a in os.environ["ASG_NAMES"].split(",") if a.strip()]
METRIC_NAMESPACE = os.environ.get("METRIC_NAMESPACE", "OptiNiSt/AgentRecovery")
METRIC_NAME = os.environ.get("METRIC_NAME", "EcsAsgInstanceUnregisteredCount")


def _registered_ec2_ids(cluster: str) -> set:
    """Return the set of EC2 instance IDs currently registered (and
    `agentConnected=true`) as container instances in the cluster."""
    registered = set()
    paginator = ecs.get_paginator("list_container_instances")
    for page in paginator.paginate(cluster=cluster, status="ACTIVE"):
        arns = page.get("containerInstanceArns", [])
        for i in range(0, len(arns), 100):
            chunk = arns[i : i + 100]
            if not chunk:
                continue
            desc = ecs.describe_container_instances(
                cluster=cluster, containerInstances=chunk
            )
            for ci in desc.get("containerInstances", []):
                if ci.get("agentConnected"):
                    registered.add(ci["ec2InstanceId"])
    return registered


def _asg_instance_ids(asg_name: str) -> set:
    desc = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
    out = set()
    for group in desc.get("AutoScalingGroups", []):
        for inst in group.get("Instances", []):
            if inst.get("LifecycleState") == "InService":
                out.add(inst["InstanceId"])
    return out


def handler(event, context):
    unregistered_total = 0
    details = []
    for cluster in CLUSTERS:
        try:
            registered = _registered_ec2_ids(cluster)
        except ecs.exceptions.ClusterNotFoundException:
            print(f"cluster {cluster} not found, skipping")
            continue
        for asg_name in ASG_NAMES:
            try:
                asg_ids = _asg_instance_ids(asg_name)
            except asg.exceptions.ClientError as exc:
                print(f"asg {asg_name} describe failed: {exc}")
                continue
            missing = asg_ids - registered
            if missing:
                details.append(
                    f"cluster={cluster} asg={asg_name} missing={sorted(missing)}"
                )
                unregistered_total += len(missing)

    print(f"unregistered_total={unregistered_total} details={details}")
    cw.put_metric_data(
        Namespace=METRIC_NAMESPACE,
        MetricData=[
            {
                "MetricName": METRIC_NAME,
                "Value": unregistered_total,
                "Unit": "Count",
            }
        ],
    )
    return {"unregistered": unregistered_total, "details": details}
