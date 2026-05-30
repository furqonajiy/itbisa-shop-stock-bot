#!/usr/bin/env python3
"""CLI for price-aware /stock_set --sku/--pieces mode."""

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PCS_PREFIX = re.compile(r"^\d+PCS-", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ITBisa price-aware stock setter",
    )
    parser.add_argument("--sku", nargs="+", required=True)
    parser.add_argument("--pieces", nargs="+", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if len(args.sku) != len(args.pieces):
        parser.error(
            f"--sku and --pieces must have the same count "
            f"(got {len(args.sku)} SKU(s) and {len(args.pieces)} piece value(s))."
        )
    if any(p < 0 for p in args.pieces):
        parser.error("All --pieces values must be non-negative.")
    return args


def normalize_pairs(raw_skus: list[str], raw_pieces: list[int]) -> dict[str, int]:
    desired: dict[str, int] = {}
    for raw_sku, pcs in zip(raw_skus, raw_pieces):
        sku = (raw_sku or "").strip().upper()
        if not sku:
            continue
        if PCS_PREFIX.match(sku):
            print(f"  Skipping variant SKU {sku}; pass base SKU only")
            continue
        if sku in desired and desired[sku] != pcs:
            print(
                f"  SKU {sku} duplicate — overwriting earlier value "
                f"{desired[sku]} with {pcs}"
            )
        desired[sku] = pcs
    return desired


def main() -> int:
    args = parse_args()

    from src.stock_set_price_rule import run_stock_set_multi

    desired = normalize_pairs(args.sku, args.pieces)
    if not desired:
        print("✗ No valid SKUs after normalization.", file=sys.stderr)
        return 2

    return run_stock_set_multi(desired, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
