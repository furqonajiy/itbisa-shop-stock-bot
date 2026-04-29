"""
bootstrap_tiktokshop_tokens.py
------------------------------
One-time setup. Exchanges an authorization code (from TikTok Shop
Open Platform Console) for the initial access_token + refresh_token
pair, and writes data/tiktokshop_tokens.json.

Steps:
  1. In TikTok Shop Partner Center, authorize your app for the shop.
     The redirect URL receives ?code=XXXX (and shop_id, etc.).
  2. Copy the `code` value (single-use, ~10-minute validity).
  3. Run:  python scripts/bootstrap_tiktokshop_tokens.py <CODE>

Same app credentials as itbisa-tiktokshop-order-bot, separate token chain.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import config


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/bootstrap_tiktokshop_tokens.py <AUTH_CODE>")
        return 1

    auth_code = sys.argv[1].strip()

    print("=" * 60)
    print("TikTok Shop token bootstrap")
    print("=" * 60)
    print(f"App key:   {config.TIKTOKSHOP_APP_KEY}")
    print(f"Shop ID:   {config.TIKTOKSHOP_SHOP_ID}")
    print(f"Auth URL:  {config.TIKTOKSHOP_AUTH_BASE_URL}")
    print()

    # /api/v2/token/get: plain GET, NOT signed.
    url = f"{config.TIKTOKSHOP_AUTH_BASE_URL}/api/v2/token/get"
    params = {
        "app_key":    config.TIKTOKSHOP_APP_KEY,
        "app_secret": config.TIKTOKSHOP_APP_SECRET,
        "auth_code":  auth_code,
        "grant_type": "authorized_code",
    }

    print(f"GET {url}")
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()

    if payload.get("code") != 0:
        print(f"✗ Failed: code={payload.get('code')} message={payload.get('message')}")
        return 1

    data = payload["data"]
    access_expiry  = datetime.fromtimestamp(data["access_token_expire_in"], tz=timezone.utc)
    refresh_expiry = datetime.fromtimestamp(data["refresh_token_expire_in"], tz=timezone.utc)

    tokens = {
        "access_token":             data["access_token"],
        "refresh_token":            data["refresh_token"],
        "access_token_expires_at":  access_expiry.isoformat(),
        "refresh_token_expires_at": refresh_expiry.isoformat(),
    }

    out = Path(config.TIKTOKSHOP_TOKEN_FILE)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(tokens, f, indent=2)

    print(f"✓ Wrote {out}")
    print(f"  access_token expires:  {access_expiry.isoformat()}")
    print(f"  refresh_token expires: {refresh_expiry.isoformat()}")
    print()
    print("Next: commit data/tiktokshop_tokens.json to the bot-state branch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
