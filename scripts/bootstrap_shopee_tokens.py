"""
bootstrap_tokens.py
-------------------
One-time script to seed data/shopee_tokens.json with initial tokens.

When to run this:
  - The very first time you set up the bot.
  - After the refresh_token expires (every 30 days) and you need to re-authorize.
  - When switching between sandbox and live environments.

How to use it:
  1. Log into Shopee Open Platform Console.
  2. Go to App List and click "Authorize" on your app.
  3. Paste the shop URL (or use the test shop for sandbox) and confirm.
  4. After redirect, copy the "code" value from the URL.
     Example: https://example.com/?code=ABC123...&shop_id=XXX
  5. Run this script and paste the code when prompted.
  6. The script writes data/shopee_tokens.json with valid tokens.

Make sure SHOPEE_API_BASE_URL in config.py points to the right environment
(sandbox or live) BEFORE running this script. The tokens you receive will
only work for that specific environment.
"""

import hashlib
import hmac
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# Add project root to path so we can import src.config
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import config


def main():
    """Interactive bootstrap flow."""

    print("=" * 60)
    print("Shopee Tokens Bootstrap")
    print("=" * 60)
    print(f"Target environment: {config.SHOPEE_API_BASE_URL}")
    print(f"Partner ID: {config.SHOPEE_PARTNER_ID}")
    print(f"Shop ID: {config.SHOPEE_SHOP_ID}")
    print()
    print("Make sure the target environment above matches where you got")
    print("the authorization code from. Sandbox codes do not work on live,")
    print("and vice versa.")
    print()

    # STEP 1: Prompt for the authorization code.
    code = input("Paste the authorization code from the Shopee Console URL: ").strip()
    if not code:
        print("No code provided. Aborting.")
        sys.exit(1)

    # STEP 2: Build the signed request to exchange the code for tokens.
    # The auth endpoint uses a simpler signature format than shop-level calls:
    # only partner_id + path + timestamp (no access_token, no shop_id).
    path = "/api/v2/auth/token/get"
    timestamp = int(time.time())

    base_string = f"{config.SHOPEE_PARTNER_ID}{path}{timestamp}"
    signature = hmac.new(
        config.SHOPEE_PARTNER_KEY.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    url = (
        f"{config.SHOPEE_API_BASE_URL}{path}"
        f"?partner_id={config.SHOPEE_PARTNER_ID}"
        f"&timestamp={timestamp}"
        f"&sign={signature}"
    )

    body = {
        "code": code,
        "partner_id": config.SHOPEE_PARTNER_ID,
        "shop_id": config.SHOPEE_SHOP_ID,
    }

    # STEP 3: Call the endpoint.
    print(f"\nCalling {path}...")
    response = requests.post(url, json=body, timeout=30)
    print(f"Status: {response.status_code}")
    data = response.json()

    # STEP 4: Check for errors.
    if response.status_code != 200 or data.get("error"):
        print(f"\nShopee rejected the request:")
        print(json.dumps(data, indent=2))
        print("\nCommon causes:")
        print("  - Code already used (each code is single-use)")
        print("  - Code expired (codes are valid for ~10 minutes)")
        print("  - Wrong environment (sandbox code used against live URL or vice versa)")
        print("  - Wrong partner_id or partner_key")
        sys.exit(1)

    # STEP 5: Extract the tokens and compute the absolute expiry timestamp.
    access_token = data["access_token"]
    refresh_token = data["refresh_token"]
    expire_in_seconds = data["expire_in"]
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expire_in_seconds)

    tokens = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "access_token_expires_at": expires_at.isoformat(),
    }

    # STEP 6: Write the tokens file.
    tokens_path = Path(config.TOKENS_FILE_PATH)
    tokens_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tokens_path, "w") as f:
        json.dump(tokens, f, indent=2, sort_keys=True)

    print(f"\n✓ Wrote tokens to {tokens_path}")
    print(f"  access_token:  {access_token[:12]}... (truncated)")
    print(f"  refresh_token: {refresh_token[:12]}... (truncated)")
    print(f"  expires at:    {expires_at.isoformat()}")
    print(f"\nYou can now run the bot normally. The bot will auto-refresh")
    print(f"the access_token every 4 hours. You only need to run this")
    print(f"script again when the refresh_token expires in 30 days.")


if __name__ == "__main__":
    main()
