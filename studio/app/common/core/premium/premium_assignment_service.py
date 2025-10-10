"""
Premium Assignment Service

Handles automatic assignment of premium users to dedicated instances.
Integrates with the premium manager Lambda function to assign/release users.
"""

import asyncio
import json
import os
import time
from typing import Dict, Optional

import boto3

from studio.app.common.core.logger import AppLogger

logger = AppLogger.get_logger()

# Global in-memory cache for preventing concurrent assignment attempts
_assignment_attempts = {}
_RATE_LIMIT_SECONDS = 30


class PremiumAssignmentService:
    """Service for handling premium user assignments to dedicated instances."""

    def __init__(self):
        self.lambda_client = None
        self.premium_manager_function_name = os.environ.get(
            "PREMIUM_MANAGER_FUNCTION_NAME", "subscr-premium-manager"
        )

    def _get_lambda_client(self):
        """Get or create Lambda client."""
        if not self.lambda_client:
            self.lambda_client = boto3.client("lambda")
        return self.lambda_client

    def _check_rate_limit(self, user_id: int) -> bool:
        """Check if user is within rate limit for assignment attempts"""
        current_time = time.time()
        last_attempt = _assignment_attempts.get(user_id, 0)

        # Debug logging
        logger.info(
            f"Rate limit check for user {user_id}: current_time={current_time}, "
            f"last_attempt={last_attempt}, diff={current_time - last_attempt}"
        )

        if current_time - last_attempt < _RATE_LIMIT_SECONDS:
            logger.warning(
                f"User {user_id} rate limited: {current_time - last_attempt}s < "
                f"{_RATE_LIMIT_SECONDS}s"
            )
            return False  # Rate limited

        _assignment_attempts[user_id] = current_time
        logger.info(
            f"User {user_id} rate limit check passed, recording timestamp "
            f"{current_time}"
        )
        return True

    def _cleanup_old_attempts(self):
        """Clean up old assignment attempts from memory"""
        current_time = time.time()
        expired_users = [
            user_id
            for user_id, timestamp in _assignment_attempts.items()
            if current_time - timestamp > _RATE_LIMIT_SECONDS * 2
        ]
        for user_id in expired_users:
            del _assignment_attempts[user_id]

    def clear_rate_limit_cache(self, user_id: int = None):
        """Manually clear rate limit cache for debugging"""
        if user_id:
            if user_id in _assignment_attempts:
                del _assignment_attempts[user_id]
                logger.info(f"Cleared rate limit for user {user_id}")
            else:
                logger.info(f"User {user_id} not in rate limit cache")
        else:
            count = len(_assignment_attempts)
            _assignment_attempts.clear()
            logger.info(f"Cleared entire rate limit cache ({count} entries)")

    async def assign_premium_user(self, user_id: int) -> Dict[str, any]:
        """
        Assign a premium user to a dedicated instance with race condition prevention.

        Args:
            user_id: The ID of the premium user to assign

        Returns:
            Dict containing assignment result:
            - success: bool
            - message: str
            - instance_id: str (if successful)
            - retry_after: int (if 202 response)
        """
        try:
            # Clean up old attempts periodically
            self._cleanup_old_attempts()

            # TEMPORARY: For debugging, clear any existing rate limit for this user
            # if they were rate limited more than 5 seconds ago
            current_time = time.time()
            last_attempt = _assignment_attempts.get(user_id, 0)
            if last_attempt > 0 and current_time - last_attempt > 5:
                logger.info(
                    f"Clearing stale rate limit for user {user_id} (last attempt "
                    f"was {current_time - last_attempt}s ago)"
                )
                del _assignment_attempts[user_id]

            # Check rate limiting to prevent concurrent calls
            if not self._check_rate_limit(user_id):
                logger.warning(f"Rate limited assignment attempt for user {user_id}")
                return {
                    "success": False,
                    "message": f"Assignment request too frequent. "
                    f"Please wait {_RATE_LIMIT_SECONDS} seconds.",
                    "requires_retry": False,
                }

            # Local development mode - skip Lambda call if running on localhost
            mysql_server = os.environ.get("MYSQL_SERVER", "")
            database_url = os.environ.get("DATABASE_URL", "")
            if (
                "localhost" in mysql_server
                or "localhost" in database_url
                or "127.0.0.1" in database_url
            ):
                logger.info(
                    f"Local dev mode: Simulating premium assignment for user {user_id}"
                )
                return {
                    "success": True,
                    "message": "Local development - premium assignment simulated",
                    "instance_id": "local-dev-instance",
                }

            logger.info(f"Assigning premium user {user_id} to dedicated instance")

            # Prepare the assignment request
            # Format as Lambda expects from API Gateway (with body field)
            payload = {
                "httpMethod": "POST",
                "body": json.dumps(
                    {"action": "assign", "user_id": str(user_id), "tier": "premium"}
                ),
            }

            # Call the premium manager Lambda function
            lambda_client = self._get_lambda_client()

            # Use asyncio to run the synchronous boto3 call
            def invoke_lambda():
                response = lambda_client.invoke(
                    FunctionName=self.premium_manager_function_name,
                    InvocationType="RequestResponse",
                    Payload=json.dumps(payload),
                )
                return response

            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, invoke_lambda)

            # Parse the response
            response_payload = json.loads(response["Payload"].read())
            status_code = response_payload.get("statusCode", 500)

            if status_code == 200:
                # Successful assignment
                body = json.loads(response_payload.get("body", "{}"))
                logger.info(
                    f"Successfully assigned premium user {user_id} to "
                    f"instance {body.get('instance_id')}"
                )
                logger.info(f"Lambda response body: {body}")
                logger.info(f"is_shared from Lambda: {body.get('is_shared')}")
                logger.info(
                    f"assignment_source from Lambda: {body.get('assignment_source')}"
                )
                result = {
                    "success": True,
                    "message": body.get("message", "Assignment successful"),
                    "instance_id": body.get("instance_id"),
                    "target_group_arn": body.get("target_group_arn"),
                    "rule_arn": body.get("rule_arn"),
                    "is_shared": body.get("is_shared", False),
                    "assignment_source": body.get("assignment_source"),
                }
                logger.info(f"Returning result: {result}")
                return result

            elif status_code == 202:
                # Instance starting from stopped state, need to wait
                body = json.loads(response_payload.get("body", "{}"))
                retry_after = body.get(
                    "retry_after", 120
                )  # Reduced wait time for stopped instances
                logger.info(
                    f"Premium standby instance starting for user {user_id}, "
                    f"retry in {retry_after} seconds"
                )
                return {
                    "success": False,
                    "message": body.get("message", "Starting standby instance"),
                    "retry_after": retry_after,
                    "requires_retry": True,
                }

            else:
                # Assignment failed
                body = json.loads(response_payload.get("body", "{}"))
                error_message = body.get("error", "Assignment failed")
                logger.error(
                    f"Failed to assign premium user {user_id}: {error_message}"
                )
                return {
                    "success": False,
                    "message": error_message,
                    "requires_retry": False,
                }

        except Exception as e:
            logger.error(f"Error assigning premium user {user_id}: {str(e)}")
            # Clear rate limiting on errors to prevent users getting permanently stuck
            if user_id in _assignment_attempts:
                del _assignment_attempts[user_id]
                logger.info(
                    f"Cleared rate limiting cache for user {user_id} due to error"
                )
            return {
                "success": False,
                "message": f"Assignment error: {str(e)}",
                "requires_retry": False,
            }

    async def release_premium_user(self, user_id: int) -> Dict[str, any]:
        """
        Release a premium user from their assigned instance and clear rate limiting.

        Args:
            user_id: The ID of the premium user to release

        Returns:
            Dict containing release result:
            - success: bool
            - message: str
            - released_instance: str (if successful)
        """
        try:
            logger.info(f"Releasing premium user {user_id} from assigned instance")

            # Clear rate limiting for this user on release/logout
            if user_id in _assignment_attempts:
                del _assignment_attempts[user_id]
                logger.info(f"Cleared rate limiting cache for user {user_id}")

            # Prepare the release request
            # Format as Lambda expects from API Gateway (with body field)
            payload = {
                "httpMethod": "POST",
                "body": json.dumps(
                    {"action": "release", "user_id": str(user_id), "tier": "premium"}
                ),
            }

            # Call the premium manager Lambda function
            lambda_client = self._get_lambda_client()

            # Use asyncio to run the synchronous boto3 call
            def invoke_lambda():
                response = lambda_client.invoke(
                    FunctionName=self.premium_manager_function_name,
                    InvocationType="RequestResponse",
                    Payload=json.dumps(payload),
                )
                return response

            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, invoke_lambda)

            # Parse the response
            response_payload = json.loads(response["Payload"].read())
            status_code = response_payload.get("statusCode", 500)

            # Lambda always returns 200 for release operations to prevent
            # blocking logout
            if status_code == 200:
                body = json.loads(response_payload.get("body", "{}"))

                # Check if the Lambda indicates success or warnings
                lambda_success = body.get("success", True)
                warnings = body.get("warnings", [])
                error = body.get("error")

                if lambda_success:
                    logger.info(
                        f"Successfully released premium user {user_id} from "
                        f"instance {body.get('released_instance')}"
                    )
                    if warnings:
                        logger.warning(
                            f"Release completed with {len(warnings)} warnings: "
                            f"{warnings}"
                        )
                else:
                    logger.warning(
                        f"Release completed with issues for user {user_id}: "
                        f"{error or 'Unknown error'}"
                    )

                # Always return success for release operations - don't block logout
                return {
                    "success": True,  # Always True to not block user logout
                    "message": body.get("message", "Release completed"),
                    "released_instance": body.get("released_instance"),
                    "warnings": warnings,
                    "lambda_success": lambda_success,
                }
            else:
                # This shouldn't happen with new Lambda logic, but handle gracefully
                body = json.loads(response_payload.get("body", "{}"))
                error_message = body.get("error", "Release failed")
                logger.warning(
                    f"Unexpected status code {status_code} from release Lambda "
                    f"for user {user_id}: {error_message}"
                )
                # Still return success to not block logout
                return {
                    "success": True,
                    "message": f"Release completed with warnings: {error_message}",
                    "warnings": [error_message],
                }

        except Exception as e:
            logger.error(f"Error releasing premium user {user_id}: {str(e)}")
            # Even on exceptions, return success to not block user logout
            # The user should be able to log out even if cleanup fails
            return {
                "success": True,
                "message": f"Release completed with errors: {str(e)}",
                "warnings": [f"Exception during release: {str(e)}"],
            }

    async def get_premium_user_status(self, user_id: int) -> Optional[Dict[str, any]]:
        """
        Get the current assignment status of a premium user.

        Args:
            user_id: The ID of the premium user

        Returns:
            Dict containing assignment status or None if not assigned
        """
        try:
            logger.info(f"Getting assignment status for premium user {user_id}")

            # Call the premium manager Lambda function with GET method simulation
            payload = {
                "httpMethod": "GET",
                "queryStringParameters": {"user_id": str(user_id)},
            }

            lambda_client = self._get_lambda_client()

            def invoke_lambda():
                response = lambda_client.invoke(
                    FunctionName=self.premium_manager_function_name,
                    InvocationType="RequestResponse",
                    Payload=json.dumps(payload),
                )
                return response

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, invoke_lambda)

            # Parse the response
            response_payload = json.loads(response["Payload"].read())
            status_code = response_payload.get("statusCode", 500)

            if status_code == 200:
                body = json.loads(response_payload.get("body", "{}"))
                return body
            elif status_code == 404:
                # User not assigned
                return None
            else:
                logger.error(
                    f"Failed to get status for premium user"
                    f"{user_id}: {response_payload}"
                )
                return None

        except Exception as e:
            logger.error(f"Error getting status for premium user {user_id}: {str(e)}")
            return None

    async def update_user_activity(self, user_id: int) -> Dict[str, any]:
        """
        Update activity timestamp for a premium user to prevent stale
        assignment cleanup.

        Args:
            user_id: The ID of the premium user

        Returns:
            Dict containing update result
        """
        try:
            logger.info(f"Updating activity timestamp for premium user {user_id}")

            # Prepare the activity update request
            # We'll use a special action for activity updates
            payload = {
                "httpMethod": "POST",
                "body": json.dumps(
                    {
                        "action": "update_activity",
                        "user_id": str(user_id),
                        "tier": "premium",
                    }
                ),
            }

            # Call the premium manager Lambda function
            lambda_client = self._get_lambda_client()

            def invoke_lambda():
                response = lambda_client.invoke(
                    FunctionName=self.premium_manager_function_name,
                    InvocationType="RequestResponse",
                    Payload=json.dumps(payload),
                )
                return response

            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, invoke_lambda)

            # Parse the response
            response_payload = json.loads(response["Payload"].read())
            status_code = response_payload.get("statusCode", 500)

            if status_code == 200:
                body = json.loads(response_payload.get("body", "{}"))
                logger.info(f"Successfully updated activity for premium user {user_id}")
                return {
                    "success": True,
                    "message": body.get("message", "Activity updated"),
                    "user_id": user_id,
                }
            else:
                body = json.loads(response_payload.get("body", "{}"))
                error_message = body.get("error", "Activity update failed")
                logger.warning(
                    f"Failed to update activity for user {user_id}: {error_message}"
                )
                return {"success": False, "message": error_message, "user_id": user_id}

        except Exception as e:
            logger.error(
                f"Error updating activity for premium user {user_id}: {str(e)}"
            )
            return {
                "success": False,
                "message": f"Activity update error: {str(e)}",
                "user_id": user_id,
            }


# Global instance
premium_assignment_service = PremiumAssignmentService()
