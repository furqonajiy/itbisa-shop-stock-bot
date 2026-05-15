"""
stock_balance.py
----------------
CLI entry for /stock_balance SKU. Reads the cross-platform piece total
of a base SKU, then rewrites it to both platforms with the standard
50:50 split. The grand total is preserved; only the per-platform share
changes.

Usage:
  python scripts/stock_balance.py --sku ITBISA-IC-NE555P-DIP8
  python scripts/stock_balance.py --sku ITBISA-IC-NE555P-DIP8 --dry-run

Triggered by:
  • /stock_balance SKU [dry] from the Telegram bot Worker
  • Manual workflow_dispatch on .github/workflows/balance.yml

Internally delegates to existing catalog walk + per-platform push
helpers in src/main.py — no duplicated allocation logic.
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
        description="ITBisa shop stock BALANCER (read total, redistribute 50:50)",
    )
    parser.add_argument(
        "--sku",
        type=str,
        required=True,
        help="Base SKU to rebalance (e.g. ITBISA-IC-NE555P-DIP8).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned rebalance without calling any write API.",
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

    return stock_main.run_stock_balance_mode(
        base_sku=base_sku,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
