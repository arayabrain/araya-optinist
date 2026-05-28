"""
Premium Assignment Service

Handles automatic assignment of premium users to dedicated instances.
Integrates with the premium manager Lambda function to assign/release users.
"""

import asyncio
import json
import os
import time
from typing import TYPE_CHECKING, Dict, Optional

import boto3

if TYPE_CHECKING:
    from mypy_boto3_lambda import LambdaClient

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.subscription.constants import SubscriptionType
from studio.app.common.core.utils.config_handler import is_local_environment

logger = AppLogger.get_logger()

# Global in-memory cache for preventing concurrent assignment attempts
_assignment_attempts = {}
_RATE_LIMIT_SECONDS = 30


class PremiumStatusCheckError(Exception):
    pass


# Timeout and retry configuration for Lambda calls
LAMBDA_TIMEOUT_SECONDS = 60
LAMBDA_MAX_RETRIES = 2
LAMBDA_RETRY_BASE_DELAY_SECONDS = 2


class PremiumAssignmentService:
    """Service for handling premium user assignments to dedicated instances."""

    def __init__(self):
        self.lambda_client: "LambdaClient | None" = None
        env_prefix = os.environ.get("ENV_PREFIX", "subscr")
        self.premium_manager_function_name = os.environ.get(
            "PREMIUM_MANAGER_FUNCTION_NAME", f"{env_prefix}-premium-manager"
        )

    def _get_lambda_client(self):
        """Get or create Lambda client."""
        if not self.lambda_client:
            self.lambda_client = boto3.client("lambda")
        return self.lambda_client

    def _check_rate_limit(self, user_id: int) -> bool:
        """Check if user is within rate limit for assignment attempts"""
        can_assign, _ = self.can_assign_premium(user_id)
        if can_assign:
            _assignment_attempts[user_id] = time.time()
            logger.info(f"User {user_id} rate limit check passed, recording timestamp")
        return can_assign

    def can_assign_premium(self, user_id: int) -> tuple:
        """
        Check if user can request premium assignment.

        Returns:
            tuple: (can_assign: bool, seconds_remaining: int)
        """
        current_time = time.time()
        last_attempt = _assignment_attempts.get(user_id, 0)
        elapsed = current_time - last_attempt

        if elapsed < _RATE_LIMIT_SECONDS:
            remaining = int(_RATE_LIMIT_SECONDS - elapsed)
            logger.warning(
                f"User {user_id} rate limited: {elapsed:.1f}s < "
                f"{_RATE_LIMIT_SECONDS}s, {remaining}s remaining"
            )
            return False, remaining

        return True, 0

    def _cleanup_old_attempts(self):
        """Clean up old assignment attempts from memory"""
        current_time = time.time()
        expired_users = [
            user_id
            for user_id, timestamp in _assignment_attempts.items()
            # Keep entries for 2x rate limit (60s) to ensure rate limiting works
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

    async def assign_premium_user(self, user_id: int, user_uid: str) -> Dict[str, any]:
        """
        Assign a premium user to a dedicated instance with race condition prevention.

        Args:
            user_id: The database ID of the premium user (for internal tracking)
            user_uid: The Firebase UID of the premium user (sent to Lambda)

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

            # Check rate limiting to prevent concurrent calls
            can_assign, seconds_remaining = self.can_assign_premium(user_id)
            if not can_assign:
                logger.warning(f"Rate limited assignment attempt for user {user_id}")
                return {
                    "success": False,
                    "message": f"Assignment request too frequent. "
                    f"Please wait {seconds_remaining} seconds.",
                    "requires_retry": False,
                    "retry_after": seconds_remaining,
                }
            # Record the attempt timestamp
            _assignment_attempts[user_id] = time.time()

            # Local development mode - skip Lambda call if running on localhost
            if is_local_environment():
                logger.info(
                    f"Local dev mode: Simulating premium assignment for user {user_id}"
                )
                return {
                    "success": True,
                    "message": "Local development - premium assignment simulated",
                    "instance_id": "local-dev-instance",
                }

            logger.info(
                f"Assigning premium user "
                f"{user_id} (uid: {user_uid}) to dedicated instance"
            )

            # Prepare the assignment request
            # Format as Lambda expects from API Gateway (with body field)
            payload = {
                "httpMethod": "POST",
                "body": json.dumps(
                    {
                        "action": "assign",
                        "user_id": user_uid,
                        "tier": SubscriptionType.PREMIUM.value,
                    }
                ),
            }

            # Call the premium manager Lambda with timeout and retry
            lambda_client = self._get_lambda_client()

            def invoke_lambda():
                response = lambda_client.invoke(
                    FunctionName=self.premium_manager_function_name,
                    InvocationType="RequestResponse",
                    Payload=json.dumps(payload),
                )
                return response

            # Retry loop with exponential backoff for transient failures
            loop = asyncio.get_event_loop()

            for attempt in range(LAMBDA_MAX_RETRIES + 1):
                try:
                    # Run with timeout to prevent hanging indefinitely
                    response = await asyncio.wait_for(
                        loop.run_in_executor(None, invoke_lambda),
                        timeout=LAMBDA_TIMEOUT_SECONDS,
                    )
                    break  # Success - exit retry loop
                except asyncio.TimeoutError:
                    if attempt < LAMBDA_MAX_RETRIES:
                        delay = LAMBDA_RETRY_BASE_DELAY_SECONDS * (2**attempt)
                        logger.warning(
                            f"Assignment timeout for user {user_id}, "
                            f"attempt {attempt + 1}/{LAMBDA_MAX_RETRIES + 1}, "
                            f"retrying in {delay}s"
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            f"Assignment timed out for user {user_id} "
                            f"after {LAMBDA_MAX_RETRIES + 1} attempts"
                        )
                        return {
                            "success": False,
                            "message": "Premium assignment timed out. Please try "
                            "again in a few moments.",
                            "requires_retry": True,
                            "retry_after": 30,
                        }
                except Exception as e:
                    if attempt < LAMBDA_MAX_RETRIES:
                        delay = LAMBDA_RETRY_BASE_DELAY_SECONDS * (2**attempt)
                        logger.warning(
                            f"Assignment error for user {user_id}: {e}, "
                            f"attempt {attempt + 1}/{LAMBDA_MAX_RETRIES + 1}, "
                            f"retrying in {delay}s"
                        )
                        await asyncio.sleep(delay)
                    else:
                        raise

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
                logger.debug(f"Lambda response body: {body}")
                logger.debug(f"is_shared from Lambda: {body.get('is_shared')}")
                logger.debug(
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
                logger.debug(f"Returning result: {result}")
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

            elif status_code == 409:
                # Another Lambda invocation is already assigning this user.
                # The lock holder likely completed by now; retry promptly.
                body = json.loads(response_payload.get("body", "{}"))
                logger.warning(
                    f"Concurrent assignment conflict for user {user_id}, " f"will retry"
                )
                return {
                    "success": False,
                    "message": body.get(
                        "message",
                        "Another assignment in progress. Please retry.",
                    ),
                    "requires_retry": True,
                    "retry_after": 5,
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

    async def release_premium_user(
        self, user_id: int, user_uid: str, *, hard: bool = False
    ) -> Dict[str, any]:
        """
        Release a premium user from their assigned instance and clear rate limiting.

        Args:
            user_id: The database ID of the premium user (for internal tracking)
            user_uid: The Firebase UID of the premium user (sent to Lambda)
            hard: If True, immediately delete assignment + ALB resources.
                  If False (default), soft-release with grace period so a
                  page refresh can restore the assignment instantly.

        Returns:
            Dict containing release result:
            - success: bool
            - message: str
            - released_instance: str (if successful)
        """
        try:
            release_type = "hard" if hard else "soft"
            logger.info(
                f"Releasing ({release_type}) premium user "
                f"{user_id} (uid: {user_uid}) from assigned instance"
            )

            # NOTE: Rate limit cache is NOT cleared on release/logout.
            # This prevents rapid re-login attempts. The cache expires naturally
            # after _RATE_LIMIT_SECONDS (30s).

            # Prepare the release request
            # Format as Lambda expects from API Gateway (with body field)
            payload = {
                "httpMethod": "POST",
                "body": json.dumps(
                    {
                        "action": "release",
                        "user_id": user_uid,
                        "tier": SubscriptionType.PREMIUM.value,
                        "hard": hard,
                    }
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
                    released = body.get("released_instance")
                    if released:
                        logger.info(
                            f"Released premium user {user_id} "
                            f"from instance {released}"
                        )
                    else:
                        logger.info(
                            f"Release for premium user {user_id}: "
                            f"no assignment found (already released)"
                        )
                    if warnings:
                        logger.warning(
                            f"Release completed with "
                            f"{len(warnings)} warnings: "
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

    async def get_premium_user_status(
        self, user_id: int, user_uid: str
    ) -> Optional[Dict[str, any]]:
        """
        Get the current assignment status of a premium user.

        Args:
            user_id: The database ID of the premium user (for logging)
            user_uid: The Firebase UID of the premium user (sent to Lambda)

        Returns:
            Dict containing assignment status or None if not assigned
        """
        try:
            logger.info(
                f"Getting assignment status for "
                f"premium user {user_id} (uid: {user_uid})"
            )

            # Call the premium manager Lambda function with GET method simulation
            payload = {
                "httpMethod": "GET",
                "queryStringParameters": {"user_id": user_uid},
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
                logger.debug(
                    "Premium status result for user %s: %s",
                    user_id,
                    body,
                )
                return body
            elif status_code == 404:
                # User not assigned
                return None
            else:
                logger.error(
                    f"Failed to get status for premium user"
                    f"{user_id}: {response_payload}"
                )
                raise PremiumStatusCheckError(
                    f"Lambda returned {status_code} for user {user_id}"
                )

        except PremiumStatusCheckError:
            raise
        except Exception as e:
            logger.error(f"Error getting status for premium user {user_id}: {str(e)}")
            raise PremiumStatusCheckError(
                f"Status check failed for user {user_id}: {e}"
            ) from e

    async def update_user_activity(self, user_id: int, user_uid: str) -> Dict[str, any]:
        """
        Update activity timestamp for a premium user to prevent stale
        assignment cleanup.

        Args:
            user_id: The database ID of the premium user (for logging)
            user_uid: The Firebase UID of the premium user (sent to Lambda)

        Returns:
            Dict containing update result
        """
        try:
            logger.info(
                f"Updating activity timestamp for "
                f"premium user {user_id} (uid: {user_uid})"
            )

            # Prepare the activity update request
            # We'll use a special action for activity updates
            payload = {
                "httpMethod": "POST",
                "body": json.dumps(
                    {
                        "action": "update_activity",
                        "user_id": user_uid,
                        "tier": SubscriptionType.PREMIUM.value,
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
