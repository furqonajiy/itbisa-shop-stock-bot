import os
import sys

# Make the repo root importable so tests can do `from src... import ...`
# regardless of pytest's import mode / rootdir handling.
sys.path.insert(0, os.path.dirname(__file__))

# src/config.py validates these at import time. Set dummy values so modules
# under test import cleanly; setdefault never overrides a real environment.
for _key in (
    "SHOPEE_PARTNER_ID",
    "SHOPEE_PARTNER_KEY",
    "SHOPEE_SHOP_ID",
    "TIKTOKSHOP_APP_KEY",
    "TIKTOKSHOP_APP_SECRET",
    "TIKTOKSHOP_SHOP_ID",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
):
    os.environ.setdefault(_key, "test")
