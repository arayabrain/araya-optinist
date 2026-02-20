"""
Free Tier Cleanup Lambda Function

Handles test data cleanup and simulation for Free Manager testing:
- Cleanup test user sessions
- Simulate user activity for testing
- Simulate workflows for testing
- Query user distribution

Designed to be invoked by test scripts to bypass VPC restrictions.
Test scripts run outside VPC and cannot access RDS directly.
This Lambda has VPC access and can connect to RDS from inside VPC.
"""

import decimal
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pymysql

# Shared constants from Lambda Layer (mounted at /opt/python by AWS Lambda)
from aws_constants import DatabaseConfig


class DecimalEncoder(json.JSONEncoder):
    """Custom JSON encoder to handle Decimal types from database"""

    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            # If it's a whole number, convert to int, otherwise float
            return int(obj) if obj % 1 == 0 else float(obj)
        return super(DecimalEncoder, self).default(obj)


def get_required_env_var(var_name: str, default_value: str | None = None) -> str:
    """Safely get required environment variable with helpful error message"""
    value = os.environ.get(var_name, default_value)
    if value is None or value == "":
        raise ValueError(
            f"Missing required environment variable: {var_name}. "
            "Check your Terraform configuration and Lambda environment settings."
        )
    return value


def get_db_connection(auto_commit=False):
    """
    Create database connection with proper transaction management and auto-close.

    This function returns a context manager that ensures connections are properly
    closed when exiting the context, preventing connection leaks.

    Usage:
        with get_db_connection() as conn:
            # Use connection
            # Connection will be automatically closed on exit
    """
    from contextlib import contextmanager

    @contextmanager
    def connection_context():
        conn = None
        try:
            rds_host = get_required_env_var("RDS_HOST")
            conn = pymysql.connect(
                host=rds_host.split(":")[0],
                port=int(rds_host.split(":")[1])
                if ":" in rds_host
                else DatabaseConfig.DEFAULT_PORT,
                user=get_required_env_var("RDS_USER"),
                password=get_required_env_var("RDS_PASSWORD"),
                database=get_required_env_var("RDS_DATABASE"),
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=auto_commit,
            )
            yield conn
        except Exception as e:
            print(f" Database connection failed: {str(e)}")
            raise
        finally:
            # CRITICAL: Always close the connection to prevent leaks
            if conn is not None:
                try:
                    conn.close()
                    print(" Database connection closed")
                except Exception as e:
                    print(f" Warning: Error closing database connection: {str(e)}")

    return connection_context()


def with_transaction(func):
    """Decorator to wrap database operations in transactions"""

    def wrapper(*args, **kwargs):
        with get_db_connection() as conn:
            try:
                result = func(conn, *args, **kwargs)
                conn.commit()
                return result
            except Exception as e:
                conn.rollback()
                print(f"Transaction rolled back due to error: {e}")
                raise e

    return wrapper


@with_transaction
def cleanup_test_user_sessions(connection, user_emails: List[str]) -> Dict[str, Any]:
    """
    Clean up free tier user sessions for specific test users by email.
    Designed to be called by test scripts that need to clean up test data.

    This function performs complete cleanup:
    1. Looks up user IDs from email addresses
    2. Deletes all free_user_assignments records for those users

    Usage (from test scripts):
        lambda_client = boto3.client('lambda')
        response = lambda_client.invoke(
            FunctionName='subscr-free-cleanup',
            InvocationType='RequestResponse',
            Payload=json.dumps({
                "action": "cleanup_test_users",
                "user_emails": ["user1@test.com", "user2@test.com"]
            })
        )

    Args:
        connection: Database connection (provided by @with_transaction decorator)
        user_emails: List of user email addresses to clean up sessions for

    Returns:
        Dict with cleanup statistics:
        {
            "success": True/False,
            "message": "Description of what happened",
            "sessions_deleted": 3,
            "users_cleaned": 2
        }
    """
    try:
        if not user_emails:
            return {
                "success": False,
                "message": "No user emails provided",
                "sessions_deleted": 0,
            }

        print(f"Cleaning up free tier sessions for {len(user_emails)} test users")

        with connection.cursor() as cursor:
            # First, get user IDs for the given emails
            placeholders = ", ".join(["%s"] * len(user_emails))
            cursor.execute(
                f"""SELECT id, email FROM users
                    WHERE email IN ({placeholders})""",
                user_emails,
            )
            users = cursor.fetchall()

            if not users:
                return {
                    "success": True,
                    "message": "No users found with provided emails",
                    "sessions_deleted": 0,
                }

            user_ids = [user["id"] for user in users]
            print(f"Found {len(user_ids)} users to clean up")

            # Delete free_user_assignments for these users
            user_id_placeholders = ", ".join(["%s"] * len(user_ids))
            cursor.execute(
                "DELETE FROM free_user_assignments "
                "WHERE user_id IN (" + user_id_placeholders + ")",
                user_ids,
            )
            deleted_count = cursor.rowcount

            print(f"Test cleanup complete: {deleted_count} sessions deleted")

            return {
                "success": True,
                "message": f"Cleaned {deleted_count} test user sessions",
                "sessions_deleted": deleted_count,
                "users_cleaned": len(user_ids),
            }

    except Exception as e:
        print(f"Error during test user cleanup: {str(e)}")
        raise e


def get_free_test_user_ids(num_users: int = 6) -> Dict[str, Any]:
    """
    Get user IDs for free tier test users by email pattern.
    Returns actual database IDs for users matching
    'optinist_test_user_free_%@araya.org'.

    Args:
        num_users: Number of test users to return (default: 6)

    Returns:
        Dict with user IDs and emails:
        {
            "success": True,
            "user_ids": [9, 10, 11, 12, 13, 14],
            "users": [{"id": 9, "email": "optinist_test_user_free_1@araya.org"}, ...]
        }
    """
    try:
        email_pattern = "optinist_test_user_free_%@araya.org"
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT id, email FROM users
                       WHERE email LIKE %s
                       ORDER BY id
                       LIMIT %s""",
                    (email_pattern, num_users),
                )
                users = cursor.fetchall()

                if not users:
                    return {
                        "success": False,
                        "message": "No free test users found in database",
                        "user_ids": [],
                        "users": [],
                    }

                user_ids = [user["id"] for user in users]
                print(f"Found {len(user_ids)} free test users: {user_ids}")

                return {
                    "success": True,
                    "message": f"Found {len(user_ids)} free test users",
                    "user_ids": user_ids,
                    "users": [{"id": u["id"], "email": u["email"]} for u in users],
                }

    except Exception as e:
        print(f"Error getting free test user IDs: {str(e)}")
        return {
            "success": False,
            "message": str(e),
            "user_ids": [],
            "users": [],
        }


@with_transaction
def cleanup_all_test_users(connection) -> Dict[str, Any]:
    """
    Clean up all free tier sessions for free test users.
    Cleans up users matching 'optinist_test_user_free_%@araya.org'.

    Args:
        connection: Database connection (provided by @with_transaction decorator)

    Returns:
        Dict with cleanup statistics
    """
    try:
        print("Cleaning up all free test users")
        email_pattern = "optinist_test_user_free_%@araya.org"

        with connection.cursor() as cursor:
            # First get user IDs for free test users
            cursor.execute(
                """SELECT id FROM users
                   WHERE email LIKE %s""",
                (email_pattern,),
            )
            users = cursor.fetchall()

            if not users:
                return {
                    "success": True,
                    "message": "No free test users found",
                    "sessions_deleted": 0,
                }

            user_ids = [u["id"] for u in users]
            placeholders = ", ".join(["%s"] * len(user_ids))

            cursor.execute(
                "DELETE FROM free_user_assignments "
                "WHERE user_id IN (" + placeholders + ")",
                user_ids,
            )
            deleted_count = cursor.rowcount

            print(f"Cleanup complete: {deleted_count} test user sessions deleted")

            return {
                "success": True,
                "message": f"Cleaned {deleted_count} test user sessions",
                "sessions_deleted": deleted_count,
            }

    except Exception as e:
        print(f"Error during test user cleanup: {str(e)}")
        raise e


@with_transaction
def simulate_user_activity(
    connection, user_id: str, instance_id: str, minutes_ago: int = 0
) -> Dict[str, Any]:
    """
    Insert/update user activity in free_user_assignments table.
    Used by test scripts to simulate user activity for testing.

    Args:
        connection: Database connection (provided by @with_transaction decorator)
        user_id: User ID (string format)
        instance_id: ECS instance ID to assign user to
        minutes_ago: How many minutes ago was last activity (default: 0 = now)

    Returns:
        Dict with success status
    """
    try:
        activity_time = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)

        with connection.cursor() as cursor:
            query = """
                INSERT INTO free_user_assignments
                    (user_id, instance_id, last_activity, active_workflow_count)
                VALUES (%s, %s, %s, 0)
                ON DUPLICATE KEY UPDATE
                    instance_id = %s,
                    last_activity = %s
            """
            cursor.execute(
                query, (user_id, instance_id, activity_time, instance_id, activity_time)
            )

        print(f"Simulated activity for user {user_id} on instance {instance_id}")

        return {
            "success": True,
            "message": f"Simulated activity for user {user_id}",
            "user_id": user_id,
            "instance_id": instance_id,
            "activity_time": activity_time.isoformat(),
        }

    except Exception as e:
        print(f"Error simulating user activity: {str(e)}")
        raise e


@with_transaction
def simulate_workflow_running(
    connection, user_id: str, workflow_count: int = 1
) -> Dict[str, Any]:
    """
    Set active_workflow_count for a user (simulates running workflows).
    Used by test scripts to test workflow protection during migration.

    Args:
        connection: Database connection (provided by @with_transaction decorator)
        user_id: User ID (string format)
        workflow_count: Number of active workflows (default: 1)

    Returns:
        Dict with success status
    """
    try:
        with connection.cursor() as cursor:
            query = """
                UPDATE free_user_assignments
                SET active_workflow_count = %s,
                    last_workflow_start = NOW()
                WHERE user_id = %s
            """
            cursor.execute(query, (workflow_count, user_id))
            updated = cursor.rowcount

            if updated == 0:
                return {
                    "success": False,
                    "message": f"User {user_id} not found in free_user_assignments",
                }

        print(f"Simulated {workflow_count} active workflow(s) for user {user_id}")

        return {
            "success": True,
            "message": f"Simulated {workflow_count} workflow(s) for user {user_id}",
            "user_id": user_id,
            "workflow_count": workflow_count,
        }

    except Exception as e:
        print(f"Error simulating workflow: {str(e)}")
        raise e


def get_user_distribution() -> Dict[str, Any]:
    """
    Get current user distribution across instances.
    Used by test scripts to verify rebalancing.

    Returns:
        Dict with user distribution stats
    """
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                # Get distribution by instance
                cursor.execute(
                    """
                    SELECT
                        instance_id,
                        COUNT(*) as user_count,
                        SUM(active_workflow_count) as total_workflows
                    FROM free_user_assignments
                    WHERE last_activity > NOW() - INTERVAL 10 MINUTE
                    GROUP BY instance_id
                    ORDER BY user_count DESC
                """
                )
                distribution = cursor.fetchall()

                # Get all users
                cursor.execute(
                    """
                    SELECT user_id, instance_id, active_workflow_count,
                    last_activity, migration_count
                    FROM free_user_assignments
                    WHERE last_activity > NOW() - INTERVAL 10 MINUTE
                    ORDER BY instance_id, user_id
                """
                )
                users = cursor.fetchall()

        result = {
            "success": True,
            "total_instances": len(distribution),
            "total_users": len(users),
            "distribution": [
                {
                    "instance_id": row["instance_id"],
                    "user_count": row["user_count"],
                    "total_workflows": row["total_workflows"] or 0,
                }
                for row in distribution
            ],
            "users": [
                {
                    "user_id": row["user_id"],
                    "instance_id": row["instance_id"],
                    "active_workflows": row["active_workflow_count"] or 0,
                    "last_activity": row["last_activity"].isoformat()
                    if row["last_activity"]
                    else None,
                    "migration_count": row["migration_count"] or 0,
                }
                for row in users
            ],
        }

        print(
            f"User distribution: {len(users)} users across "
            f"{len(distribution)} instances"
        )
        return result

    except Exception as e:
        print(f"Error getting user distribution: {str(e)}")
        return {"success": False, "error": str(e)}


def count_active_users(activity_threshold_minutes: int = 10) -> Dict[str, Any]:
    """
    Count users with activity in last N minutes.
    Used by test scripts to verify active user counting.

    Args:
        activity_threshold_minutes: Minutes threshold for considering user active

    Returns:
        Dict with active user count
    """
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*) as count
                    FROM free_user_assignments
                    WHERE last_activity > NOW() - INTERVAL %s MINUTE
                """,
                    (activity_threshold_minutes,),
                )
                result = cursor.fetchone()
                count = result["count"] if result else 0

        print(f"Active users (last {activity_threshold_minutes}min): {count}")

        return {
            "success": True,
            "active_user_count": count,
            "threshold_minutes": activity_threshold_minutes,
        }

    except Exception as e:
        print(f"Error counting active users: {str(e)}")
        return {"success": False, "error": str(e)}


def get_user_assignment(user_email: str) -> Dict[str, Any]:
    """
    Get the free_user_assignment for a specific user by email.
    Used by test scripts to check which instance a user is assigned to.

    Args:
        user_email: The user's email address

    Returns:
        Dict with user assignment info:
        {
            "success": True,
            "user_id": 123,
            "instance_id": "i-abc123",
            "active_workflow_count": 0,
            "last_activity": "2024-01-01T12:00:00",
            "migration_count": 0
        }
    """
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                # First, look up the user's database ID from their email
                cursor.execute(
                    "SELECT id FROM users WHERE email = %s",
                    (user_email,),
                )
                user = cursor.fetchone()

                if not user:
                    return {
                        "success": False,
                        "message": f"User not found: {user_email}",
                    }

                user_id = user["id"]

                # Now get the free_user_assignment for this user
                cursor.execute(
                    """
                    SELECT user_id, instance_id, active_workflow_count,
                           last_activity, migration_count
                    FROM free_user_assignments
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
                assignment = cursor.fetchone()

                if not assignment:
                    return {
                        "success": True,
                        "message": f"No assignment found for user {user_email}",
                        "user_id": user_id,
                        "instance_id": None,
                    }

                print(
                    f"User {user_email} (id={user_id}) assigned to "
                    f"instance {assignment['instance_id']}"
                )

                return {
                    "success": True,
                    "user_id": assignment["user_id"],
                    "instance_id": assignment["instance_id"],
                    "active_workflow_count": assignment["active_workflow_count"] or 0,
                    "last_activity": assignment["last_activity"].isoformat()
                    if assignment["last_activity"]
                    else None,
                    "migration_count": assignment["migration_count"] or 0,
                }

    except Exception as e:
        print(f"Error getting user assignment: {str(e)}")
        return {"success": False, "error": str(e)}


@with_transaction
def migrate_user(
    connection, user_email: str, target_instance_id: str
) -> Dict[str, Any]:
    """
    Migrate a free-tier user to a different instance.

    Replicates the atomic migration SQL from free_user_utils.py
    with workflow protection (active_workflow_count = 0).

    Args:
        connection: DB connection (provided by @with_transaction)
        user_email: Email of the user to migrate
        target_instance_id: EC2 instance ID to migrate to

    Returns:
        Dict with migration result
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM users WHERE email = %s",
                (user_email,),
            )
            user = cursor.fetchone()
            if not user:
                return {
                    "success": False,
                    "message": f"User not found: {user_email}",
                }

            user_id = user["id"]

            cursor.execute(
                """SELECT instance_id, active_workflow_count,
                          migration_count
                   FROM free_user_assignments
                   WHERE user_id = %s""",
                (user_id,),
            )
            assignment = cursor.fetchone()
            if not assignment:
                return {
                    "success": False,
                    "message": (f"No assignment for {user_email}"),
                }

            source = assignment["instance_id"]
            if source == target_instance_id:
                return {
                    "success": False,
                    "message": ("Source and target are the same" f": {source}"),
                }

            workflows = assignment["active_workflow_count"] or 0
            if workflows > 0:
                return {
                    "success": False,
                    "message": (
                        f"User has {workflows} active" " workflow(s), cannot migrate"
                    ),
                }

            cursor.execute(
                """UPDATE free_user_assignments
                   SET instance_id = %s,
                       migration_count = migration_count + 1,
                       last_migration = NOW()
                   WHERE user_id = %s
                     AND active_workflow_count = 0""",
                (target_instance_id, user_id),
            )

            if cursor.rowcount == 0:
                return {
                    "success": False,
                    "message": "Migration blocked (race condition)",
                }

            new_count = (assignment["migration_count"] or 0) + 1
            print(
                f"Migrated {user_email} (id={user_id})"
                f" {source} -> {target_instance_id}"
            )

            return {
                "success": True,
                "message": "Migration successful",
                "user_id": user_id,
                "source_instance": source,
                "target_instance": target_instance_id,
                "migration_count": new_count,
            }

    except Exception as e:
        print(f"Error migrating user: {str(e)}")
        raise e


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Free Cleanup Lambda Handler

    Supports manual invocations from test scripts:

    Actions:
    - cleanup_test_users: Clean up sessions for specific test users by email
      Event: {"action": "cleanup_test_users", "user_emails": ["email1@test.com", ...]}

    - cleanup_all_test_users: Clean up all users with 'test_' prefix
      Event: {"action": "cleanup_all_test_users"}

    - simulate_user_activity: Insert/update user activity
      Event: {"action": "simulate_user_activity", "user_id": "test_user_1",
              "instance_id": "i-123", "minutes_ago": 0}

    - simulate_workflow: Set active workflow count for user
      Event: {"action": "simulate_workflow", "user_id": "test_user_1",
              "workflow_count": 1}

    - get_user_distribution: Get current user distribution across instances
      Event: {"action": "get_user_distribution"}

    - count_active_users: Count users active in last N minutes
      Event: {"action": "count_active_users", "threshold_minutes": 10}

    - migrate_user: Migrate a user to a different instance
      Event: {"action": "migrate_user", "user_email": "u@test.com",
              "target_instance_id": "i-abc123"}
    """

    print(f"Free cleanup triggered by event: {json.dumps(event, cls=DecimalEncoder)}")
    print(f"Lambda context: {context.function_name if context else 'No context'}")

    try:
        action = event.get("action")

        if action == "cleanup_test_users":
            user_emails = event.get("user_emails", [])
            print(f"Manual test cleanup invocation for {len(user_emails)} users")
            result = cleanup_test_user_sessions(user_emails)
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {"message": result.get("message"), "result": result},
                    cls=DecimalEncoder,
                ),
            }

        elif action == "cleanup_all_test_users":
            print("Cleaning up all test users (user_id LIKE 'test_%')")
            result = cleanup_all_test_users()
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {"message": result.get("message"), "result": result},
                    cls=DecimalEncoder,
                ),
            }

        elif action == "simulate_user_activity":
            user_id = event.get("user_id")
            instance_id = event.get("instance_id")
            minutes_ago = event.get("minutes_ago", 0)

            if not user_id or not instance_id:
                return {
                    "statusCode": 400,
                    "body": json.dumps(
                        {"error": "Missing required parameters: user_id, instance_id"},
                        cls=DecimalEncoder,
                    ),
                }

            result = simulate_user_activity(user_id, instance_id, minutes_ago)
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {"message": result.get("message"), "result": result},
                    cls=DecimalEncoder,
                ),
            }

        elif action == "simulate_workflow":
            user_id = event.get("user_id")
            workflow_count = event.get("workflow_count", 1)

            if not user_id:
                return {
                    "statusCode": 400,
                    "body": json.dumps(
                        {"error": "Missing required parameter: user_id"},
                        cls=DecimalEncoder,
                    ),
                }

            result = simulate_workflow_running(user_id, workflow_count)
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {"message": result.get("message"), "result": result},
                    cls=DecimalEncoder,
                ),
            }

        elif action == "get_user_distribution":
            result = get_user_distribution()
            return {
                "statusCode": 200,
                "body": json.dumps({"result": result}, cls=DecimalEncoder),
            }

        elif action == "count_active_users":
            threshold_minutes = event.get("threshold_minutes", 10)
            result = count_active_users(threshold_minutes)
            return {
                "statusCode": 200,
                "body": json.dumps({"result": result}, cls=DecimalEncoder),
            }

        elif action == "get_free_test_user_ids":
            num_users = event.get("num_users", 6)
            result = get_free_test_user_ids(num_users)
            return {
                "statusCode": 200,
                "body": json.dumps({"result": result}, cls=DecimalEncoder),
            }

        elif action == "get_user_assignment":
            user_email = event.get("user_email")
            if not user_email:
                return {
                    "statusCode": 400,
                    "body": json.dumps(
                        {"error": "Missing required parameter: user_email"},
                        cls=DecimalEncoder,
                    ),
                }
            result = get_user_assignment(user_email)
            return {
                "statusCode": 200,
                "body": json.dumps({"result": result}, cls=DecimalEncoder),
            }

        elif action == "migrate_user":
            user_email = event.get("user_email")
            target_instance_id = event.get("target_instance_id")
            if not user_email or not target_instance_id:
                return {
                    "statusCode": 400,
                    "body": json.dumps(
                        {
                            "error": (
                                "Missing required parameters:"
                                " user_email,"
                                " target_instance_id"
                            )
                        },
                        cls=DecimalEncoder,
                    ),
                }
            result = migrate_user(user_email, target_instance_id)
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {"result": result},
                    cls=DecimalEncoder,
                ),
            }

        else:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {
                        "error": f"Unknown action: {action}",
                        "supported_actions": [
                            "cleanup_test_users",
                            "cleanup_all_test_users",
                            "simulate_user_activity",
                            "simulate_workflow",
                            "get_user_distribution",
                            "count_active_users",
                            "get_free_test_user_ids",
                            "get_user_assignment",
                            "migrate_user",
                        ],
                    },
                    cls=DecimalEncoder,
                ),
            }

    except Exception as e:
        print(f"Error during free cleanup: {str(e)}")
        import traceback

        traceback.print_exc()
        return {
            "statusCode": 500,
            "body": json.dumps(
                {"error": f"Free cleanup failed: {str(e)}"}, cls=DecimalEncoder
            ),
        }
