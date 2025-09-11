#!/usr/bin/env python3
"""
Automated JWT Token Generator for Priority Queue Testing

This script automatically generates JWT tokens for the test users using
email/password login, then updates the test script with the new tokens.

Usage:
    python get_jwt_tokens.py [--local|--cloud]

Prerequisites:
    - requests library (pip install requests)
    - Backend API server running
    - Test users with known passwords
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict

import requests
from test_user_config import load_test_users_for_jwt, print_configuration_help

# Add the project root directory to the Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


def load_test_users() -> Dict:
    """Load test user configuration from various sources."""
    test_users = load_test_users_for_jwt()

    if not test_users:
        print_configuration_help()
        raise ValueError(
            "Test user configuration not found. "
            "Please see README_PRIORITY_TESTING.md for setup instructions."
        )

    return test_users


# Load test users dynamically
TEST_USERS = load_test_users()


class JWTTokenGenerator:
    """Generates JWT tokens for test users using email/password authentication."""

    def __init__(self, api_base_url: str = "http://localhost:8002"):
        self.api_base_url = api_base_url.rstrip("/")

    def login_user(self, email: str, password: str) -> Dict[str, str]:
        """Login user with email/password and get JWT tokens."""
        try:
            url = f"{self.api_base_url}/auth/login"

            payload = {"email": email, "password": password}

            headers = {"Content-Type": "application/json"}

            print(f"🔄 Logging in {email}...")
            response = requests.post(url, json=payload, headers=headers)

            if response.status_code == 200:
                token_data = response.json()
                print(f"✅ Successfully logged in {email}")

                return {
                    "access_token": token_data.get("access_token"),
                    "token_type": token_data.get("token_type", "bearer"),
                    "refresh_token": token_data.get("refresh_token"),
                    "ex_token": token_data.get("ex_token"),
                    "user_email": email,
                }
            else:
                print(f"Login failed for {email}: {response.status_code}")
                print(f"Response: {response.text}")
                raise ValueError(
                    f"Login failed: {response.status_code} - {response.text}"
                )

        except requests.RequestException as e:
            print(f"Network error during login for {email}: {e}")
            raise
        except Exception as e:
            print(f"Login error for {email}: {e}")
            raise

    def verify_token(self, token: str, email: str) -> bool:
        """Verify that the token works by making a test API call."""
        try:
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            # Try to get user info to verify token works
            test_url = f"{self.api_base_url}/users/me"
            response = requests.get(test_url, headers=headers)

            if response.status_code == 200:
                user_data = response.json()
                print(
                    f"✅ Token verified for {email} - User: "
                    f"{user_data.get('name', 'Unknown')}"
                )
                return True
            else:
                print(f"Token verification failed for {email}: {response.status_code}")
                return False

        except Exception as e:
            print(f"Token verification error for {email}: {e}")
            return False

    def generate_tokens_for_users(self) -> Dict[str, Dict[str, str]]:
        """Generate JWT tokens for both test users."""
        tokens = {}

        for user_type, user_info in TEST_USERS.items():
            print(
                f"\n🔄 Generating token for {user_type} user ({user_info['email']})..."
            )

            try:
                # Login with email/password
                token_info = self.login_user(user_info["email"], user_info["password"])

                # Verify the token works
                if self.verify_token(token_info["access_token"], user_info["email"]):
                    tokens[user_type] = token_info
                    print(f"✅ Successfully generated token for {user_type} user")
                else:
                    print(f"Token verification failed for {user_type} user")
                    tokens[user_type] = None

            except Exception as e:
                print(f"Failed to generate token for {user_type} user: {e}")
                tokens[user_type] = None

        return tokens


def main():
    parser = argparse.ArgumentParser(
        description="Generate JWT tokens for priority queue testing"
    )
    parser.add_argument(
        "--environment",
        choices=["local", "cloud"],
        default="local",
        help="Target environment (default: local)",
    )
    parser.add_argument(
        "--api-url", help="Custom API base URL (overrides environment default)"
    )
    parser.add_argument(
        "--output-file",
        default="tokens.json",
        help="Save tokens to JSON file (default: tokens.json)",
    )

    args = parser.parse_args()

    # Determine API URL
    if args.api_url:
        api_url = args.api_url
    elif args.environment == "local":
        api_url = "http://localhost:8002"
    else:
        # For cloud, you'll need to provide the actual cloud URL
        print(
            "Cloud URL not configured. Please use --api-url to specify cloud endpoint"
        )
        sys.exit(1)

    print("🚀 JWT Token Generator for Priority Queue Testing")
    print(f"Environment: {args.environment}")
    print(f"API URL: {api_url}")
    print(f"Output file: {args.output_file}")

    try:
        # Initialize token generator
        generator = JWTTokenGenerator(api_url)

        # Generate tokens
        print("\n🔑 Generating JWT tokens...")
        tokens = generator.generate_tokens_for_users()

        # Display results
        print("\n📊 Token Generation Results:")
        print("=" * 50)

        for user_type, token_info in tokens.items():
            if token_info:
                print(f"✅ {user_type.capitalize()} User: SUCCESS")
                print(f"   Email: {TEST_USERS[user_type]['email']}")
                print(f"   Token: {token_info['access_token'][:50]}...")
            else:
                print(f"{user_type.capitalize()} User: FAILED")

        # Save tokens to file
        with open(args.output_file, "w") as f:
            json.dump(tokens, f, indent=2)
        print(f"\n💾 Tokens saved to: {args.output_file}")

        print("\n🎯 Ready to run tests:")
        print("   cd studio/scripts")
        print("   ./test-workflow-tutorial1-post.sh")

        print("\n✅ JWT token generation completed!")

    except KeyboardInterrupt:
        print("\n⚠️  Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
