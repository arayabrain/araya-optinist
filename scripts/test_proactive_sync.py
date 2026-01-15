#!/usr/bin/env python3
"""
Manual testing script for proactive experiment sync feature.

Tests that experiment metadata syncs correctly when users are migrated between
instances, preventing 404 errors when accessing experiments after migration.

Prerequisites:
    - AWS credentials configured (aws configure)
    - boto3, pymysql, requests packages installed
    - Terraform initialized in infrastructure/terraform directory

Usage:
    # Option 1: Auto-fetch from Terraform/AWS (recommended)
    cd infrastructure/terraform
    python ../../scripts/test_proactive_sync.py --from-terraform status <user_id>

    # Option 2: Set environment variables manually
    export DB_HOST=your-rds-host
    export DB_USER=your-db-user
    export DB_PASSWORD=your-db-password
    export DB_NAME=optinist
    export ALB_DNS_NAME=your-alb-dns.amazonaws.com
    export INTERNAL_API_SECRET=your-secret-from-secrets-manager

Commands:
    find-user <email>     Find user by email (partial match)
    status <user_id>      Get user's current instance assignment
    list-instances        List all instances with user counts
    migrate <user_id>     Migrate user to different instance (auto-scales if needed)
    sync <user_id>        Trigger experiment sync for user

Example workflow:
    cd infrastructure/terraform
    python ../../scripts/test_proactive_sync.py --from-terraform find-user test@mail.com
    python ../../scripts/test_proactive_sync.py --from-terraform migrate 42

    The migrate command will:
    - Scale ASG to 2 instances if only 1 exists (~3-5 min)
    - Select a different instance as target
    - Update database assignment
    - Trigger experiment sync on new instance

Verification:
    After migration, check CloudWatch Logs for:
    - "Initiating experiment sync for user X"
    - "Experiment sync completed for user X"
"""

import argparse
import json
import os
import subprocess
import sys


def load_from_terraform():
    """Load configuration from Terraform outputs and AWS Secrets Manager."""
    print("Loading configuration from Terraform outputs...")

    # Get Terraform outputs
    try:
        result = subprocess.run(
            ["terraform", "output", "-json"],
            capture_output=True,
            text=True,
            check=True,
        )
        outputs = json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Error running terraform output: {e.stderr}")
        sys.exit(1)
    except FileNotFoundError:
        print("Error: terraform command not found. Make sure Terraform is installed.")
        sys.exit(1)

    # Extract values from outputs
    alb_dns = outputs.get("alb_dns_name", {}).get("value")
    rds_endpoint = outputs.get("rds_endpoint", {}).get("value", "").split(":")[0]

    if not alb_dns or not rds_endpoint:
        print("Error: Could not get alb_dns_name or rds_endpoint from Terraform")
        sys.exit(1)

    os.environ["ALB_DNS_NAME"] = alb_dns
    os.environ["DB_HOST"] = rds_endpoint

    # Get secrets from AWS Secrets Manager
    print("Fetching secrets from AWS Secrets Manager...")
    try:
        import boto3

        secrets_client = boto3.client("secretsmanager")

        # Get database credentials
        db_secret = secrets_client.get_secret_value(
            SecretId="subscr-optinist/database/config"
        )
        db_config = json.loads(db_secret["SecretString"])
        os.environ["DB_USER"] = db_config.get("username", "optinist")
        os.environ["DB_PASSWORD"] = db_config["password"]
        os.environ["DB_NAME"] = db_config.get("database", "optinist")

        # Get internal API secret
        api_secret = secrets_client.get_secret_value(
            SecretId="subscr-internal-api-secret"
        )
        api_config = json.loads(api_secret["SecretString"])
        os.environ["INTERNAL_API_SECRET"] = api_config["key"]

        print(f"  ALB DNS: {alb_dns}")
        print(f"  DB Host: {rds_endpoint}")
        print(f"  DB User: {os.environ['DB_USER']}")
        print("  Secrets loaded successfully")

    except Exception as e:
        print(f"Error fetching secrets from AWS: {e}")
        print("Make sure you have AWS credentials configured (aws configure)")
        sys.exit(1)


def get_db_connection():
    """Get database connection using environment variables."""
    import pymysql

    return pymysql.connect(
        host=os.environ["DB_HOST"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
        cursorclass=pymysql.cursors.DictCursor,
    )


def find_user_by_email(email: str) -> dict:
    """Find user by email address."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT u.id, u.email, u.name,
                       f.instance_id, f.last_activity
                FROM users u
                LEFT JOIN free_user_assignments f ON u.id = f.user_id
                WHERE u.email LIKE %s
                ORDER BY u.id
                """,
                (f"%{email}%",),
            )
            return cursor.fetchall()
    finally:
        conn.close()


def get_user_status(user_id: int) -> dict:
    """Get current user assignment status."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Get user info
            cursor.execute(
                """
                SELECT u.id, u.email, u.name
                FROM users u
                WHERE u.id = %s
                """,
                (user_id,),
            )
            user = cursor.fetchone()

            if not user:
                return {"error": f"User {user_id} not found"}

            # Get assignment info
            cursor.execute(
                """
                SELECT instance_id, last_activity, assigned_at,
                       migration_count, active_workflow_count
                FROM free_user_assignments
                WHERE user_id = %s
                """,
                (user_id,),
            )
            assignment = cursor.fetchone()

            return {
                "user": user,
                "assignment": assignment,
            }
    finally:
        conn.close()


def list_instances() -> list:
    """List all instances with user counts."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT instance_id,
                       COUNT(*) as user_count,
                       MAX(last_activity) as latest_activity
                FROM free_user_assignments
                GROUP BY instance_id
                ORDER BY user_count DESC
                """
            )
            return cursor.fetchall()
    finally:
        conn.close()


def migrate_user(user_id: int, target_instance: str = None) -> dict:
    """
    Migrate user to target instance and trigger sync.

    If target_instance is None, auto-selects a different instance
    (scaling up if necessary).
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Get current assignment
            cursor.execute(
                """
                SELECT instance_id, active_workflow_count
                FROM free_user_assignments
                WHERE user_id = %s
                """,
                (user_id,),
            )
            current = cursor.fetchone()

            if not current:
                return {"error": f"No assignment found for user {user_id}"}

            if current["active_workflow_count"] > 0:
                return {
                    "error": f"User has {current['active_workflow_count']} "
                    "active workflows - cannot migrate"
                }

            old_instance = current["instance_id"]

            # Auto-select target instance if not specified
            if target_instance is None:
                asg_status = get_asg_status()
                if "error" in asg_status:
                    return asg_status

                healthy = [
                    i["id"]
                    for i in asg_status["instances"]
                    if i["state"] == "InService" and i["health"] == "Healthy"
                ]

                # Need at least 2 instances to migrate
                if len(healthy) < 2:
                    print("Only 1 instance available. Scaling up...")
                    scale_result = scale_asg(2, wait=True)
                    if "error" in scale_result:
                        return scale_result
                    healthy = scale_result.get("instances", [])

                # Pick an instance different from current
                other_instances = [i for i in healthy if i != old_instance]
                if not other_instances:
                    return {"error": "No other instance available for migration"}

                target_instance = other_instances[0]
                print(f"Auto-selected target instance: {target_instance}")

            # Perform migration
            cursor.execute(
                """
                UPDATE free_user_assignments
                SET instance_id = %s,
                    migration_count = migration_count + 1,
                    last_migration = NOW()
                WHERE user_id = %s
                """,
                (target_instance, user_id),
            )
            conn.commit()

            result = {
                "status": "migrated",
                "user_id": user_id,
                "from_instance": old_instance,
                "to_instance": target_instance,
            }

            # Trigger sync
            sync_result = trigger_sync(user_id)
            result["sync"] = sync_result

            return result
    finally:
        conn.close()


def scale_asg(desired_count: int, wait: bool = True) -> dict:
    """Scale the free tier ASG to desired count."""
    import boto3

    # Get ASG name from Terraform output
    try:
        result = subprocess.run(
            ["terraform", "output", "-raw", "asg_name"],
            capture_output=True,
            text=True,
            check=True,
        )
        asg_name = result.stdout.strip()
    except subprocess.CalledProcessError:
        return {"error": "Could not get asg_name from Terraform output"}

    print(f"Scaling ASG '{asg_name}' to {desired_count} instances...")

    try:
        autoscaling = boto3.client("autoscaling")

        # Get current state
        response = autoscaling.describe_auto_scaling_groups(
            AutoScalingGroupNames=[asg_name]
        )
        if not response["AutoScalingGroups"]:
            return {"error": f"ASG '{asg_name}' not found"}

        asg = response["AutoScalingGroups"][0]
        current_count = asg["DesiredCapacity"]
        current_instances = [i["InstanceId"] for i in asg["Instances"]]

        print(f"Current capacity: {current_count}, Instances: {current_instances}")

        if current_count == desired_count:
            return {
                "status": "no_change",
                "current_count": current_count,
                "instances": current_instances,
            }

        # Scale ASG
        autoscaling.set_desired_capacity(
            AutoScalingGroupName=asg_name,
            DesiredCapacity=desired_count,
            HonorCooldown=False,
        )
        print(f"Requested scale to {desired_count} instances")

        if not wait:
            return {
                "status": "scaling_initiated",
                "from": current_count,
                "to": desired_count,
            }

        # Wait for instances to be ready
        print("Waiting for instances to become healthy...")
        import time

        max_wait = 300  # 5 minutes
        start_time = time.time()

        while time.time() - start_time < max_wait:
            response = autoscaling.describe_auto_scaling_groups(
                AutoScalingGroupNames=[asg_name]
            )
            asg = response["AutoScalingGroups"][0]
            healthy = [
                i
                for i in asg["Instances"]
                if i["LifecycleState"] == "InService" and i["HealthStatus"] == "Healthy"
            ]

            print(
                f"  {len(healthy)}/{desired_count} instances healthy "
                f"({int(time.time() - start_time)}s elapsed)"
            )

            if len(healthy) >= desired_count:
                instance_ids = [i["InstanceId"] for i in healthy]
                return {
                    "status": "scaled",
                    "from": current_count,
                    "to": desired_count,
                    "instances": instance_ids,
                }

            time.sleep(10)

        return {"error": "Timeout waiting for instances to become healthy"}

    except Exception as e:
        return {"error": str(e)}


def get_asg_status() -> dict:
    """Get current ASG status."""
    import boto3

    try:
        result = subprocess.run(
            ["terraform", "output", "-raw", "asg_name"],
            capture_output=True,
            text=True,
            check=True,
        )
        asg_name = result.stdout.strip()
    except subprocess.CalledProcessError:
        return {"error": "Could not get asg_name from Terraform output"}

    try:
        autoscaling = boto3.client("autoscaling")
        response = autoscaling.describe_auto_scaling_groups(
            AutoScalingGroupNames=[asg_name]
        )
        if not response["AutoScalingGroups"]:
            return {"error": f"ASG '{asg_name}' not found"}

        asg = response["AutoScalingGroups"][0]
        return {
            "asg_name": asg_name,
            "desired_capacity": asg["DesiredCapacity"],
            "min_size": asg["MinSize"],
            "max_size": asg["MaxSize"],
            "instances": [
                {
                    "id": i["InstanceId"],
                    "state": i["LifecycleState"],
                    "health": i["HealthStatus"],
                }
                for i in asg["Instances"]
            ],
        }
    except Exception as e:
        return {"error": str(e)}


def trigger_sync(user_id: int) -> dict:
    """Trigger experiment sync via internal API."""
    import requests

    alb_dns = os.environ.get("ALB_DNS_NAME")
    internal_secret = os.environ.get("INTERNAL_API_SECRET")

    if not alb_dns or not internal_secret:
        return {"error": "ALB_DNS_NAME or INTERNAL_API_SECRET not configured"}

    url = f"https://{alb_dns}/internal/sync-experiments/{user_id}"
    headers = {
        "X-Internal-Secret": internal_secret,
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(url, headers=headers, timeout=30.0)
        resp_body = response.json() if response.status_code == 200 else response.text
        return {"status_code": response.status_code, "response": resp_body}
    except requests.exceptions.SSLError:
        # Try without SSL verification for internal ALB
        print("Warning: SSL verification failed, retrying without verify")
        response = requests.post(url, headers=headers, timeout=30.0, verify=False)
        resp_body = response.json() if response.status_code == 200 else response.text
        return {"status_code": response.status_code, "response": resp_body}
    except Exception as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(
        description="Test proactive experiment sync feature"
    )

    # Global options
    parser.add_argument(
        "--from-terraform",
        action="store_true",
        help="Load config from Terraform outputs and AWS Secrets Manager",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Find user command
    find_parser = subparsers.add_parser("find-user", help="Find user by email")
    find_parser.add_argument("email", help="Email address (partial match)")

    # Status command
    status_parser = subparsers.add_parser("status", help="Get user assignment status")
    status_parser.add_argument("user_id", type=int, help="User ID to check")

    # List instances command
    subparsers.add_parser("list-instances", help="List all instances with user counts")

    # Migrate command
    migrate_parser = subparsers.add_parser(
        "migrate", help="Migrate user to different instance (auto-scales if needed)"
    )
    migrate_parser.add_argument("user_id", type=int, help="User ID to migrate")
    migrate_parser.add_argument(
        "target_instance",
        nargs="?",
        default=None,
        help="Target instance ID (optional - auto-selects if omitted)",
    )
    migrate_parser.add_argument(
        "--scale-down",
        action="store_true",
        help="Scale back to 1 instance after migration",
    )

    # Sync command
    sync_parser = subparsers.add_parser("sync", help="Trigger experiment sync for user")
    sync_parser.add_argument("user_id", type=int, help="User ID to sync")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Load config from Terraform if requested
    if args.from_terraform:
        load_from_terraform()

    # Check required environment variables
    required_vars = ["DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"]
    if args.command in ["migrate", "sync"]:
        required_vars.extend(["ALB_DNS_NAME", "INTERNAL_API_SECRET"])

    missing = [v for v in required_vars if not os.environ.get(v)]
    if missing:
        print(f"Error: Missing environment variables: {', '.join(missing)}")
        print("Tip: Use --from-terraform to auto-load from Terraform outputs")
        sys.exit(1)

    # Execute command
    if args.command == "find-user":
        users = find_user_by_email(args.email)
        print(f"\n=== Users matching '{args.email}' ===")
        if not users:
            print("No users found")
        else:
            for u in users:
                instance = u["instance_id"] or "(no assignment)"
                print(f"  ID: {u['id']:4d} | {u['email']} | {u['name']}")
                print(f"         Instance: {instance}")
                if u["last_activity"]:
                    print(f"         Last Activity: {u['last_activity']}")
                print()

    elif args.command == "status":
        result = get_user_status(args.user_id)
        print(f"\n=== User Status for ID {args.user_id} ===")
        if "error" in result:
            print(f"Error: {result['error']}")
        else:
            user = result["user"]
            print(f"Name: {user['name']}")
            print(f"Email: {user['email']}")

            assignment = result["assignment"]
            if assignment:
                print(f"\nInstance: {assignment['instance_id']}")
                print(f"Last Activity: {assignment['last_activity']}")
                print(f"Migration Count: {assignment['migration_count']}")
                print(f"Active Workflows: {assignment['active_workflow_count']}")
            else:
                print("\nNo free tier assignment found")

    elif args.command == "list-instances":
        instances = list_instances()
        print("\n=== Instances with User Counts ===")
        if not instances:
            print("No instances found")
        else:
            for inst in instances:
                print(
                    f"  {inst['instance_id']}: "
                    f"{inst['user_count']} users, "
                    f"latest activity: {inst['latest_activity']}"
                )

    elif args.command == "migrate":
        target = args.target_instance or "(auto-select)"
        print(f"\n=== Migrating user {args.user_id} to {target} ===")
        result = migrate_user(args.user_id, args.target_instance)
        if "error" in result:
            print(f"Error: {result['error']}")
        else:
            print(f"Status: {result['status']}")
            print(f"From: {result['from_instance']}")
            print(f"To: {result['to_instance']}")
            print(f"Sync Result: {result['sync']}")

            # Scale down if requested
            if args.scale_down:
                print("\n=== Scaling back down to 1 instance ===")
                scale_result = scale_asg(1, wait=False)
                if "error" in scale_result:
                    print(f"Scale down error: {scale_result['error']}")
                else:
                    print("Scale down initiated")

    elif args.command == "sync":
        print(f"\nTriggering sync for user {args.user_id}...")
        result = trigger_sync(args.user_id)
        if "error" in result:
            print(f"Error: {result['error']}")
        else:
            print(f"Status Code: {result['status_code']}")
            print(f"Response: {result['response']}")


if __name__ == "__main__":
    main()
