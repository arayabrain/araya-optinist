#!/usr/bin/env python3
"""
Test User Configuration Loader (Utility Module)

NOTE: This is NOT a test suite - it's a shared configuration utility module.
      Running it directly will complete silently (no output = success).

WHERE TO RUN:
- Local development machine - Used by other test scripts
- Cloud ECS container - Used by other test scripts
- Imported by other testing scripts, not run standalone

WHAT IT DOES:
Provides centralized test user configuration loading from multiple sources:
1. TEST_USERS_CONFIG environment variable (JSON format from Terraform)
2. terraform.tfvars file (HCL format)
3. .env file (key=value format)
4. Individual environment variables (PREMIUM_USER_EMAIL, FREE_USER_EMAIL, etc.)

USED BY:
- get_jwt_tokens.py - Loads test users for JWT token generation
- create_test_users.py - Loads test users for database creation
- priority_queue_test.py - Loads test users for priority queue testing
- Other testing and setup scripts

CONFIGURATION SOURCES (in order of precedence):
1. TEST_USERS_CONFIG env var (highest priority)
2. terraform.tfvars
3. .env file
4. Individual env vars (lowest priority)

FUNCTIONS:
- load_test_users_unified() - Returns raw config (dict or list)
- load_test_users_for_jwt() - Returns dict format {premium: {...}, free: {...}}
- load_test_users_for_db() - Returns list format [{...}, {...}]
- parse_terraform_test_users() - Parses terraform.tfvars
- parse_env_test_users() - Parses .env file
- parse_env_vars_test_users() - Parses environment variables
- print_configuration_help() - Prints setup instructions

HOW TO USE:
  from test_user_config import load_test_users_for_jwt
  users = load_test_users_for_jwt()
  premium_user = users['premium']
  free_user = users['free']

EXPECTED RESULT when run directly:
  No output (silent success)
"""

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Union


def get_project_root() -> Path:
    """Get the project root directory."""
    return Path(__file__).parent.parent.parent


def load_test_users_for_jwt() -> Dict[str, Dict]:
    """Load test user configuration in format expected by
    JWT scripts (dict with premium/free keys)."""
    config = load_test_users_unified()

    if isinstance(config, list):
        # Convert list format to dict format
        users = {}
        for user_data in config:
            if "email" in user_data:
                if "premium" in user_data["email"]:
                    users["premium"] = user_data
                elif "free" in user_data["email"]:
                    users["free"] = user_data
        return users

    return config or {}


def load_test_users_for_db() -> List[Dict]:
    """Load test user configuration in format expected
    by database scripts (list format)."""
    config = load_test_users_unified()

    if isinstance(config, dict):
        # Convert dict format to list format
        users = []
        if "premium" in config:
            users.append(config["premium"])
        if "free" in config:
            users.append(config["free"])
        return users

    return config or []


def load_test_users_unified() -> Union[List[Dict], Dict[str, Dict], None]:
    """Load test user configuration from various sources,
    returning the raw format found."""

    # Method 1: Try TEST_USERS_CONFIG environment variable (JSON format from Terraform)
    test_users_json = os.getenv("TEST_USERS_CONFIG")
    if test_users_json:
        try:
            users = json.loads(test_users_json)
            print("Loaded test users from TEST_USERS_CONFIG environment variable")
            return users
        except json.JSONDecodeError as e:
            print(f"Failed to parse TEST_USERS_CONFIG: {e}")

    # Method 2: Try terraform.tfvars (returns list format)
    terraform_path = (
        get_project_root() / "studio" / "config" / "terraform" / "terraform.tfvars"
    )
    if terraform_path.exists():
        try:
            users = parse_terraform_test_users(terraform_path)
            if users:
                print("Loaded test users from terraform.tfvars")
                return users
        except Exception as e:
            print(f"Failed to parse terraform.tfvars: {e}")

    # Method 3: Try .env file
    env_path = get_project_root() / ".env"
    if env_path.exists():
        try:
            users = parse_env_test_users(env_path)
            if users:
                print("Loaded test users from .env")
                return users
        except Exception as e:
            print(f"Failed to parse .env: {e}")

    # Method 4: Try individual environment variables
    try:
        users = parse_env_vars_test_users()
        if users:
            print("Loaded test users from environment variables")
            return users
    except Exception as e:
        print(f"Failed to load from environment variables: {e}")

    return None


def parse_terraform_test_users(terraform_path: Path) -> Optional[List[Dict]]:
    """Parse test users from terraform.tfvars file, return list format."""
    with open(terraform_path, "r") as f:
        content = f.read()

    # Find the test_users block
    test_users_pattern = r"test_users\s*=\s*\[(.*?)\]"
    match = re.search(test_users_pattern, content, re.DOTALL)
    if not match:
        return None

    test_users_block = match.group(1)

    # Parse individual user blocks
    user_pattern = r"\{([^}]+)\}"
    user_matches = re.findall(user_pattern, test_users_block)

    users = []
    for user_block in user_matches:
        user_data = {}

        # Parse key-value pairs
        field_patterns = {
            "email": r'email\s*=\s*"([^"]+)"',
            "name": r'name\s*=\s*"([^"]+)"',
            "password": r'password\s*=\s*"([^"]+)"',
            "firebase_uid": r'firebase_uid\s*=\s*"([^"]+)"',
            "subscription_plan_id": r"subscription_plan_id\s*=\s*(\d+)",
            "role_id": r"role_id\s*=\s*(\d+)",
            "storage_quota_gb": r"storage_quota_gb\s*=\s*(\d+)",
        }

        for field, pattern in field_patterns.items():
            match = re.search(pattern, user_block)
            if match:
                value = match.group(1)
                if field in ["subscription_plan_id", "role_id", "storage_quota_gb"]:
                    value = int(value)
                user_data[field] = value

        if "email" in user_data:
            users.append(user_data)

    return users if users else None


def parse_env_test_users(env_path: Path) -> Optional[Dict[str, Dict]]:
    """Parse test users from .env file, return dict format."""
    # Load .env file manually (simple parser)
    env_vars = {}
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env_vars[key.strip()] = value.strip().strip("\"'")

    users = {}

    # Premium user
    if all(key in env_vars for key in ["PREMIUM_USER_EMAIL", "PREMIUM_USER_PASSWORD"]):
        users["premium"] = {
            "email": env_vars["PREMIUM_USER_EMAIL"],
            "name": env_vars.get("PREMIUM_USER_NAME", "Premium Test User"),
            "password": env_vars["PREMIUM_USER_PASSWORD"],
            "firebase_uid": env_vars.get("PREMIUM_USER_FIREBASE_UID", ""),
            "subscription_plan_id": int(env_vars.get("PREMIUM_USER_PLAN_ID", "2")),
            "role_id": int(env_vars.get("PREMIUM_USER_ROLE_ID", "20")),
            "storage_quota_gb": int(env_vars.get("PREMIUM_USER_STORAGE_GB", "200")),
        }

    # Free user
    if all(key in env_vars for key in ["FREE_USER_EMAIL", "FREE_USER_PASSWORD"]):
        users["free"] = {
            "email": env_vars["FREE_USER_EMAIL"],
            "name": env_vars.get("FREE_USER_NAME", "Free Test User"),
            "password": env_vars["FREE_USER_PASSWORD"],
            "firebase_uid": env_vars.get("FREE_USER_FIREBASE_UID", ""),
            "subscription_plan_id": int(env_vars.get("FREE_USER_PLAN_ID", "1")),
            "role_id": int(env_vars.get("FREE_USER_ROLE_ID", "20")),
            "storage_quota_gb": int(env_vars.get("FREE_USER_STORAGE_GB", "5")),
        }

    return users if len(users) >= 2 else None


def parse_env_vars_test_users() -> Optional[Dict[str, Dict]]:
    """Parse test users from environment variables, return dict format."""
    users = {}

    # Premium user
    if all(
        key in os.environ for key in ["PREMIUM_USER_EMAIL", "PREMIUM_USER_PASSWORD"]
    ):
        users["premium"] = {
            "email": os.environ["PREMIUM_USER_EMAIL"],
            "name": os.environ.get("PREMIUM_USER_NAME", "Premium Test User"),
            "password": os.environ["PREMIUM_USER_PASSWORD"],
            "firebase_uid": os.environ.get("PREMIUM_USER_FIREBASE_UID", ""),
            "subscription_plan_id": int(os.environ.get("PREMIUM_USER_PLAN_ID", "2")),
            "role_id": int(os.environ.get("PREMIUM_USER_ROLE_ID", "20")),
            "storage_quota_gb": int(os.environ.get("PREMIUM_USER_STORAGE_GB", "200")),
        }

    # Free user
    if all(key in os.environ for key in ["FREE_USER_EMAIL", "FREE_USER_PASSWORD"]):
        users["free"] = {
            "email": os.environ["FREE_USER_EMAIL"],
            "name": os.environ.get("FREE_USER_NAME", "Free Test User"),
            "password": os.environ["FREE_USER_PASSWORD"],
            "firebase_uid": os.environ.get("FREE_USER_FIREBASE_UID", ""),
            "subscription_plan_id": int(os.environ.get("FREE_USER_PLAN_ID", "1")),
            "role_id": int(os.environ.get("FREE_USER_ROLE_ID", "20")),
            "storage_quota_gb": int(os.environ.get("FREE_USER_STORAGE_GB", "5")),
        }

    return users if len(users) >= 2 else None


def print_configuration_help():
    """Print help message for configuring test users."""
    print("No test user configuration found!")
    print("Please configure test users using one of these methods:")
    print("1. Set TEST_USERS_CONFIG env var (JSON format, used by Terraform)")
    print("2. Create .env file: cp .env.example .env (then edit)")
    print(
        "3. Set environment variables (PREMIUM_USER_EMAIL, PREMIUM_USER_PASSWORD, etc.)"
    )
    print("4. Ensure terraform.tfvars exists in studio/config/terraform/")
    print("See README_PRIORITY_TESTING.md for detailed setup instructions.")
