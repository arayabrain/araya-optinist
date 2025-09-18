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

        if current_time - last_attempt < _RATE_LIMIT_SECONDS:
            return False  # Rate limited

        _assignment_attempts[user_id] = current_time
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

            # Check rate limiting to prevent concurrent calls
            if not self._check_rate_limit(user_id):
                logger.warning(f"Rate limited assignment attempt for user {user_id}")
                return {
                    "success": False,
                    "message": f"Assignment request too frequent. "
                    f"Please wait {_RATE_LIMIT_SECONDS} seconds.",
                    "requires_retry": False,
                }

            logger.info(f"Assigning premium user {user_id} to dedicated instance")

            # Prepare the assignment request
            payload = {"action": "assign", "user_id": str(user_id), "tier": "premium"}

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
                return {
                    "success": True,
                    "message": body.get("message", "Assignment successful"),
                    "instance_id": body.get("instance_id"),
                    "target_group_arn": body.get("target_group_arn"),
                    "rule_arn": body.get("rule_arn"),
                }

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
            return {
                "success": False,
                "message": f"Assignment error: {str(e)}",
                "requires_retry": False,
            }

    async def release_premium_user(self, user_id: int) -> Dict[str, any]:
        """
        Release a premium user from their assigned instance.

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

            # Prepare the release request
            payload = {"action": "release", "user_id": str(user_id), "tier": "premium"}

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
                # Successful release
                body = json.loads(response_payload.get("body", "{}"))
                logger.info(
                    f"Successfully released premium user {user_id} from "
                    f"instance {body.get('released_instance')}"
                )
                return {
                    "success": True,
                    "message": body.get("message", "Release successful"),
                    "released_instance": body.get("released_instance"),
                }
            else:
                # Release failed
                body = json.loads(response_payload.get("body", "{}"))
                error_message = body.get("error", "Release failed")
                logger.error(
                    f"Failed to release premium user {user_id}: {error_message}"
                )
                return {"success": False, "message": error_message}

        except Exception as e:
            logger.error(f"Error releasing premium user {user_id}: {str(e)}")
            return {"success": False, "message": f"Release error: {str(e)}"}

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


# Global instance
premium_assignment_service = PremiumAssignmentService()
