"""
get_tiktokshop_cipher_code.py
-----------------------------
Fetches the authorized shops list from TikTok Shop and prints the full
response body so we can see the shop cipher code.

This version matches the confirmed working curl shape:
  GET /authorization/202309/shops
  query params:
    - access_token
    - app_key
    - shop_id
    - sign
    - timestamp
    - version
  header:
    - x-tts-access-token

How to use it:
  1. Make sure these environment variables are set:
       - TIKTOKSHOP_APP_KEY
       - TIKTOKSHOP_APP_SECRET
       - TIKTOKSHOP_SHOP_ID
  2. Make sure data/tiktokshop_tokens.json already exists and contains a
     valid access_token.
  3. Run:
       python scripts/get_tiktokshop_cipher_code.py
"""

import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

# Add project root to path so we can import src.config.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import config


def main():
    """Entry point for the helper script."""

    print("=" * 60)
    print("TikTok Shop - Get Authorized Shops / Cipher Code")
    print("=" * 60)
    print(f"App Key: {config.TIKTOKSHOP_APP_KEY}")
    print(f"Target Shop ID: {config.TIKTOKSHOP_SHOP_ID}")
    print()

    # STEP 1: Load the access token from the tokens file.
    access_token = _load_access_token()
    print(f"Loaded access token from {config.TIKTOKSHOP_TOKEN_FILE}")

    # STEP 2: Build the request params exactly like the confirmed curl.
    path = "/authorization/202309/shops"
    timestamp = str(int(time.time()))

    # Important:
    # This request shape includes shop_id in the signed query.
    params_for_sign = {
        "app_key": config.TIKTOKSHOP_APP_KEY,
        "shop_id": str(config.TIKTOKSHOP_SHOP_ID),
        "timestamp": timestamp,
        "version": "202309",
    }

    sign = _make_signature(path=path, params=params_for_sign)

    # STEP 3: Add the remaining params used in the actual GET request.
    request_params = {
        "access_token": access_token,
        **params_for_sign,
        "sign": sign,
    }

    url = f"{config.TIKTOKSHOP_OPEN_API_BASE_URL}{path}"
    headers = {
        "x-tts-access-token": access_token,
    }

    print(f"Calling {path} ...")
    print(f"GET {url}?{urlencode(request_params)}")

    # STEP 4: Call the endpoint.
    response = requests.get(
        url,
        params=request_params,
        headers=headers,
        timeout=30,
    )
    print(f"HTTP status: {response.status_code}")

    # STEP 5: Print the raw response body exactly as requested.
    print("\nRaw response body:")
    print(response.text)

    # STEP 6: Parse JSON so we can also show the matching shop cleanly.
    try:
        data = response.json()
    except ValueError:
        print("\nResponse is not valid JSON. Stopping here.")
        sys.exit(1)

    if response.status_code != 200:
        print("\nTikTok Shop returned a non-200 HTTP status.")
        sys.exit(1)

    if data.get("code") not in (0, "0"):
        print("\nTikTok Shop returned an API error:")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        sys.exit(1)

    # STEP 7: Extract the shops list and find the configured shop.
    shops = data["data"].get("shops", [])
    if not shops:
        print("\nNo shops found in response.")
        return

    print("\nAuthorized shops summary:")
    for shop in shops:
        print(
            f"  - id={shop.get('id')} | code={shop.get('code')} | "
            f"cipher={shop.get('cipher')} | name={shop.get('name')}"
        )

    matching_shop = next(
        (shop for shop in shops if str(shop.get("id")) == str(config.TIKTOKSHOP_SHOP_ID)),
        None,
    )

    # STEP 8: Print the final matched result.
    if matching_shop:
        print("\nMatched shop:")
        print(json.dumps(matching_shop, indent=2, ensure_ascii=False))
        print(f"\nCipher code for shop {config.TIKTOKSHOP_SHOP_ID}: {matching_shop.get('cipher')}")
    else:
        print("\nConfigured TIKTOKSHOP_SHOP_ID was not found in the authorized shops list.")
        print("Check whether the correct TikTok Shop was authorized for this access token.")


def _load_access_token():
    """Loads the access token from data/tiktokshop_tokens.json."""

    tokens_path = Path(config.TIKTOKSHOP_TOKEN_FILE)
    if not tokens_path.exists():
        raise FileNotFoundError(
            f"Tokens file not found at {tokens_path}. "
            f"Run bootstrap_tiktokshop_tokens.py first."
        )

    with open(tokens_path, "r", encoding="utf-8") as f:
        token_data = json.load(f)

    return token_data["access_token"]


def _make_signature(path, params):
    """
    Builds the TikTok Shop signature for the authorized shops endpoint.

    The access_token is sent in the request, but is not included in the
    signature input here.
    """

    sorted_items = sorted(params.items(), key=lambda item: item[0])

    parts = [config.TIKTOKSHOP_APP_SECRET, path]
    for key, value in sorted_items:
        parts.append(str(key))
        parts.append(str(value))
    parts.append(config.TIKTOKSHOP_APP_SECRET)
    string_to_sign = "".join(parts)

    return hmac.new(
        config.TIKTOKSHOP_APP_SECRET.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


if __name__ == "__main__":
    main()