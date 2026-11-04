#!/usr/bin/env python3
"""
Put the deployed lifecycle e2e fixtures back over the free quota.

TEST_GRACE_OVER and TEST_FREE_DOWNGRADE are expired premium accounts kept above
the free limit on purpose, so every expiration pass trims them. This copies
their S3 data back from the pristine fixture copy, then resets what a pass
changes in the database: usage, the job's stamps, and the data flags it
cleared, set again wherever the restored objects back them. A pass only ever
clears flags, so none is cleared here.

Development only. Run before a deployed 11-lifecycle round:

    TEST_GRACE_OVER_EMAIL=... TEST_FREE_DOWNGRADE_EMAIL=... \\
        python infrastructure/scripts/restore_e2e_fixture_data.py
"""

import os
import subprocess
import sys

import boto3
from botocore.exceptions import WaiterError

REGION = "ap-northeast-1"
FIXTURE_BUCKET = "development-optinist-e2e-fixtures"
FIXTURES = {
    "TEST_GRACE_OVER_EMAIL": "grace-over",
    "TEST_FREE_DOWNGRADE_EMAIL": "free-downgrade",
}
SSM_INSTANCE_NAME = "development-optinist-background"
RDS_SECRET_ID = "development-optinist/database/config"
RDS_PROXY_HOST = (
    "development-optinist-rds-proxy.proxy-cdmeogcuo1v2.ap-northeast-1.rds.amazonaws.com"
)
OUTPUT_PREFIX = "app/studio_data/output/"
INPUT_PREFIX = "app/studio_data/input/"


def ssm_instance_id(ec2):
    reservations = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [SSM_INSTANCE_NAME]},
            {"Name": "instance-state-name", "Values": ["running"]},
        ]
    )["Reservations"]
    if not reservations:
        sys.exit(f"no running {SSM_INSTANCE_NAME} instance; is dev up?")
    return reservations[0]["Instances"][0]["InstanceId"]


def run_sql(ssm, instance_id, sql):
    """Run SQL on the dev RDS through the proxy, from an in-VPC instance."""
    read = "python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])'"
    commands = [
        "set -e",
        f"CFG=$(aws secretsmanager get-secret-value --region {REGION} "
        f"--secret-id {RDS_SECRET_ID} --query SecretString --output text)",
        f"export MYSQL_PWD=$(printf '%s' \"$CFG\" | {read} password)",
        f"DBU=$(printf '%s' \"$CFG\" | {read} username)",
        f"DBN=$(printf '%s' \"$CFG\" | {read} database)",
        f'mariadb --ssl -h {RDS_PROXY_HOST} -u "$DBU" --connect-timeout=10 '
        f"\"$DBN\" -N <<'SQL'\n{sql}\nSQL",
    ]
    command_id = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": commands},
    )["Command"]["CommandId"]
    try:
        ssm.get_waiter("command_executed").wait(
            CommandId=command_id, InstanceId=instance_id
        )
    except WaiterError:
        pass  # the invocation says why
    result = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
    if result["Status"] != "Success":
        sys.exit(f"SQL failed ({result['Status']}): {result['StandardErrorContent']}")
    return result["StandardOutputContent"].strip()


def record_flags(keys, workspace_id, uid):
    """The data flags a record's restored objects imply, as the job classifies them."""
    prefix = f"{OUTPUT_PREFIX}{workspace_id}/{uid}/"
    rest = [k[len(prefix) :] for k in keys if k.startswith(prefix) and k != prefix]
    return {
        "has_intermediates": any("/" in r for r in rest),
        "has_outputs": any(
            "/" not in r and not r.lower().endswith((".yaml", ".yml")) for r in rest
        ),
        "has_nwb": any(r.lower().endswith(".nwb") for r in rest),
        "has_inputs": any(k.startswith(f"{INPUT_PREFIX}{workspace_id}/") for k in keys),
    }


def restore(s3, ssm, instance_id, email, source):
    quoted = email.replace("'", "''")
    user_id = run_sql(
        ssm, instance_id, f"SELECT id FROM users WHERE email = '{quoted}'"
    )
    if not user_id.isdigit():
        sys.exit(f"expected one user for {email}, got {user_id!r}")
    buckets = [
        b["Name"]
        for b in s3.list_buckets()["Buckets"]
        if b["Name"].startswith(f"development-optinist-user-{user_id}-")
    ]
    if len(buckets) != 1:
        sys.exit(f"expected one bucket for user {user_id}, got {buckets}")
    bucket = buckets[0]

    subprocess.run(
        ["aws", "s3", "sync", f"s3://{FIXTURE_BUCKET}/{source}/", f"s3://{bucket}/"]
        + ["--only-show-errors", "--region", REGION],
        check=True,
    )
    sizes = {
        obj["Key"]: obj["Size"]
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket)
        for obj in page.get("Contents", [])
    }
    total = sum(sizes.values())

    records = run_sql(
        ssm,
        instance_id,
        "SELECT r.id, r.workspace_id, r.uid FROM experiment_records r "
        f"JOIN workspaces w ON w.id = r.workspace_id WHERE w.user_id = {user_id}",
    )
    statements = [
        f"UPDATE user_storage_usage SET storage_usage_bytes = {total}, "
        f"last_updated = UTC_TIMESTAMP() WHERE user_id = {user_id};",
        "UPDATE subscription_users SET deletion_processed_at = NULL, "
        "grace_start_notified_at = NULL, grace_reminder_notified_at = NULL, "
        f"reprocess_notified_at = NULL WHERE user_id = {user_id};",
    ]
    for line in records.splitlines():
        record_id, workspace_id, uid = line.split("\t")
        flags = record_flags(sizes, workspace_id, uid)
        assignments = ", ".join(f"{k} = 1" for k, backed in flags.items() if backed)
        if assignments:
            statements.append(
                f"UPDATE experiment_records SET {assignments} WHERE id = {record_id};"
            )
    run_sql(ssm, instance_id, "\n".join(statements))
    print(
        f"{email}: user {user_id}, {bucket}, {total / 2**30:.2f} GiB, "
        f"{len(records.splitlines())} records"
    )


def main():
    missing = [name for name in FIXTURES if not os.environ.get(name)]
    if missing:
        sys.exit(f"set {', '.join(missing)}")
    s3 = boto3.client("s3", region_name=REGION)
    ssm = boto3.client("ssm", region_name=REGION)
    instance_id = ssm_instance_id(boto3.client("ec2", region_name=REGION))
    for name, source in FIXTURES.items():
        restore(s3, ssm, instance_id, os.environ[name], source)


if __name__ == "__main__":
    main()
