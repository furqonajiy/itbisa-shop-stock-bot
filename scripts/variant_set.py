#!/usr/bin/env python3
"""CLI for /variant_set — rebuild a TikTok Shop product's pack-size variation.

Sets the product's "Packing" variation to exactly the given pack sizes PLUS a
standard ITBISA-BUBBLE-WRAP value (stock 0, price 100), via Edit Product
(202309). New variants are created at stock 0; re-apply stock afterward with
`/stock_set <base_sku> <saved total>`. TikTok Shop only.

Usage:
    python scripts/variant_set.py --sku ITBISA-IC-PC817-DIP4 --packs 1 20 50 500 1000 --dry-run
    python scripts/variant_set.py --sku ITBISA-IC-PC817-DIP4 --packs 1 20 50 500 1000
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Rebuild a TikTok Shop product's pack-size variation."
    )
    p.add_argument("--sku", type=str, required=True, help="Exact base SKU.")
    p.add_argument(
        "--packs",
        nargs="+",
        type=int,
        required=True,
        help="Pack sizes, e.g. 1 20 50 500 1000.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Build + log the Edit Product payload only; no write.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    base_sku = args.sku.strip().upper()
    if not base_sku:
        print("✗ --sku must not be empty.", file=sys.stderr)
        return 2
    if any(p < 1 for p in args.packs):
        print("✗ pack sizes must be >= 1.", file=sys.stderr)
        return 2

    from src.variant_set_tiktok import run_variant_set

    return run_variant_set(base_sku, args.packs, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
