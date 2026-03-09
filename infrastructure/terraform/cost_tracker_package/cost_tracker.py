"""
Cost Tracker Lambda Function

Tracks resource usage, actual AWS spend, and per-user cost metrics.
Publishes to CloudWatch namespace Optinist/CostTracking on an hourly schedule.

Uses instance_usage_log table for accurate per-user cost reporting
based on actual session hours rather than assuming 24/7 usage.
"""

import calendar
import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict

import boto3
import pymysql
from aws_constants import DatabaseConfig

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Hourly rates for usage-based cost (t3.large ap-northeast-1, as of March 2026)
PREMIUM_HOURLY_RATE = float(os.environ.get("PREMIUM_HOURLY_RATE", "0.1088"))
FREE_HOURLY_RATE = float(os.environ.get("FREE_HOURLY_RATE", "0.1088"))

NAMESPACE = "Optinist/CostTracking"

SSL_ARGS = {"check_hostname": False}


# ============================================================
# Database helpers (same pattern as common_user_manager.py)
# ============================================================


def get_required_env_var(var_name: str, default_value: str = None) -> str:
    value = os.environ.get(var_name, default_value)
    if value is None or value == "":
        raise ValueError(f"Missing required environment variable: {var_name}")
    return value


def _get_db_params():
    """Parse RDS connection params from environment."""
    rds_host = get_required_env_var("RDS_HOST")
    if ":" in rds_host:
        host, port_str = rds_host.split(":", 1)
        port = int(port_str)
    else:
        host = rds_host
        port = DatabaseConfig.DEFAULT_PORT
    return {
        "host": host,
        "port": port,
        "user": get_required_env_var("RDS_USER"),
        "password": get_required_env_var("RDS_PASSWORD"),
        "database": get_required_env_var("RDS_DATABASE"),
    }


def _create_ssl_connection(params):
    """Create a pymysql connection with SSL enforcement."""
    return pymysql.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        database=params["database"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        ssl=SSL_ARGS,
    )


def get_db_connection():
    """Get pymysql connection for direct SQL queries."""

    @contextmanager
    def connection_context():
        conn = None
        try:
            conn = _create_ssl_connection(_get_db_params())
            yield conn
        finally:
            if conn:
                conn.close()

    return connection_context()


# ============================================================
# Data collection functions
# ============================================================


def track_premium_instances(ec2_client) -> Dict[str, Any]:
    """Track premium instance counts using EC2 DescribeInstances with tag filter."""
    try:
        response = ec2_client.describe_instances(
            Filters=[
                {"Name": "tag:Service", "Values": ["premium-tier"]},
                {
                    "Name": "instance-state-name",
                    "Values": ["running", "stopped", "pending"],
                },
            ]
        )

        running = 0
        stopped = 0
        for reservation in response.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                state = instance.get("State", {}).get("Name")
                if state == "running":
                    running += 1
                elif state == "stopped":
                    stopped += 1

        logger.info(f"Premium instances — running: {running}, stopped: {stopped}")
        return {"running_instances": running, "stopped_instances": stopped}

    except Exception as e:
        logger.warning(f"Error tracking premium instances: {e}")
        return {"running_instances": 0, "stopped_instances": 0}


def track_free_instances(asg_client, asg_name: str | None) -> Dict[str, Any]:
    """Track free tier instance metrics from ASG."""
    try:
        if not asg_name:
            logger.warning("No ASG name provided")
            return {"instance_count": 0, "running_instances": 0}

        response = asg_client.describe_auto_scaling_groups(
            AutoScalingGroupNames=[asg_name]
        )

        if not response["AutoScalingGroups"]:
            logger.warning(f"ASG {asg_name} not found")
            return {"instance_count": 0, "running_instances": 0}

        asg = response["AutoScalingGroups"][0]
        instances = asg.get("Instances", [])
        running_instances = len(
            [i for i in instances if i.get("LifecycleState") == "InService"]
        )

        logger.info(
            f"Free tier instances — total: {len(instances)}, "
            f"running: {running_instances}"
        )
        return {
            "instance_count": len(instances),
            "running_instances": running_instances,
        }

    except Exception as e:
        logger.warning(f"Error tracking free instances: {e}")
        return {"instance_count": 0, "running_instances": 0}


def query_user_counts() -> Dict[str, int]:
    """Query active user counts from RDS."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) as count FROM free_user_assignments")
                free_count = cursor.fetchone()["count"]

                cursor.execute(
                    "SELECT COUNT(*) as count FROM premium_user_assignments "
                    "WHERE status = 'active' AND is_standby = 0"
                )
                premium_count = cursor.fetchone()["count"]

        logger.info(f"User counts — free: {free_count}, premium: {premium_count}")
        return {"free": free_count, "premium": premium_count}

    except Exception as e:
        logger.warning(f"Error querying user counts: {e}")
        return {"free": 0, "premium": 0}


def query_actual_spend(ce_client) -> float:
    """Query Cost Explorer for month-to-date actual spend."""
    try:
        now = datetime.now(timezone.utc)
        first_of_month = now.strftime("%Y-%m-01")
        today = now.strftime("%Y-%m-%d")

        # CE end date is exclusive; if today is the 1st, no data yet
        if first_of_month == today:
            logger.info("First day of month — no Cost Explorer data yet")
            return 0.0

        response = ce_client.get_cost_and_usage(
            TimePeriod={"Start": first_of_month, "End": today},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
        )

        results = response.get("ResultsByTime", [])
        if results:
            amount = float(
                results[0].get("Total", {}).get("UnblendedCost", {}).get("Amount", "0")
            )
            logger.info(f"Month-to-date spend: ${amount:.2f}")
            return amount

        return 0.0

    except Exception as e:
        logger.warning(f"Error querying Cost Explorer: {e}")
        return 0.0


def query_usage_hours() -> Dict[str, Any]:
    """Query instance_usage_log for session hours this month."""
    try:
        now = datetime.now(timezone.utc)
        month_start = now.strftime("%Y-%m-01 00:00:00")

        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Premium: per-user hours this month
                cursor.execute(
                    """SELECT COUNT(DISTINCT user_id) AS user_count,
                              SUM(TIMESTAMPDIFF(SECOND, started_at,
                                  COALESCE(ended_at, NOW())) / 3600.0) AS total_hours
                       FROM instance_usage_log
                       WHERE tier = 'premium' AND started_at >= %s""",
                    (month_start,),
                )
                premium_row = cursor.fetchone()

                # Free: total session hours
                cursor.execute(
                    """SELECT COUNT(DISTINCT user_id) AS user_count,
                              SUM(TIMESTAMPDIFF(SECOND, started_at,
                                  COALESCE(ended_at, NOW())) / 3600.0) AS total_hours
                       FROM instance_usage_log
                       WHERE tier = 'free' AND started_at >= %s""",
                    (month_start,),
                )
                free_row = cursor.fetchone()

        result = {
            "premium_user_count": int(premium_row["user_count"] or 0),
            "premium_total_hours": float(premium_row["total_hours"] or 0.0),
            "free_user_count": int(free_row["user_count"] or 0),
            "free_total_hours": float(free_row["total_hours"] or 0.0),
        }

        logger.info(
            f"Usage hours — premium: {result['premium_total_hours']:.1f}h "
            f"({result['premium_user_count']} users), "
            f"free: {result['free_total_hours']:.1f}h "
            f"({result['free_user_count']} users)"
        )
        return result

    except Exception as e:
        logger.warning(f"Error querying usage hours: {e}")
        return {
            "premium_user_count": 0,
            "premium_total_hours": 0.0,
            "free_user_count": 0,
            "free_total_hours": 0.0,
        }


def generate_usage_report() -> Dict[str, Any]:
    """Generate per-user usage breakdown for the current month.

    Returns a dict with per-user rows and tier-level averages, suitable for
    the monthly maintenance report.
    """
    try:
        now = datetime.now(timezone.utc)
        month_start = now.strftime("%Y-%m-01 00:00:00")
        month_label = now.strftime("%Y-%m")

        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """SELECT user_id, tier,
                              COUNT(*) AS sessions,
                              SUM(TIMESTAMPDIFF(SECOND, started_at,
                                  COALESCE(ended_at, NOW())) / 3600.0) AS hours
                       FROM instance_usage_log
                       WHERE started_at >= %s
                       GROUP BY user_id, tier
                       ORDER BY hours DESC""",
                    (month_start,),
                )
                rows = cursor.fetchall()

        users = []
        premium_hours_total = 0.0
        free_hours_total = 0.0
        premium_user_count = 0
        free_user_count = 0

        for row in rows:
            hours = float(row["hours"] or 0.0)
            tier = row["tier"]
            rate = PREMIUM_HOURLY_RATE if tier == "premium" else FREE_HOURLY_RATE
            cost = hours * rate

            users.append(
                {
                    "user_id": row["user_id"],
                    "tier": tier,
                    "sessions": int(row["sessions"]),
                    "hours": round(hours, 2),
                    "cost": round(cost, 2),
                }
            )

            if tier == "premium":
                premium_hours_total += hours
                premium_user_count += 1
            else:
                free_hours_total += hours
                free_user_count += 1

        premium_cost_total = premium_hours_total * PREMIUM_HOURLY_RATE
        free_cost_total = free_hours_total * FREE_HOURLY_RATE

        summary = {
            "month": month_label,
            "premium": {
                "users": premium_user_count,
                "total_hours": round(premium_hours_total, 2),
                "total_cost": round(premium_cost_total, 2),
                "avg_hours": round(premium_hours_total / premium_user_count, 2)
                if premium_user_count > 0
                else 0.0,
                "avg_cost": round(premium_cost_total / premium_user_count, 2)
                if premium_user_count > 0
                else 0.0,
            },
            "free": {
                "users": free_user_count,
                "total_hours": round(free_hours_total, 2),
                "total_cost": round(free_cost_total, 2),
                "avg_hours": round(free_hours_total / free_user_count, 2)
                if free_user_count > 0
                else 0.0,
                "avg_cost": round(free_cost_total / free_user_count, 2)
                if free_user_count > 0
                else 0.0,
            },
        }

        logger.info(
            f"Usage report — {len(users)} users, "
            f"premium: {premium_user_count} ({premium_hours_total:.1f}h), "
            f"free: {free_user_count} ({free_hours_total:.1f}h)"
        )

        return {"summary": summary, "users": users}

    except Exception as e:
        logger.error(f"Error generating usage report: {e}")
        return {"summary": {}, "users": [], "error": str(e)}


def calculate_metrics(
    premium_instances: Dict[str, Any],
    free_instances: Dict[str, Any],
    user_counts: Dict[str, int],
    actual_spend: float,
    usage_hours: Dict[str, Any],
) -> Dict[str, Any]:
    """Derive per-user costs, utilization, and budget thresholds."""
    premium_count = user_counts["premium"]
    free_count = user_counts["free"]
    premium_running = premium_instances["running_instances"]
    free_running = free_instances.get("running_instances", 0)

    now = datetime.now(timezone.utc)
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    days_elapsed = max(now.day - 1 + now.hour / 24.0, 1)

    # Projected budget from actual spend trend
    daily_rate = actual_spend / days_elapsed
    projected_monthly = daily_rate * days_in_month

    # Usage-based per-user costs
    # Premium: actual session hours * hourly rate / active users
    premium_total_cost = usage_hours["premium_total_hours"] * PREMIUM_HOURLY_RATE
    cost_per_premium = (
        (premium_total_cost / usage_hours["premium_user_count"])
        if usage_hours["premium_user_count"] > 0
        else 0.0
    )

    # Free: actual session hours * hourly rate / unique free users this month
    free_total_cost = usage_hours["free_total_hours"] * FREE_HOURLY_RATE
    cost_per_free = (
        (free_total_cost / usage_hours["free_user_count"])
        if usage_hours["free_user_count"] > 0
        else 0.0
    )

    # Utilization: users per available capacity
    premium_utilization = (
        (premium_count / premium_running * 100) if premium_running > 0 else 0.0
    )
    # Free tier: each instance serves ~5 users
    free_capacity = free_running * 5
    free_utilization = (free_count / free_capacity * 100) if free_capacity > 0 else 0.0

    return {
        "projected_monthly": projected_monthly,
        "cost_per_premium": cost_per_premium,
        "cost_per_free": cost_per_free,
        "premium_utilization": premium_utilization,
        "free_utilization": free_utilization,
        "premium_session_hours_mtd": usage_hours["premium_total_hours"],
    }


def publish_metrics(
    cloudwatch_client,
    premium_instances: Dict[str, Any],
    free_instances: Dict[str, Any],
    user_counts: Dict[str, int],
    actual_spend: float,
    calculated: Dict[str, Any],
) -> None:
    """Publish all metrics to CloudWatch."""
    try:
        now = datetime.now(timezone.utc)

        metric_data = [
            {
                "MetricName": "PremiumInstanceCount",
                "Value": premium_instances["running_instances"],
                "Unit": "Count",
                "Timestamp": now,
            },
            {
                "MetricName": "FreeInstanceCount",
                "Value": free_instances.get("running_instances", 0),
                "Unit": "Count",
                "Timestamp": now,
            },
            {
                "MetricName": "ActivePremiumUsers",
                "Value": user_counts["premium"],
                "Unit": "Count",
                "Timestamp": now,
            },
            {
                "MetricName": "ActiveFreeUsers",
                "Value": user_counts["free"],
                "Unit": "Count",
                "Timestamp": now,
            },
            {
                "MetricName": "ActualMonthToDateSpend",
                "Value": actual_spend,
                "Unit": "None",
                "Timestamp": now,
            },
            {
                "MetricName": "ExpectedMonthlyBudget",
                "Value": calculated["projected_monthly"],
                "Unit": "None",
                "Timestamp": now,
            },
            {
                "MetricName": "CostPerPremiumUser",
                "Value": calculated["cost_per_premium"],
                "Unit": "None",
                "Timestamp": now,
            },
            {
                "MetricName": "CostPerFreeUser",
                "Value": calculated["cost_per_free"],
                "Unit": "None",
                "Timestamp": now,
            },
            {
                "MetricName": "PremiumUtilization",
                "Value": calculated["premium_utilization"],
                "Unit": "Percent",
                "Timestamp": now,
            },
            {
                "MetricName": "FreeUtilization",
                "Value": calculated["free_utilization"],
                "Unit": "Percent",
                "Timestamp": now,
            },
            {
                "MetricName": "PremiumSessionHoursMTD",
                "Value": calculated["premium_session_hours_mtd"],
                "Unit": "Count",
                "Timestamp": now,
            },
        ]

        # CloudWatch accepts max 1000 metric data points per call,
        # but best practice is batches of 20
        cloudwatch_client.put_metric_data(Namespace=NAMESPACE, MetricData=metric_data)

        logger.info(f"Published {len(metric_data)} metrics to {NAMESPACE}")

    except Exception as e:
        logger.error(f"Error publishing metrics: {e}")


# ============================================================
# Handler
# ============================================================


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Cost tracking Lambda handler — orchestrates all tracking steps.

    Supports two modes via event["mode"]:
      - (default): hourly metrics collection and CloudWatch publish
      - "usage_report": on-demand per-user usage breakdown for reports
    """
    logger.info("Cost tracking Lambda invoked")

    mode = event.get("mode", "metrics")

    if mode == "usage_report":
        try:
            report = generate_usage_report()
            return {
                "statusCode": 200,
                "body": json.dumps(report, default=str),
            }
        except Exception as e:
            logger.error(f"Error generating usage report: {e}")
            return {
                "statusCode": 500,
                "body": json.dumps({"error": str(e)}),
            }

    try:
        region = os.environ.get("REGION", "ap-northeast-1")
        asg_name = os.environ.get("ASG_NAME")

        # Initialize AWS clients
        ec2_client = boto3.client("ec2", region_name=region)
        cloudwatch_client = boto3.client("cloudwatch", region_name=region)
        asg_client = boto3.client("autoscaling", region_name=region)
        # Cost Explorer endpoint is always us-east-1
        ce_client = boto3.client("ce", region_name="us-east-1")

        # 1. Collect instance counts
        premium_instances = track_premium_instances(ec2_client)
        free_instances = track_free_instances(asg_client, asg_name)

        # 2. Query active user counts from RDS
        user_counts = query_user_counts()

        # 3. Query actual month-to-date spend from Cost Explorer
        actual_spend = query_actual_spend(ce_client)

        # 4. Query usage hours from instance_usage_log
        usage_hours = query_usage_hours()

        # 5. Calculate derived metrics
        calculated = calculate_metrics(
            premium_instances,
            free_instances,
            user_counts,
            actual_spend,
            usage_hours,
        )

        # 6. Publish all metrics to CloudWatch
        publish_metrics(
            cloudwatch_client,
            premium_instances,
            free_instances,
            user_counts,
            actual_spend,
            calculated,
        )

        logger.info("Cost tracking completed successfully")

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Cost tracking completed",
                    "premium_instances": premium_instances,
                    "free_instances": free_instances,
                    "user_counts": user_counts,
                    "actual_spend": actual_spend,
                    "usage_hours": usage_hours,
                    "calculated": calculated,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            ),
        }

    except Exception as e:
        logger.error(f"Error in cost tracking: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps(
                {
                    "message": f"Cost tracking failed: {str(e)}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            ),
        }
