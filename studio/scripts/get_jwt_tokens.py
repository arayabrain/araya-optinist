#!/usr/bin/env python3
"""
JWT Token Generation for Load Testing

RUNTIME ENVIRONMENT:
Can run locally (with local API server)
Can run on cloud (with proper API endpoints)
Requires API server to be running and accessible

Generates Firebase ID tokens for different user types (free, premium, admin)
for use in load testing. This module provides the missing dependency for load_test.py.

REQUIREMENTS:
- Firebase service account credentials (from Terraform or environment)
- Test users with Firebase UIDs (from Terraform outputs or test_user_config)
- firebase_admin and pyrebase4 Python packages
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

# Add parent directories to path for imports
sys.path.append(str(Path(__file__).parent.parent.parent))

try:
    import firebase_admin
    from firebase_admin import auth as firebase_auth, credentials
except ImportError:
    print("❌ Error: firebase_admin not installed")
    print("   Run: pip install firebase-admin")
    sys.exit(1)

try:
    from studio.app.common.core.auth import pyrebase_app
except ImportError:
    print("⚠️  Warning: Could not import pyrebase_app from studio")
    print("   Attempting direct pyrebase initialization...")
    pyrebase_app = None


def get_terraform_outputs(terraform_dir: str = "../config/terraform") -> Dict:
    """Get Terraform outputs including test_users"""
    try:
        result = subprocess.run(
            ["terraform", "output", "-json"],
            cwd=terraform_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to get Terraform outputs: {e.stderr}")
        return {}
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse Terraform outputs: {e}")
        return {}
    except FileNotFoundError:
        print(f"❌ Terraform not found. Make sure terraform is installed and in PATH")
        return {}


def initialize_firebase_admin(service_account_path: Optional[str] = None, terraform_dir: str = "../config/terraform") -> bool:
    """Initialize Firebase Admin SDK"""
    if firebase_admin._apps:
        print("✓ Firebase Admin SDK already initialized")
        return True

    # Try to get service account from various sources
    cred = None

    if service_account_path and os.path.exists(service_account_path):
        print(f"🔑 Using service account from: {service_account_path}")
        cred = credentials.Certificate(service_account_path)
    else:
        # Try environment variable
        service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY")
        if service_account_json:
            print("🔑 Using service account from FIREBASE_SERVICE_ACCOUNT_KEY env var")
            cred = credentials.Certificate(json.loads(service_account_json))
        else:
            # Try to get from Terraform tfvars file
            tfvars_path = os.path.join(terraform_dir, "terraform.tfvars")
            if os.path.exists(tfvars_path):
                try:
                    import re
                    with open(tfvars_path, 'r') as f:
                        content = f.read()

                    # Look for firebase_private_json variable
                    # Match: firebase_private_json = <<-EOT ... EOT or firebase_private_json = "..."
                    heredoc_match = re.search(r'firebase_private_json\s*=\s*<<-?(\w+)\s*(.*?)\s*\1', content, re.DOTALL)
                    if heredoc_match:
                        service_account_json = heredoc_match.group(2).strip()
                        print("🔑 Using service account from terraform.tfvars (heredoc)")
                        cred = credentials.Certificate(json.loads(service_account_json))
                    else:
                        # Try regular string format
                        string_match = re.search(r'firebase_private_json\s*=\s*["\'](.+?)["\']', content, re.DOTALL)
                        if string_match:
                            service_account_json = string_match.group(1)
                            print("🔑 Using service account from terraform.tfvars (string)")
                            cred = credentials.Certificate(json.loads(service_account_json))
                except Exception as e:
                    print(f"⚠️  Could not parse firebase_private_json from terraform.tfvars: {e}")

            # If still no cred, try default paths
            if not cred:
                default_paths = [
                    "studio/config/firebase-service-account.json",
                    "../config/firebase-service-account.json",
                    "firebase-service-account.json",
                ]
                for path in default_paths:
                    if os.path.exists(path):
                        print(f"🔑 Using service account from: {path}")
                        cred = credentials.Certificate(path)
                        break

    if not cred:
        print("❌ Firebase service account credentials not found!")
        print("   Please provide credentials via:")
        print("   1. --service-account-path argument")
        print("   2. FIREBASE_SERVICE_ACCOUNT_KEY environment variable")
        print("   3. firebase_private_json in terraform.tfvars")
        print("   4. Place firebase-service-account.json in studio/config/")
        return False

    try:
        firebase_admin.initialize_app(cred)
        print("✓ Firebase Admin SDK initialized")
        return True
    except Exception as e:
        print(f"❌ Failed to initialize Firebase Admin SDK: {e}")
        return False


def get_test_users_from_terraform(terraform_dir: str) -> Optional[list]:
    """Get test users from Terraform outputs"""
    outputs = get_terraform_outputs(terraform_dir)

    if "test_users" in outputs:
        test_users_data = outputs["test_users"].get("value", [])
        print(f"✓ Found {len(test_users_data)} test users from Terraform outputs")
        return test_users_data

    print("⚠️  test_users not found in Terraform outputs")
    print("   Make sure you've added the test_users output to main.tf and run 'terraform apply'")
    return None


def get_test_users_from_config() -> Optional[list]:
    """Fallback: Get test users from test_user_config module"""
    try:
        from test_user_config import load_test_users_for_db
        users = load_test_users_for_db()
        if users:
            print(f"✓ Found {len(users)} test users from test_user_config")
            return users
    except ImportError:
        print("⚠️  Could not import test_user_config")
    except Exception as e:
        print(f"⚠️  Error loading test users from config: {e}")

    return None


def create_firebase_id_token(firebase_uid: str, email: str) -> Optional[str]:
    """
    Create a Firebase ID token for a user

    Steps:
    1. Create custom token using Firebase Admin SDK
    2. Exchange custom token for ID token via Firebase REST API
    """
    try:
        # Step 1: Create custom token (server-side, signed by our service account)
        custom_token = firebase_auth.create_custom_token(firebase_uid)
        print(f"  ✓ Created custom token for {email}")

        # Step 2: Exchange for ID token (signed by Google)
        if pyrebase_app:
            # Use existing pyrebase instance
            user = pyrebase_app.auth().sign_in_with_custom_token(custom_token.decode())
            id_token = user['idToken']
            print(f"  ✓ Exchanged for ID token (expires in 1 hour)")
            return id_token
        else:
            # Direct Firebase REST API call as fallback
            import requests

            # Get Firebase project ID from service account
            firebase_app = firebase_admin.get_app()
            project_id = firebase_app.project_id

            # Firebase REST API endpoint
            url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken"
            params = {"key": os.getenv("FIREBASE_API_KEY", "")}

            if not params["key"]:
                print("  ⚠️  FIREBASE_API_KEY not set, trying without it...")
                # Try the v1 endpoint which might not require API key
                url = f"https://www.googleapis.com/identitytoolkit/v3/relyingparty/verifyCustomToken"
                params = {"key": "AIzaSyDummyKey"}  # Some endpoints accept any value

            response = requests.post(
                url,
                params=params,
                json={"token": custom_token.decode(), "returnSecureToken": True}
            )

            if response.status_code == 200:
                data = response.json()
                id_token = data['idToken']
                print(f"  ✓ Exchanged for ID token (expires in 1 hour)")
                return id_token
            else:
                print(f"  ❌ Failed to exchange custom token: {response.status_code}")
                print(f"     Response: {response.text}")
                return None

    except Exception as e:
        print(f"  ❌ Error creating ID token for {email}: {e}")
        return None


def generate_jwt_tokens(
    environment: str = "local",
    api_url: str = None,
    terraform_dir: str = "../config/terraform",
    service_account_path: str = None,
) -> Optional[Dict[str, str]]:
    """
    Generate Firebase ID tokens for load testing

    Args:
        environment: Environment type ("local" or "cloud")
        api_url: API base URL (for reference only, not used in token generation)
        terraform_dir: Path to Terraform directory
        service_account_path: Path to Firebase service account JSON file

    Returns:
        Dict containing tokens for different user types
    """
    print(f"🔑 Generating Firebase ID tokens for {environment} environment")

    if api_url:
        print(f"🌐 API URL: {api_url}")

    # Initialize Firebase Admin SDK
    if not initialize_firebase_admin(service_account_path, terraform_dir):
        return None

    # Get test users
    test_users = get_test_users_from_terraform(terraform_dir)
    if not test_users:
        print("⚠️  Trying fallback: test_user_config module...")
        test_users = get_test_users_from_config()

    if not test_users:
        print("❌ No test users found!")
        return None

    # Generate token only for free user
    tokens = {}
    free_user = None

    # Find the free user
    for user_data in test_users:
        email = user_data.get("email", "")
        if "free" in email.lower() and "optinist_test_user_free" in email.lower():
            free_user = user_data
            break

    if not free_user:
        print("❌ Free user (optinist_test_user_free@araya.org) not found!")
        return None

    email = free_user.get("email", "")
    firebase_uid = free_user.get("firebase_uid", "")

    if not firebase_uid:
        print(f"❌ Free user has no firebase_uid")
        return None

    print(f"\n📝 Generating token for: {email}")
    print(f"   Firebase UID: {firebase_uid}")

    id_token = create_firebase_id_token(firebase_uid, email)

    if id_token:
        tokens["free_token"] = id_token

    if tokens:
        # Save tokens to file for reuse
        tokens_file = os.path.join(os.path.dirname(__file__), "tokens.json")
        try:
            with open(tokens_file, "w") as f:
                json.dump(tokens, f, indent=2)
            print(f"\n✓ Tokens saved to {tokens_file}")
        except Exception as e:
            print(f"\n⚠️  Could not save tokens file: {e}")

        print(f"\n✅ Generated token for free user successfully")
        print("\n⚠️  Note: Firebase ID tokens expire after 1 hour")
        print("   For long-running tests, you may need to regenerate tokens")
        return tokens
    else:
        print("\n❌ Failed to generate token")
        return None


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate Firebase ID tokens for load testing")
    parser.add_argument(
        "--environment",
        choices=["local", "cloud"],
        default="cloud",
        help="Environment type (default: cloud)"
    )
    parser.add_argument(
        "--api-url",
        help="API URL (optional, for reference)"
    )
    parser.add_argument(
        "--terraform-dir",
        default="../config/terraform",
        help="Path to Terraform directory (default: ../config/terraform)"
    )
    parser.add_argument(
        "--service-account-path",
        help="Path to Firebase service account JSON file"
    )

    args = parser.parse_args()

    tokens = generate_jwt_tokens(
        environment=args.environment,
        api_url=args.api_url,
        terraform_dir=args.terraform_dir,
        service_account_path=args.service_account_path,
    )

    if tokens:
        print(f"\n📋 Available tokens: {list(tokens.keys())}")
        sys.exit(0)
    else:
        sys.exit(1)
