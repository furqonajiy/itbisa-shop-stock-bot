#!/usr/bin/env python3
"""CLI for /weight_set — set the per-piece weight across a TikTok Shop product.

Derives a per-piece weight from a reference pack + its total weight, then sets
every variant's weight to `per_pcs × its multiplier` via Edit Product (202309),
preserving the existing variation set, stock, and prices. TikTok Shop only.

Usage:
    python scripts/weight_set.py --sku ITBISA-IC-PC817-DIP4 --ref-pcs 1000 --grams 1700 --dry-run
    python scripts/weight_set.py --sku ITBISA-IC-PC817-DIP4 --ref-pcs 1000 --grams 1700
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Set per-piece weight across a TikTok Shop product's variants."
    )
    p.add_argument("--sku", type=str, required=True, help="Exact base SKU.")
    p.add_argument(
        "--ref-pcs",
        type=int,
        required=True,
        help="Reference pack size in pieces, e.g. 1000.",
    )
    p.add_argument(
        "--grams",
        type=int,
        required=True,
        help="Total weight in grams of the reference pack, e.g. 1700.",
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
    if args.ref_pcs < 1:
        print("✗ --ref-pcs must be >= 1.", file=sys.stderr)
        return 2
    if args.grams < 1:
        print("✗ --grams must be >= 1.", file=sys.stderr)
        return 2

    from src.weight_set_tiktok import run_weight_set

    return run_weight_set(base_sku, args.ref_pcs, args.grams, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
