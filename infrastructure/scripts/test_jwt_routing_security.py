#!/usr/bin/env python3
"""
JWT-Based Routing Integration Tests (Non-Reversible Routing IDs)

Tests the JWT-based routing with HMAC-SHA256 non-reversible routing IDs.

WHAT IT TESTS:
1. Valid JWT flow - User authenticates and receives routing headers
2. Routing headers present - Verify x-user-tier and x-routing-id for premium
3. UID privacy - Verify UID is NEVER exposed in headers
4. Tier accuracy - Verify tier matches user's subscription
5. Routing ID format - Verify routing ID is 16-character hex
6. Free tier handling - Verify free users don't get routing ID
7. Invalid JWT handling - Verify graceful handling of bad tokens
8. Cache behavior - Verify tier caching works

HOW TO RUN:
  # Auto-generate tokens (recommended)
  python infrastructure/scripts/test_jwt_routing_security.py \
      --api-url https://your-alb-url.amazonaws.com

  # Use existing token
  export FIREBASE_TOKEN="your_token"
  export TEST_USER_UID="user_uid"
  export TEST_USER_TIER="premium"
  python infrastructure/scripts/test_jwt_routing_security.py \
      --api-url https://your-alb-url.amazonaws.com

REQUIREMENTS:
- Python 3.8+
- requests library
- Access to deployed environment
- Valid Firebase authentication token
"""

import argparse
import os
import sys
import time
from typing import Optional, Tuple

import requests

# ANSI colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"

# Import token generation utility
try:
    from get_jwt_tokens import generate_jwt_tokens
except ImportError:
    generate_jwt_tokens = None


def get_test_tokens(user_tier: str = "premium") -> Tuple[str, str, str]:
    """Generate Firebase tokens for testing"""
    if not generate_jwt_tokens:
        raise RuntimeError("Cannot import get_jwt_tokens module")

    print(f"Generating Firebase tokens for {user_tier} user...")
    tokens = generate_jwt_tokens()

    if user_tier not in tokens:
        raise RuntimeError(f"No {user_tier} user token generated")

    user_data = tokens[user_tier]
    return user_data["id_token"], user_data["uid"], user_tier


class JWTRoutingTests:
    """Integration tests for JWT-based routing"""

    def __init__(
        self,
        api_base_url: str,
        firebase_token: str,
        test_user_uid: str,
        test_user_tier: str,
    ):
        self.api_base_url = api_base_url.rstrip("/")
        self.firebase_token = firebase_token
        self.test_user_uid = test_user_uid
        self.test_user_tier = test_user_tier
        self.session = requests.Session()
        self.tests_passed = 0
        self.tests_failed = 0

    def print_test(self, name: str):
        print(f"\n{YELLOW}Test: {name}{RESET}")

    def print_pass(self, msg: str):
        print(f"{GREEN}✓ PASS: {msg}{RESET}")
        self.tests_passed += 1

    def print_fail(self, msg: str):
        print(f"{RED}✗ FAIL: {msg}{RESET}")
        self.tests_failed += 1

    def print_info(self, msg: str):
        print(f"  {msg}")

    def make_request(
        self, endpoint: str, headers: Optional[dict] = None
    ) -> requests.Response:
        """Make authenticated request"""
        url = f"{self.api_base_url}{endpoint}"
        all_headers = {"Authorization": f"Bearer {self.firebase_token}"}
        if headers:
            all_headers.update(headers)
        return self.session.get(url, headers=all_headers, timeout=30)

    def test_valid_jwt_flow(self) -> bool:
        """Test 1: Valid JWT flow with routing headers"""
        self.print_test("Valid JWT Flow (Premium User)")

        try:
            response = self.make_request("/api/users/me")

            if response.status_code != 200:
                self.print_fail(f"Request failed: {response.status_code}")
                return False

            # Check for routing headers
            user_tier = response.headers.get("x-user-tier")
            routing_id = response.headers.get("x-routing-id")

            if not user_tier:
                self.print_fail("Missing x-user-tier header")
                return False

            self.print_info(f"x-user-tier: {user_tier}")

            # Verify tier matches
            if user_tier != self.test_user_tier:
                self.print_fail(
                    f"Tier mismatch: expected {self.test_user_tier}, got {user_tier}"
                )
                return False

            # Premium users should have routing ID
            if self.test_user_tier == "premium":
                if not routing_id:
                    self.print_fail("Missing x-routing-id header for premium user")
                    return False

                self.print_info(f"x-routing-id: {routing_id}")

                # Verify routing ID format (16 hex characters)
                if len(routing_id) != 16 or not all(
                    c in "0123456789abcdef" for c in routing_id
                ):
                    self.print_fail(f"Invalid routing ID format: {routing_id}")
                    return False

                self.print_pass("Premium routing headers present and valid")
            else:
                # Free users should NOT have routing ID
                if routing_id:
                    self.print_fail("Free user should not have x-routing-id header")
                    return False

                self.print_pass("Free tier headers correct (no routing ID)")

            return True

        except Exception as e:
            self.print_fail(f"Request failed: {e}")
            return False

    def test_uid_privacy(self) -> bool:
        """Test 2: UID Privacy - Verify UID is NEVER exposed"""
        self.print_test("UID Privacy (Security Critical)")

        try:
            response = self.make_request("/api/users/me")

            if response.status_code != 200:
                self.print_fail(f"Request failed: {response.status_code}")
                return False

            # Check ALL response headers for UID exposure
            all_headers = dict(response.headers)
            uid_exposed = False

            for header_name, header_value in all_headers.items():
                if self.test_user_uid in str(header_value):
                    self.print_fail(
                        f"UID EXPOSED in header '{header_name}': {header_value}"
                    )
                    uid_exposed = True

            if uid_exposed:
                return False

            # Also verify x-user-id header does NOT exist (old implementation)
            if "x-user-id" in all_headers:
                self.print_fail("x-user-id header present (should be removed)")
                return False

            self.print_pass("UID not exposed in any response header (secure)")
            return True

        except Exception as e:
            self.print_fail(f"Request failed: {e}")
            return False

    def test_invalid_jwt_handling(self) -> bool:
        """Test 3: Invalid JWT handling"""
        self.print_test("Invalid JWT Handling")

        try:
            # Make request with invalid token
            url = f"{self.api_base_url}/api/users/me"
            response = self.session.get(
                url, headers={"Authorization": "Bearer invalid_token_12345"}, timeout=30
            )

            # Should either reject (401/403) or pass through without routing headers
            if response.status_code in [401, 403]:
                self.print_pass(f"Invalid JWT rejected: {response.status_code}")
                return True

            # If 200, routing headers should not be present
            if response.status_code == 200:
                user_tier = response.headers.get("x-user-tier")
                routing_id = response.headers.get("x-routing-id")

                if user_tier or routing_id:
                    self.print_fail("Routing headers present for invalid JWT")
                    return False

                self.print_pass("Invalid JWT handled gracefully (no routing headers)")
                return True

            self.print_info(f"Unexpected status: {response.status_code}")
            return True

        except Exception as e:
            self.print_fail(f"Request failed: {e}")
            return False

    def test_tier_cache_consistency(self) -> bool:
        """Test 4: Tier cache consistency"""
        self.print_test("Tier Cache Consistency")

        try:
            # Make multiple requests
            tiers = []
            for i in range(3):
                response = self.make_request("/api/users/me")
                if response.status_code == 200:
                    tier = response.headers.get("x-user-tier")
                    if tier:
                        tiers.append(tier)
                time.sleep(0.5)

            if len(tiers) < 2:
                self.print_fail("Not enough responses to verify consistency")
                return False

            # All tiers should be the same (cache working)
            if len(set(tiers)) != 1:
                self.print_fail(f"Inconsistent tiers: {tiers}")
                return False

            self.print_pass(f"Tier consistent across {len(tiers)} requests")
            return True

        except Exception as e:
            self.print_fail(f"Request failed: {e}")
            return False

    def test_missing_auth_header(self) -> bool:
        """Test 5: Missing Authorization header"""
        self.print_test("Missing Authorization Header")

        try:
            url = f"{self.api_base_url}/api/users/me"
            response = self.session.get(url, timeout=30)

            # Should reject without auth
            if response.status_code in [401, 403]:
                self.print_pass(
                    f"Request rejected without auth: {response.status_code}"
                )
                return True

            # If passes through, should not have routing headers
            if response.status_code == 200:
                user_tier = response.headers.get("x-user-tier")
                routing_id = response.headers.get("x-routing-id")

                if user_tier or routing_id:
                    self.print_fail("Routing headers present without auth")
                    return False

                self.print_pass("No routing headers without auth")
                return True

            self.print_info(f"Unexpected status: {response.status_code}")
            return True

        except Exception as e:
            self.print_fail(f"Request failed: {e}")
            return False

    def run_all_tests(self) -> int:
        """Run all tests"""
        print(f"\n{BLUE}{'=' * 80}{RESET}")
        print(
            f"{BLUE}JWT-Based Routing Integration Tests "
            f"(Non-Reversible Routing IDs){RESET}"
        )
        print(f"{BLUE}{'=' * 80}{RESET}")

        self.test_valid_jwt_flow()
        self.test_uid_privacy()
        self.test_invalid_jwt_handling()
        self.test_tier_cache_consistency()
        self.test_missing_auth_header()

        # Summary
        print(f"\n{BLUE}{'=' * 80}{RESET}")
        print(f"{BLUE}Test Summary{RESET}")
        print(f"{BLUE}{'=' * 80}{RESET}")
        print(f"Passed: {GREEN}{self.tests_passed}{RESET}")
        print(f"Failed: {RED}{self.tests_failed}{RESET}")

        if self.tests_failed == 0:
            print(f"\n{GREEN}✓ All tests passed!{RESET}\n")
            return 0
        else:
            print(f"\n{RED}✗ {self.tests_failed} test(s) failed{RESET}\n")
            return 1


def main():
    parser = argparse.ArgumentParser(description="JWT-Based Routing Integration Tests")
    parser.add_argument(
        "--api-url", default=os.environ.get("API_BASE_URL"), help="API base URL"
    )
    parser.add_argument(
        "--firebase-token",
        default=os.environ.get("FIREBASE_TOKEN"),
        help="Firebase token",
    )
    parser.add_argument(
        "--user-uid", default=os.environ.get("TEST_USER_UID"), help="Test user UID"
    )
    parser.add_argument(
        "--user-tier",
        default=os.environ.get("TEST_USER_TIER", "premium"),
        help="User tier",
    )
    parser.add_argument(
        "--skip-token-gen", action="store_true", help="Skip auto token generation"
    )

    args = parser.parse_args()

    if not args.api_url:
        print(f"{RED}Error: API_BASE_URL required{RESET}")
        return 1

    # Auto-generate tokens if needed
    firebase_token = args.firebase_token
    user_uid = args.user_uid
    user_tier = args.user_tier

    if not args.skip_token_gen and (not firebase_token or not user_uid):
        try:
            firebase_token, user_uid, user_tier = get_test_tokens(user_tier)
        except Exception as e:
            print(f"{RED}Error generating tokens: {e}{RESET}")
            return 1

    if not firebase_token or not user_uid:
        print(f"{RED}Error: Missing credentials{RESET}")
        return 1

    # Run tests
    test_suite = JWTRoutingTests(args.api_url, firebase_token, user_uid, user_tier)
    return test_suite.run_all_tests()


if __name__ == "__main__":
    sys.exit(main())
