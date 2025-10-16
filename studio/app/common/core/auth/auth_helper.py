"""
Authentication helper functions for extracting user identity from tokens

This module provides shared utilities for extracting user_id (uid) from
authentication tokens, supporting both Firebase and JWT authentication methods.
"""

from typing import Optional, Tuple

from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials
from firebase_admin import auth as firebase_auth

from studio.app.common.core.auth.auth_config import AUTH_CONFIG
from studio.app.common.core.auth.security import validate_access_token


def extract_uid_from_firebase_credential(
    credential: HTTPAuthorizationCredentials,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract user_id (uid) from Firebase authentication credential

    Args:
        credential: HTTPAuthorizationCredentials from FastAPI

    Returns:
        Tuple of (uid, error_message). If successful, error_message is None.
    """
    try:
        user = firebase_auth.verify_id_token(credential.credentials)
        uid = user.get("uid")
        return uid, None
    except Exception as e:
        return None, f"Firebase token validation failed: {e}"


def extract_uid_from_jwt_token(ex_token: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract user_id (uid) from JWT ExToken

    Args:
        ex_token: JWT token string

    Returns:
        Tuple of (uid, error_message). If successful, error_message is None.
    """
    try:
        payload, err = validate_access_token(ex_token)
        if err is not None:
            return None, err
        if payload:
            uid = payload.get("sub")
            return uid, None
        return None, "Empty payload"
    except Exception as e:
        return None, f"JWT token validation failed: {e}"


def extract_uid_from_request(request: Request) -> Optional[str]:
    """
    Extract user_id (uid) from HTTP request based on configured authentication method

    This is a convenience function for middleware and other non-dependency contexts
    where you need to extract uid without raising exceptions.

    Note: This function directly accesses request.headers because it's designed for
    middleware context where FastAPI's Dependency Injection is not available.
    For route handlers, use extract_uid_from_firebase_credential() or
    extract_uid_from_jwt_token() with FastAPI's Depends(APIKeyHeader/HTTPBearer).

    Args:
        request: FastAPI Request object

    Returns:
        user_id if found, None otherwise (errors are silently ignored)
    """
    try:
        use_firebase_auth = AUTH_CONFIG.USE_FIREBASE_TOKEN

        if use_firebase_auth:
            # Firebase authentication via Bearer token
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header.replace("Bearer ", "")
                try:
                    user = firebase_auth.verify_id_token(token)
                    return user.get("uid")
                except Exception:
                    pass
        else:
            # JWT authentication via ExToken header
            ex_token = request.headers.get("ExToken")
            if ex_token:
                try:
                    payload, err = validate_access_token(ex_token)
                    if err is None and payload:
                        return payload.get("sub")
                except Exception:
                    pass

    except Exception:
        pass

    return None
