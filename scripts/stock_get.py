"""
stock_get.py
------------
CLI entry for /stock_get SKU. Read-only stock inspection across Shopee
and TikTok Shop. Mirrors the dispatch shape of stock_set.py.

Usage:
  python scripts/stock_get.py --sku ITBISA-IC-NE555P-DIP8

Triggered by:
  • /stock_get SKU from the Telegram bot Worker
  • Manual workflow_dispatch on .github/workflows/get.yml

This is a READ-ONLY operation — no write APIs are called. The only
state-changing side effect is token rotation, which the workflow then
commits to bot-state.
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# NOTE: src.main imports config.py which validates required env vars at
# import time. We defer that import until AFTER argparse has run, so
# `--help` works without the env being fully configured.


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ITBisa shop stock GETTER (read-only, Shopee + TikTok Shop)",
    )
    parser.add_argument(
        "--sku",
        type=str,
        required=True,
        help="SKU to inspect (e.g. ITBISA-IC-NE555P-DIP8).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_sku = args.sku.strip().upper()

    if not base_sku:
        print("✗ --sku must not be empty.", file=sys.stderr)
        return 2

    # Deferred import: config.py validates env vars at import time, and
    # we want `--help` and basic input validation to work without that.
    from src import main as stock_main

    return stock_main.run_stock_get_mode(base_sku=base_sku)


if __name__ == "__main__":
    sys.exit(main())
