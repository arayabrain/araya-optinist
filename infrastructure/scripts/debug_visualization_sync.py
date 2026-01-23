#!/usr/bin/env python3
"""Debug visualization_sync endpoint failure."""
import json
import os
import sys

import requests


def load_token():
    """Load token from tokens.json."""
    token_file = "/app/scripts/tokens.json"
    if os.path.exists(token_file):
        with open(token_file) as f:
            tokens = json.load(f)
            token = tokens.get("optinist_test_user_free@araya.org")
            # Token might be stored directly as string or as object with id_token
            if isinstance(token, str):
                return token
            elif isinstance(token, dict):
                return token.get("id_token")
    return None


def main():
    workspace_id = sys.argv[1] if len(sys.argv) > 1 else "38"
    unique_id = sys.argv[2] if len(sys.argv) > 2 else "5d09dc4e"

    print(f"Testing POST /outputs/sync/{workspace_id}/{unique_id}")
    print("=" * 60)

    token = load_token()
    if not token:
        print("ERROR: No token found in /app/scripts/tokens.json")
        return

    print(f"Token loaded (first 50 chars): {token[:50]}...")

    # Make the request
    url = f"http://localhost:8000/outputs/sync/{workspace_id}/{unique_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    print(f"\nURL: {url}")
    print(f"Headers: Authorization: Bearer {token[:30]}...")

    try:
        response = requests.post(url, headers=headers, timeout=30)
        print(f"\nStatus Code: {response.status_code}")
        print(f"Response Headers: {dict(response.headers)}")
        print(f"Response Body: {response.text[:1000]}")

        if response.status_code == 200:
            print("\n SUCCESS - Endpoint returned 200")
        else:
            print(f"\n FAILED - Status {response.status_code}")

    except requests.exceptions.ConnectionError as e:
        print(f"\nConnection Error: {e}")
        print("Trying HTTPS...")

        # Try HTTPS
        url = f"https://localhost:8000/outputs/sync/{workspace_id}/{unique_id}"
        try:
            response = requests.post(url, headers=headers, timeout=30, verify=False)
            print(f"\nHTTPS Status Code: {response.status_code}")
            print(f"Response Body: {response.text[:1000]}")
        except Exception as e2:
            print(f"HTTPS also failed: {e2}")

    except Exception as e:
        print(f"\nError: {e}")


if __name__ == "__main__":
    main()
