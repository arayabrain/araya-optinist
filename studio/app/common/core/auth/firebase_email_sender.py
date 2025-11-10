"""
Firebase email sender using REST API.

This module sends verification emails using Firebase's built-in email service
without requiring SMTP configuration.
"""

import json
import logging
import os

import requests
from firebase_admin import auth as firebase_auth

from studio.app.dir_path import DIRPATH

logger = logging.getLogger(__name__)

# Load Firebase API key from existing firebase_config.json
FIREBASE_API_KEY = None
try:
    with open(DIRPATH.FIREBASE_CONFIG_PATH) as f:
        firebase_config = json.load(f)
        FIREBASE_API_KEY = firebase_config.get("apiKey")
        if FIREBASE_API_KEY:
            logger.info("Firebase API key loaded from firebase_config.json")
except FileNotFoundError:
    logger.warning("firebase_config.json not found. Email sending will be disabled.")
except Exception as e:
    logger.error(f"Error loading firebase_config.json: {e}")

# Allow override from environment variable (optional)
if os.getenv("FIREBASE_API_KEY"):
    FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY")
    logger.info("Firebase API key overridden from environment variable")

# Firebase REST API endpoint for sending OOB codes
FIREBASE_SEND_OOB_URL = "https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode"


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
        base_url = "https://identitytoolkit.googleapis.com/v1/accounts"
        exchange_url = f"{base_url}:signInWithCustomToken?key={FIREBASE_API_KEY}"
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
