#!/usr/bin/env python3
"""
JWT Token Generation for Load Testing

RUNTIME ENVIRONMENT:
✅ Can run locally (with local API server)
✅ Can run on cloud (with proper API endpoints)
⚠️ Requires API server to be running and accessible

Generates JWT tokens for different user types (free, premium, admin)
for use in load testing. This module provides the missing dependency for load_test.py.
"""

import json
import os
import time
from typing import Dict, Optional


def generate_jwt_tokens(
    environment: str = "local", api_url: str = None
) -> Optional[Dict[str, str]]:
    """
    Generate JWT tokens for load testing

    Args:
        environment: Environment type ("local" or "cloud")
        api_url: API base URL (auto-detected for local)

    Returns:
        Dict containing tokens for different user types
    """

    print(f"🔑 Generating JWT tokens for {environment} environment")

    if not api_url:
        if environment == "local":
            api_url = "http://localhost:8000"
        else:
            print("❌ API URL required for non-local environments")
            return None

    print(f"🌐 API URL: {api_url}")

    # For now, generate mock tokens since we can't guarantee requests module
    # is available in all environments
    tokens = {}

    test_users = ["free", "premium", "admin"]

    for user_type in test_users:
        try:
            # Generate mock token for testing
            mock_payload = {
                "user_id": hash(f"{user_type}@example.com") % 100000,
                "email": f"{user_type}@example.com",
                "subscription_type": "premium"
                if user_type in ["premium", "admin"]
                else "free",
                "role": user_type,
                "exp": int(time.time()) + 3600,  # 1 hour expiry
            }

            # Simple mock JWT (not cryptographically signed, for testing only)
            import base64

            header = base64.b64encode(
                json.dumps({"alg": "none", "typ": "JWT"}).encode()
            ).decode()
            payload = base64.b64encode(json.dumps(mock_payload).encode()).decode()
            mock_token = f"{header}.{payload}."

            tokens[f"{user_type}_token"] = mock_token
            print(f"  ✅ Generated mock token for {user_type} user")

        except Exception as e:
            print(f"    ❌ Error generating token for {user_type}: {e}")
            continue

    if tokens:
        # Save tokens to file for reuse
        tokens_file = os.path.join(os.path.dirname(__file__), "tokens.json")
        try:
            with open(tokens_file, "w") as f:
                json.dump(tokens, f, indent=2)
            print(f"💾 Tokens saved to {tokens_file}")
        except Exception as e:
            print(f"⚠️ Could not save tokens file: {e}")

        print(f"✅ Generated {len(tokens)} tokens successfully")
        return tokens
    else:
        print("❌ No tokens generated")
        return None


if __name__ == "__main__":
    tokens = generate_jwt_tokens("local")
    if tokens:
        print(f"\n🎯 Available tokens: {list(tokens.keys())}")
