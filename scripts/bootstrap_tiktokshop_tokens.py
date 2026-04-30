"""
bootstrap_tiktokshop_tokens.py
------------------------------
One-time script to seed data/tiktokshop_tokens.json with initial
TikTok Shop tokens.

When to run this:
  - The very first time you set up the bot.
  - After the refresh_token expires and you need to re-authorize.
  - When switching TikTok Shop apps.

How to use it:
  1. Log into TikTok Shop Open Platform.
  2. Authorize your app for the target shop.
  3. After redirect, copy the auth_code value from the URL.
  4. Run this script and paste the auth_code when prompted.
  5. The script writes data/tiktokshop_tokens.json with valid tokens.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# Add project root to path so we can import src.config.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import config


def main():
    """Interactive bootstrap flow."""

    print("=" * 60)
    print("TikTok Shop Tokens Bootstrap")
    print("=" * 60)
    print(f"Target environment: {config.TIKTOKSHOP_AUTH_BASE_URL}")
    print(f"App Key: {config.TIKTOKSHOP_APP_KEY}")
    print()

    # STEP 1: Prompt for the authorization code.
    auth_code = input("Paste the auth_code from the TikTok Shop redirect URL: ").strip()
    if not auth_code:
        print("No auth_code provided. Aborting.")
        sys.exit(1)

    # STEP 2: Build the request exactly as TikTok Shop expects it.
    path = "/api/v2/token/get"
    url = f"{config.TIKTOKSHOP_AUTH_BASE_URL}{path}"
    params = {
        "app_key": config.TIKTOKSHOP_APP_KEY,
        "app_secret": config.TIKTOKSHOP_APP_SECRET,
        "auth_code": auth_code,
        "grant_type": "authorized_code",
    }

    # STEP 3: Call the endpoint.
    print(f"\nCalling {path}...")
    response = requests.get(url, params=params, timeout=30)
    print(f"Status: {response.status_code}")

    try:
        data = response.json()
    except ValueError:
        print("\nTikTok Shop did not return valid JSON:")
        print(response.text)
        sys.exit(1)

    # STEP 4: Check for HTTP or API errors.
    if response.status_code != 200 or data.get("code") not in (0, "0"):
        print("\nTikTok Shop rejected the request:")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        sys.exit(1)

    # STEP 5: Extract the payload from the response.
    payload = data["data"]
    access_token = payload["access_token"]
    refresh_token = payload["refresh_token"]
    access_token_expire_in = payload["access_token_expire_in"]
    refresh_token_expire_in = payload["refresh_token_expire_in"]

    # STEP 6: Convert TikTok Shop expiry timestamps into ISO format.
    # These values are Unix timestamps, not durations.
    access_token_expires_at = _unix_to_iso(access_token_expire_in)
    refresh_token_expires_at = _unix_to_iso(refresh_token_expire_in)

    tokens = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "access_token_expires_at": access_token_expires_at,
        "refresh_token_expires_at": refresh_token_expires_at,
    }

    # STEP 7: Write the tokens file.
    tokens_path = Path(config.TIKTOKSHOP_TOKEN_FILE)
    tokens_path.parent.mkdir(parents=True, exist_ok=True)

    with open(tokens_path, "w", encoding="utf-8") as f:
        json.dump(tokens, f, indent=2, sort_keys=True)

    print(f"\n✓ Wrote tokens to {tokens_path}")
    print(f"  access_token:  {access_token[:12]}... (truncated)")
    print(f"  refresh_token: {refresh_token[:12]}... (truncated)")
    print(f"  access_token_expires_at:  {access_token_expires_at}")
    print(f"  refresh_token_expires_at: {refresh_token_expires_at}")


def _unix_to_iso(value):
    """
    Converts a Unix timestamp in seconds to ISO 8601 UTC string.

    Example:
      1777539662 -> 2026-04-29T...
    """
    return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()


if __name__ == "__main__":
    main()