"""
Firebase email sender using REST API.

This module sends verification emails using Firebase's built-in email service
without requiring SMTP configuration.
"""

import os

import requests
from firebase_admin import auth as firebase_auth

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.utils.file_reader import JsonReader
from studio.app.dir_path import DIRPATH

logger = AppLogger.get_logger()


# Load Firebase API key from existing firebase_config.json
def _load_firebase_api_key() -> str | None:
    """Load Firebase API key from firebase_config.json."""
    if not os.path.exists(DIRPATH.FIREBASE_CONFIG_PATH):
        logger.warning(
            "firebase_config.json not found. Email sending will be disabled."
        )
        return None

    try:
        firebase_config = JsonReader.read(DIRPATH.FIREBASE_CONFIG_PATH)
        api_key = firebase_config.get("apiKey")
        if api_key:
            logger.info("Firebase API key loaded from firebase_config.json")
        return api_key
    except Exception as e:
        logger.error(f"Error loading firebase_config.json: {e}")
        return None


FIREBASE_API_KEY = _load_firebase_api_key()

# Firebase REST API endpoint for sending OOB codes
FIREBASE_SEND_OOB_URL = "https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode"

FIREBASE_REST_API_BASE_URL = "https://identitytoolkit.googleapis.com/v1/accounts"


def send_verification_email_via_firebase(email: str) -> bool:
    """
    Send verification email using Firebase's built-in email service via REST API.

    This uses Firebase's REST API to trigger the verification email.
    Firebase will use the template configured in Firebase Console.

    Args:
        email: User's email address

    Returns:
        bool: True if email was sent successfully

    Raises:
        Exception: If email sending fails
    """
    if not FIREBASE_API_KEY:
        raise ValueError(
            "FIREBASE_API_KEY not set in firebase_config.json. "
            "Make sure your firebase_config.json file has the 'apiKey' field."
        )

    try:
        # Get the user's ID token first (required for sending verification email)
        # We'll use Firebase Admin SDK to create a custom token, then exchange it
        firebase_user = firebase_auth.get_user_by_email(email)
        custom_token = firebase_auth.create_custom_token(firebase_user.uid)

        # Exchange custom token for ID token via Firebase REST API
        exchange_url = (
            f"{FIREBASE_REST_API_BASE_URL}:signInWithCustomToken?key={FIREBASE_API_KEY}"
        )
        exchange_response = requests.post(
            exchange_url,
            json={"token": custom_token.decode("utf-8"), "returnSecureToken": True},
        )
        exchange_response.raise_for_status()
        id_token = exchange_response.json()["idToken"]

        # Now send the verification email using the ID token
        send_url = f"{FIREBASE_SEND_OOB_URL}?key={FIREBASE_API_KEY}"
        send_response = requests.post(
            send_url,
            json={"requestType": "VERIFY_EMAIL", "idToken": id_token},
        )
        send_response.raise_for_status()

        logger.info(f"Verification email sent to {email} via Firebase")
        return True

    except requests.exceptions.RequestException as e:
        logger.error(f"Firebase REST API error: {e}", exc_info=True)
        if hasattr(e, "response") and e.response:
            logger.error(f"Response: {e.response.text}")
        raise Exception(f"Failed to send verification email via Firebase: {e}")
    except Exception as e:
        logger.error(f"Failed to send verification email: {e}", exc_info=True)
        raise


def send_password_reset_email_via_firebase(email: str) -> bool:
    """
    Send password reset email using Firebase's built-in email service.

    Args:
        email: User's email address

    Returns:
        bool: True if email was sent successfully

    Raises:
        Exception: If email sending fails
    """
    if not FIREBASE_API_KEY:
        raise ValueError(
            "FIREBASE_API_KEY not set in firebase_config.json. "
            "Make sure your firebase_config.json file has the 'apiKey' field."
        )

    try:
        send_url = f"{FIREBASE_SEND_OOB_URL}?key={FIREBASE_API_KEY}"
        send_response = requests.post(
            send_url, json={"requestType": "PASSWORD_RESET", "email": email}
        )
        send_response.raise_for_status()

        logger.info(f"Password reset email sent to {email} via Firebase")
        return True

    except requests.exceptions.RequestException as e:
        logger.error(f"Firebase REST API error: {e}", exc_info=True)
        if hasattr(e, "response") and e.response:
            logger.error(f"Response: {e.response.text}")
        raise Exception(f"Failed to send password reset email via Firebase: {e}")
    except Exception as e:
        logger.error(f"Failed to send password reset email: {e}", exc_info=True)
        raise
