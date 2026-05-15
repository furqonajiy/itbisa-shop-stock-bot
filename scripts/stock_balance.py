#!/usr/bin/env python3
"""CLI for /stock_balance.

Accepts one or more base SKUs (space-separated). Catalogs on both
platforms are walked ONCE inside src.main.run_stock_balance_multi, then
the per-SKU balance flow loops against the cached catalogs. Each SKU
sends its own Telegram message as soon as it finishes, so the operator
sees per-SKU pass/fail in real time.

Usage:
    python scripts/stock_balance.py --sku BASE_SKU
    python scripts/stock_balance.py --sku BASE_SKU1 BASE_SKU2 BASE_SKU3
    python scripts/stock_balance.py --sku BASE_SKU --dry-run
"""

import argparse
import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.main import run_stock_balance_multi

logger = logging.getLogger(__name__)

PCS_PREFIX = re.compile(r"^\d+PCS-", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Rebalance stock 50:50 for one or more base SKUs"
    )
    p.add_argument(
        "--sku",
        nargs="+",
        required=True,
        help="One or more base SKUs (space-separated). XPCS- variants rejected.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip API writes; show planned allocation only.",
    )
    return p.parse_args()


def normalize_skus(raw_tokens: list[str]) -> tuple[list[str], list[str]]:
    """Uppercase, strip, dedupe (preserve order), reject XPCS- variants."""
    seen: set[str] = set()
    ordered: list[str] = []
    rejected_variants: list[str] = []
    for raw in raw_tokens:
        if raw is None:
            continue
        # Each token may itself contain spaces (e.g. when shell-passed as
        # one quoted argument). Split defensively.
        for token in str(raw).split():
            base = token.strip().upper()
            if not base:
                continue
            if PCS_PREFIX.match(base):
                rejected_variants.append(base)
                continue
            if base in seen:
                continue
            seen.add(base)
            ordered.append(base)
    return ordered, rejected_variants


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    args = parse_args()

    skus, rejected = normalize_skus(args.sku)

    for v in rejected:
        logger.warning("Ignoring variant SKU %s; pass base SKU only", v)

    if not skus:
        logger.error("No valid base SKUs provided")
        return 2

    return run_stock_balance_multi(skus, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())