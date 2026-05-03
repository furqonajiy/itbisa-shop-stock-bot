"""
config.py
---------
All environment variables and constants in one place. Both Shopee and
TikTok Shop credentials live here because this bot talks to both platforms.

This file is loaded once at import time. Required variables are read
directly from the environment, matching the order-bot repos.

Convention: secrets read from env, fixed API hosts and bot behaviour
limits/reserves as module constants. Mirrors the layout of config.py in
the order-bot repos so a developer who knows one knows this.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root if present. Production runs on
# GitHub Actions where vars come from secrets, so the .env file is
# only meaningful for local development.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# ============================================================
# Shopee
# ============================================================

SHOPEE_PARTNER_ID = os.environ["SHOPEE_PARTNER_ID"]
SHOPEE_PARTNER_KEY = os.environ["SHOPEE_PARTNER_KEY"]
SHOPEE_SHOP_ID = os.environ["SHOPEE_SHOP_ID"]

# Live Shopee Open API host. Kept here as a code constant, like the
# Shopee order bot, because this stock bot is intended for production.
SHOPEE_API_BASE_URL = "https://partner.shopeemobile.com"

SHOPEE_TOKEN_FILE = PROJECT_ROOT / "data" / "shopee_tokens.json"

# Refresh access_token this many minutes before its declared expiry.
# 10 min is the same value used by itbisa-shopee-order-bot; keeps the
# two bots in lockstep on token-refresh timing.
SHOPEE_TOKEN_REFRESH_BUFFER_MINUTES = 10

# ============================================================
# TikTok Shop
# ============================================================

TIKTOKSHOP_APP_KEY = os.environ["TIKTOKSHOP_APP_KEY"]
TIKTOKSHOP_APP_SECRET = os.environ["TIKTOKSHOP_APP_SECRET"]
TIKTOKSHOP_SHOP_ID = os.environ["TIKTOKSHOP_SHOP_ID"]

# TikTok Shop uses two distinct hosts: auth and Open API.
TIKTOKSHOP_AUTH_BASE_URL = "https://auth.tiktok-shops.com"
TIKTOKSHOP_OPEN_API_BASE_URL = "https://open-api.tiktokglobalshop.com"

TIKTOKSHOP_TOKEN_FILE = PROJECT_ROOT / "data" / "tiktokshop_tokens.json"

TIKTOKSHOP_TOKEN_REFRESH_BUFFER_MINUTES = 10

# TikTok Shop order-aware allocation reserve.
#
# Fixed code constant, not an env var / GitHub Secret. Change this in code
# only when you intentionally want to change stock-allocation behaviour.
#
# This is a PHYSICAL PIECE reserve for the smallest pack-size variant,
# not a per-variant unit cap.
#
# Example: platform share = 2000 pcs, variants = [1PCS, 100PCS], reserve=200:
#   1PCS:   200 units (= 200 pcs)
#   100PCS: 18 units  (= 1800 pcs)
#
# Why: stock above the practical one-order qty limit on 1PCS does not
# help large buyers. Bulk stock should be represented by the largest
# pack-size variant, while the smallest pack stays available for small buyers.
TIKTOKSHOP_SMALL_PACK_RESERVE_PIECES = 200

# Legacy compatibility constant only. Do not configure this via env and do
# not use it for new allocation logic; TikTok Shop allocation should use
# TIKTOKSHOP_SMALL_PACK_RESERVE_PIECES instead.
TIKTOKSHOP_MAX_UNITS_PER_VARIANT = 200

# ============================================================
# Telegram
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# ============================================================
# Stock bot behaviour
# ============================================================

# Politeness delay between API calls (seconds). Both platforms have
# rate limits ~1-2 QPS in practice; 1.0s is the same value the original
# scripts used and never hit a limit in production.
DELAY_BETWEEN_CALLS_SECONDS = 1.0

# Safety ceiling: a single run that would touch more than this many
# SKU rows aborts before any API call. Prevents a typo'd 50,000-row
# Excel file from being pushed by accident.
#
# Fixed code constant, not an env var / GitHub Secret. Change this in code
# only when the catalog is intentionally larger.
MAX_SKUS_PER_RUN = 500
