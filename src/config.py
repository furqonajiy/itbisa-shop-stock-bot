"""
config.py
---------
All environment variables in one place. Both Shopee and TikTok Shop
credentials live here because this bot talks to both platforms.

This file is loaded once at import time. If a required variable is
missing, we fail loudly at startup rather than mid-run. Optional
variables (USE_FAKE_*) default to safe values.

Convention: secrets read from env, paths/buffers as module constants.
Mirrors the layout of config.py in itbisa-shopee-order-bot and
itbisa-tiktokshop-order-bot so a developer who knows one knows this.
"""

import os
from pathlib import Path

from dotenv import load_dotenv


# Load .env from the project root if present. Production runs on
# GitHub Actions where vars come from secrets, so the .env file is
# only meaningful for local development.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _required(name: str) -> str:
    """Returns the env var or raises a clear error if missing."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Set it in .env (local) or GitHub Secrets (CI)."
        )
    return value


def _optional(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _flag(name: str, default: bool = False) -> bool:
    """Boolean from env: 'true', '1', 'yes' = True. Anything else = False."""
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("true", "1", "yes")


# ============================================================
# Shopee
# ============================================================

SHOPEE_PARTNER_ID  = _required("SHOPEE_PARTNER_ID")
SHOPEE_PARTNER_KEY = _required("SHOPEE_PARTNER_KEY")
SHOPEE_SHOP_ID     = _required("SHOPEE_SHOP_ID")

# Live (https://partner.shopeemobile.com) vs sandbox
# (https://partner.test-stable.shopeemobile.com). Default = live since
# the inventory bot only runs after the order bots are already in prod.
SHOPEE_API_BASE_URL = _optional(
    "SHOPEE_API_BASE_URL",
    "https://partner.shopeemobile.com",
)

SHOPEE_TOKEN_FILE = PROJECT_ROOT / "data" / "shopee_tokens.json"

# Refresh access_token this many minutes before its declared expiry.
# 10 min is the same value used by itbisa-shopee-order-bot; keeps the
# two bots in lockstep on token-refresh timing.
SHOPEE_TOKEN_REFRESH_BUFFER_MINUTES = 10

# Local development knob — swaps in a canned fake client. Off in CI.
USE_FAKE_SHOPEE = _flag("USE_FAKE_SHOPEE", default=False)


# ============================================================
# TikTok Shop
# ============================================================

TIKTOKSHOP_APP_KEY    = _required("TIKTOKSHOP_APP_KEY")
TIKTOKSHOP_APP_SECRET = _required("TIKTOKSHOP_APP_SECRET")
TIKTOKSHOP_SHOP_ID    = _required("TIKTOKSHOP_SHOP_ID")

# Two distinct hosts — auth and Open API are not the same domain.
TIKTOKSHOP_AUTH_BASE_URL = _optional(
    "TIKTOKSHOP_AUTH_BASE_URL",
    "https://auth.tiktok-shops.com",
)
TIKTOKSHOP_OPEN_API_BASE_URL = _optional(
    "TIKTOKSHOP_OPEN_API_BASE_URL",
    "https://open-api.tiktokglobalshop.com",
)

TIKTOKSHOP_TOKEN_FILE = PROJECT_ROOT / "data" / "tiktokshop_tokens.json"

TIKTOKSHOP_TOKEN_REFRESH_BUFFER_MINUTES = 10

USE_FAKE_TIKTOKSHOP = _flag("USE_FAKE_TIKTOKSHOP", default=False)


# ============================================================
# Telegram
# ============================================================

TELEGRAM_BOT_TOKEN = _required("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = _required("TELEGRAM_CHAT_ID")


# ============================================================
# Inventory bot behaviour
# ============================================================

# Politeness delay between API calls (seconds). Both platforms have
# rate limits ~1-2 QPS in practice; 1.0s is the same value the original
# scripts used and never hit a limit in production.
DELAY_BETWEEN_CALLS_SECONDS = 1.0

# Safety ceiling: a single run that would touch more than this many
# SKU rows aborts before any API call. Prevents a typo'd 50,000-row
# Excel file from being pushed by accident. Override via env if the
# operator genuinely has a larger catalog.
MAX_SKUS_PER_RUN = int(_optional("MAX_SKUS_PER_RUN", "500"))
