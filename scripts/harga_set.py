#!/usr/bin/env python3
"""CLI for /harga_set — set tiered TikTok Shop prices for one base SKU.

Tiers are (JUMLAH HARGA) pairs: the unit price for quantities at or above each
JUMLAH. Each TikTok Shop pack-size variant is priced by the tier its pack size
falls into (× its pack size). Shopee "Harga Grosir" is added in a later change.

Usage:
    python scripts/harga_set.py --sku ITBISA-IC-NE555P-DIP8 --tiers 1 749 50 739 100 699
    python scripts/harga_set.py --sku ITBISA-IC-NE555P-DIP8 --tiers 1 749 50 739 --dry-run
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Set tiered TikTok Shop prices for one base SKU (read-only Shopee for now)."
    )
    p.add_argument("--sku", type=str, required=True, help="Exact base SKU.")
    p.add_argument(
        "--tiers",
        nargs="+",
        required=True,
        help="JUMLAH HARGA pairs, e.g. 1 749 50 739 100 699.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip price write APIs; show the planned prices only.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    base_sku = args.sku.strip().upper()
    if not base_sku:
        print("✗ --sku must not be empty.", file=sys.stderr)
        return 2

    from src.harga_set_price import run_harga_set

    return run_harga_set(base_sku, args.tiers, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
