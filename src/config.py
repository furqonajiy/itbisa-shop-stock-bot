"""
config.py
---------
All environment variables and constants in one place. Both Shopee and
TikTok Shop credentials live here because this bot talks to both platforms.

This file is loaded once at import time. If a required variable is
missing, we fail loudly at startup rather than mid-run.

Convention: secrets read from env, fixed API hosts and paths/buffers as
module constants. Mirrors the layout of config.py in the order-bot repos
so a developer who knows one knows this.
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


# ============================================================
# Shopee
# ============================================================

SHOPEE_PARTNER_ID  = _required("SHOPEE_PARTNER_ID")
SHOPEE_PARTNER_KEY = _required("SHOPEE_PARTNER_KEY")
SHOPEE_SHOP_ID     = _required("SHOPEE_SHOP_ID")

# Live Shopee Open API host. Kept here as a code constant, like the
# Shopee order bot, because this inventory bot is intended for production.
SHOPEE_API_BASE_URL = "https://partner.shopeemobile.com"

SHOPEE_TOKEN_FILE = PROJECT_ROOT / "data" / "shopee_tokens.json"

# Refresh access_token this many minutes before its declared expiry.
# 10 min is the same value used by itbisa-shopee-order-bot; keeps the
# two bots in lockstep on token-refresh timing.
SHOPEE_TOKEN_REFRESH_BUFFER_MINUTES = 10


# ============================================================
# TikTok Shop
# ============================================================

TIKTOKSHOP_APP_KEY    = _required("TIKTOKSHOP_APP_KEY")
TIKTOKSHOP_APP_SECRET = _required("TIKTOKSHOP_APP_SECRET")
TIKTOKSHOP_SHOP_ID    = _required("TIKTOKSHOP_SHOP_ID")

# TikTok Shop uses two distinct hosts: auth and Open API.
TIKTOKSHOP_AUTH_BASE_URL = "https://auth.tiktok-shops.com"
TIKTOKSHOP_OPEN_API_BASE_URL = "https://open-api.tiktokglobalshop.com"

TIKTOKSHOP_TOKEN_FILE = PROJECT_ROOT / "data" / "tiktokshop_tokens.json"

TIKTOKSHOP_TOKEN_REFRESH_BUFFER_MINUTES = 10

# Per-variant unit cap on TikTok Shop ONLY. The allocator fills the
# smallest-multiplier variant first up to this cap, then shifts to the
# next-smallest variant up to this cap, and so on.
#
# Worked example: 5000 pcs → [1PCS, 100PCS] with cap=200:
#   1PCS:   200 units (= 200 pcs)   ← capped
#   100PCS: 48 units  (= 4800 pcs)
#
# Set very high (e.g. 100000) to effectively disable. Shopee is NOT
# affected by this setting — Shopee variants live under separate
# products and the operator already controls per-product caps via the
# Excel input.
TIKTOKSHOP_MAX_UNITS_PER_VARIANT = int(_optional("TIKTOKSHOP_MAX_UNITS_PER_VARIANT", "200"))


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