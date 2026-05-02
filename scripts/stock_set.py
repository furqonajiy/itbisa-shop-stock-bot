"""
stock_set.py
------------
CLI entry point for itbisa-shop-stock-bot. Two usages:

  Excel mode (manual bulk operator workflow):
    python scripts/stock_set.py stock.xlsx
    python scripts/stock_set.py stock.xlsx --dry-run

  Single-SKU mode (used by /stock_set in the Telegram bot Worker):
    python scripts/stock_set.py --sku ITBISA-IC-NE555P-DIP8 --pieces 10000
    python scripts/stock_set.py --sku ITBISA-IC-NE555P-DIP8 --pieces 10000 --dry-run

The CLI is deliberately argparse-based (not just sys.argv positional)
because the GitHub Actions workflow_dispatch dispatches with named
inputs (sku, pieces, dry_run), and named-arg parsing keeps the
workflow YAML readable.
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
        description="ITBisa shop stock setter (Shopee + TikTok Shop)",
    )

    # Mutually exclusive: either an Excel path OR a single-SKU pair.
    parser.add_argument(
        "excel_path",
        nargs="?",
        type=Path,
        help="Path to stock.xlsx (Excel mode). Omit when using --sku.",
    )
    parser.add_argument(
        "--sku",
        type=str,
        default=None,
        help="Base SKU for single-SKU mode (e.g. ITBISA-IC-NE555P-DIP8).",
    )
    parser.add_argument(
        "--pieces",
        type=int,
        default=None,
        help="Total physical pieces for single-SKU mode (e.g. 10000).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned allocation without calling any write API.",
    )

    args = parser.parse_args()

    excel_given = args.excel_path is not None
    single_given = args.sku is not None or args.pieces is not None

    if excel_given and single_given:
        parser.error("Use either an Excel path OR --sku/--pieces, not both.")
    if not excel_given and not single_given:
        parser.error("Provide either an Excel path or both --sku and --pieces.")
    if single_given and (args.sku is None or args.pieces is None):
        parser.error("Single-SKU mode requires BOTH --sku and --pieces.")
    if args.pieces is not None and args.pieces < 0:
        parser.error("--pieces must be non-negative.")

    return args


def main() -> int:
    args = parse_args()

    # Deferred import: config.py validates env vars at import time, and
    # we want `--help` to work without that.
    from src import main as stock_main

    if args.excel_path is not None:
        return stock_main.run_excel_mode(args.excel_path, args.dry_run)

    return stock_main.run_single_sku_mode(
        base_sku=args.sku.strip(),
        total_pieces=args.pieces,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
