"""
bootstrap_shopee_tokens.py
--------------------------
One-time setup script. Exchanges a fresh Shopee authorization code for
the initial access_token + refresh_token pair, and writes
data/shopee_tokens.json.

You only need to run this:
  - The very first time you set up this repo locally.
  - If the refresh_token has fully expired (rare, ~30 day inactivity).

Steps:
  1. In Shopee Open Platform Console (https://open.shopee.com), open
     your app and click "Authorize" for the shop. Shopee redirects to
     your configured redirect URL with ?code=XXXX&shop_id=YYYY.
  2. Copy the `code` value (valid for ~10 minutes, single-use).
  3. Run:  python scripts/bootstrap_shopee_tokens.py <CODE>
  4. The script writes data/shopee_tokens.json. Commit + push to
     bot-state branch (the run.yml workflow expects it there).

NOTE: This bot uses the SAME app credentials as itbisa-shopee-order-bot,
but a SEPARATE token chain. So the order bot's tokens are unaffected
by running this script (and vice versa).
"""

import hashlib
import hmac
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import config


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/bootstrap_shopee_tokens.py <AUTH_CODE>")
        print()
        print("Get AUTH_CODE from Shopee Open Platform Console → Authorize.")
        return 1

    auth_code = sys.argv[1].strip()

    print("=" * 60)
    print("Shopee token bootstrap")
    print("=" * 60)
    print(f"Partner ID: {config.SHOPEE_PARTNER_ID}")
    print(f"Shop ID:    {config.SHOPEE_SHOP_ID}")
    print(f"Base URL:   {config.SHOPEE_API_BASE_URL}")
    print()

    # AUTH endpoint signature: base = partner_id + path + timestamp.
    path = "/api/v2/auth/token/get"
    timestamp = int(time.time())
    base = f"{config.SHOPEE_PARTNER_ID}{path}{timestamp}"
    sign = hmac.new(
        config.SHOPEE_PARTNER_KEY.encode("utf-8"),
        base.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    url = (
        f"{config.SHOPEE_API_BASE_URL}{path}"
        f"?partner_id={config.SHOPEE_PARTNER_ID}"
        f"&timestamp={timestamp}"
        f"&sign={sign}"
    )
    body = {
        "code":       auth_code,
        "shop_id":    int(config.SHOPEE_SHOP_ID),
        "partner_id": int(config.SHOPEE_PARTNER_ID),
    }

    print(f"POST {url}")
    response = requests.post(url, json=body, timeout=30)
    response.raise_for_status()
    data = response.json()

    if data.get("error"):
        print(f"✗ Failed: {data.get('error')}: {data.get('message')}")
        return 1

    expire_in = int(data.get("expire_in", 4 * 3600))
    tokens = {
        "access_token":  data["access_token"],
        "refresh_token": data["refresh_token"],
        "access_token_expires_at": (
            datetime.now(timezone.utc) + timedelta(seconds=expire_in)
        ).isoformat(),
    }

    out = Path(config.SHOPEE_TOKEN_FILE)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(tokens, f, indent=2)

    print(f"✓ Wrote {out}")
    print()
    print("Next: commit data/shopee_tokens.json to the bot-state branch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
