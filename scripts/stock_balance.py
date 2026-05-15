#!/usr/bin/env python3
"""CLI for /stock_balance.

Accepts one or more base SKUs (space-separated). For each SKU runs the
balance flow (read cross-platform total, split 50:50, push). Sends one
Telegram message per SKU as soon as it finishes, so the operator can
see per-SKU status in the chat heartbeat.

SKU missing on a platform -> that SKU is skipped (Telegram alert sent),
the loop continues with the next SKU.

Usage:
    python scripts/stock_balance.py --sku BASE_SKU
    python scripts/stock_balance.py --sku BASE_SKU1 BASE_SKU2 BASE_SKU3
    python scripts/stock_balance.py --sku BASE_SKU --dry-run
"""

import argparse
import logging
import re
import sys

from src.main import run_balance_single

logger = logging.getLogger(__name__)

PCS_PREFIX = re.compile(r"^\d+PCS-", re.IGNORECASE)


def parse_args():
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


def normalize_skus(raw_tokens):
    """Uppercase, strip, dedupe (preserve order), reject XPCS- variants."""
    seen = set()
    ordered = []
    rejected_variants = []
    for raw in raw_tokens:
        if raw is None:
            continue
        # Each token may itself contain spaces (e.g. when shell-passed as one
        # quoted argument). Split defensively.
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


def main():
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

    logger.info(
        "Balancing %d SKU(s)%s: %s",
        len(skus),
        " [DRY RUN]" if args.dry_run else "",
        ", ".join(skus),
    )

    any_success = False
    for sku in skus:
        logger.info("=== %s ===", sku)
        try:
            ok = run_balance_single(sku, dry_run=args.dry_run)
            if ok:
                any_success = True
        except Exception:
            logger.exception("Unhandled error balancing %s; continuing", sku)

    return 0 if any_success else 1


if __name__ == "__main__":
    sys.exit(main())