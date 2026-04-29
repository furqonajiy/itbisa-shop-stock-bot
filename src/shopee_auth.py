"""
shopee_auth.py
--------------
Shopee token lifecycle, mirrored 1:1 from itbisa-shopee-order-bot.

Public contract:
  get_valid_access_token() -> str
    Returns a usable access_token. Refreshes from refresh_token if the
    current access_token is within TOKEN_REFRESH_BUFFER_MINUTES of expiry.
    Raises RefreshTokenExpiredError if the refresh_token itself has
    expired (custom exception so main.py can send a Bahasa alert).

Token file format (data/shopee_tokens.json), three fields:
  {
    "access_token": "...",
    "refresh_token": "...",
    "access_token_expires_at": "2026-04-29T12:00:00+00:00"
  }

NOTE: Shopee does NOT return a refresh_token expiry, so we don't store
one. Refresh tokens nominally last 30 days and rotate on every refresh
(the new refresh_token is in the response body of access_token/get).
We save the new refresh_token IMMEDIATELY upon receiving it — losing
it would force a manual re-authorize.
"""

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from src import config


class RefreshTokenExpiredError(Exception):
    """Raised when Shopee's refresh_token has expired and the operator
    must re-authorize the app via the Open Platform Console."""


def get_valid_access_token() -> str:
    """Returns a non-expired access_token, refreshing if needed."""
    tokens = _load_tokens()

    if not _is_access_token_expiring_soon(tokens):
        return tokens["access_token"]

    print("  [shopee_auth] Access token near expiry, refreshing...")
    new_tokens = _refresh_access_token(tokens["refresh_token"])
    _save_tokens(new_tokens)
    return new_tokens["access_token"]


# ============================================================
# Token file I/O
# ============================================================

def _load_tokens() -> dict:
    path = Path(config.SHOPEE_TOKEN_FILE)
    if not path.exists():
        raise RuntimeError(
            f"Shopee token file not found at {path}. "
            f"Run scripts/bootstrap_shopee_tokens.py first."
        )
    with open(path, "r") as f:
        return json.load(f)


def _save_tokens(tokens: dict) -> None:
    """Atomic write: tmp file -> rename. Prevents half-written state on crash."""
    path = Path(config.SHOPEE_TOKEN_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(tokens, f, indent=2)
    tmp.replace(path)


def _is_access_token_expiring_soon(tokens: dict) -> bool:
    expires_at = datetime.fromisoformat(tokens["access_token_expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    buffer = timedelta(minutes=config.SHOPEE_TOKEN_REFRESH_BUFFER_MINUTES)
    return datetime.now(timezone.utc) + buffer >= expires_at


# ============================================================
# Refresh flow
# ============================================================

def _refresh_access_token(refresh_token: str) -> dict:
    """
    Exchanges a refresh_token for a new access_token + new refresh_token.

    Auth-endpoint signature (NO access_token, NO shop_id in base_string):
      base = partner_id + path + timestamp
      sign = HMAC-SHA256(partner_key, base).hexdigest()
    """
    path = "/api/v2/auth/access_token/get"
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
        "shop_id": int(config.SHOPEE_SHOP_ID),
        "refresh_token": refresh_token,
        "partner_id": int(config.SHOPEE_PARTNER_ID),
    }

    response = requests.post(url, json=body, timeout=30)
    response.raise_for_status()
    data = response.json()

    # Shopee signals a dead refresh_token via the `error` field.
    err = (data.get("error") or "").lower()
    if err and "expir" in err:
        raise RefreshTokenExpiredError(data.get("message", "refresh token expired"))
    if data.get("error"):
        raise RuntimeError(f"Shopee refresh failed: {data.get('error')}: {data.get('message')}")

    new_access  = data["access_token"]
    new_refresh = data["refresh_token"]
    expire_in   = int(data.get("expire_in", 4 * 3600))  # access_token TTL in seconds

    return {
        "access_token":  new_access,
        "refresh_token": new_refresh,
        "access_token_expires_at": (
            datetime.now(timezone.utc) + timedelta(seconds=expire_in)
        ).isoformat(),
    }
