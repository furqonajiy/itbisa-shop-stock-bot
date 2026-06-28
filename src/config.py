"""
config.py
---------
All environment variables and constants in one place. Both Shopee and
TikTok Shop credentials live here because this bot talks to both platforms.

This file is loaded once at import time. Required variables are read
directly from the environment, matching the order-bot repos.

Convention: secrets read from env, fixed API hosts and bot behaviour
limits/caps as module constants. Mirrors the layout of config.py in
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

# TikTok Shop per-variant unit cap.
#
# Fixed code constant, not an env var / GitHub Secret. Change this in code
# only when you intentionally want to change stock-allocation behaviour.
#
# The TikTok Shop allocator fills variants smallest-first, capping each
# variant at this many units. Once every variant hits the cap, leftover
# stock stacks onto the largest variant (intentionally over the cap so
# no pieces are dropped).
#
# Example: platform share = 3500 pcs, variants = [1PCS, 10PCS, 50PCS, 200PCS]:
#   1PCS:   400 units (= 400 pcs)    ← cap
#   10PCS:  310 units (= 3100 pcs)   ← remainder fits below cap
#   50PCS:  0 units
#   200PCS: 0 units
#
# Why: TikTok Shop limits buyers to ~20 units per SKU per order. Spreading
# stock across pack sizes widens the range of single-order quantities a
# buyer can place. The cap stops any one variant from hoarding stock so
# every pack size carries something whenever total stock allows.
TIKTOKSHOP_MAX_UNITS_PER_VARIANT = 50

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

# Shopee reserve (IDR). During /stock_balance and /stock_set the bot first
# reserves enough units to Shopee to equal this stock value —
# ceil(SHOPEE_RESERVE_IDR / Shopee unit price), e.g. Rp200.000 / Rp1.000 = 200
# units — then splits the remaining stock between Shopee and TikTok Shop by
# SHOPEE_SPLIT_PERCENT. Set to 0 to disable the reserve. Best-effort: if the
# Shopee price is unknown, no reserve is applied.
SHOPEE_RESERVE_IDR = 200000

# Post-reserve split: Shopee gets this percent of the remaining stock, TikTok
# Shop gets the rest (e.g. 70 → 70:30 Shopee:TikTok Shop). Shopee absorbs any
# rounding remainder. Same split for /stock_balance and /stock_set.
SHOPEE_SPLIT_PERCENT = 70

# Low-stock report (/stock_low): a base SKU is "low" when its combined on-hand
# stock (Shopee + TikTok Shop, in pieces) is below this threshold.
LOW_STOCK_THRESHOLD = 50

# Throttle for the low-stock report. The report (a full two-platform catalog
# scan) is generated at most once per this many hours; repeat triggers within
# the window get an "already generated today" reply instead of re-scanning.
LOW_STOCK_MIN_INTERVAL_HOURS = 24