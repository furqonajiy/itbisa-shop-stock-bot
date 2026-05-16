"""
stock_set.py
------------
CLI entry point for itbisa-shop-stock-bot. Three usages:

  Excel mode (manual bulk operator workflow):
    python scripts/stock_set.py stock.xlsx
    python scripts/stock_set.py stock.xlsx --dry-run

  Single-SKU mode (used by /stock_set SKU JUMLAH in the Telegram Worker):
    python scripts/stock_set.py --sku ITBISA-IC-NE555P-DIP8 --pieces 10000
    python scripts/stock_set.py --sku ITBISA-IC-NE555P-DIP8 --pieces 10000 --dry-run

  Multi-SKU mode (used by /stock_set SKU1 N1 SKU2 N2 ... from the Worker):
    python scripts/stock_set.py --sku SKU1 SKU2 SKU3 --pieces 100 200 300
    python scripts/stock_set.py --sku SKU1 SKU2 --pieces 100 200 --dry-run

In single- and multi-SKU modes, --sku and --pieces are parallel lists
paired positionally. The lists must have the same length.

The CLI is deliberately argparse-based (not just sys.argv positional)
because the GitHub Actions workflow_dispatch dispatches with named
inputs (sku, pieces, dry_run), and named-arg parsing keeps the
workflow YAML readable.
"""

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# NOTE: src.main imports config.py which validates required env vars at
# import time. We defer that import until AFTER argparse has run, so
# `--help` works without the env being fully configured.

_PCS_PREFIX = re.compile(r"^\d+PCS-", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ITBisa shop stock setter (Shopee + TikTok Shop)",
    )

    parser.add_argument(
        "excel_path",
        nargs="?",
        type=Path,
        help="Path to stock.xlsx (Excel mode). Omit when using --sku/--pieces.",
    )
    parser.add_argument(
        "--sku",
        nargs="+",
        type=str,
        default=None,
        help="One or more base SKUs (space-separated). Paired positionally with --pieces.",
    )
    parser.add_argument(
        "--pieces",
        nargs="+",
        type=int,
        default=None,
        help="One or more piece counts (space-separated). Paired positionally with --sku.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned allocation without calling any write API.",
    )

    args = parser.parse_args()

    excel_given = args.excel_path is not None
    sku_given = args.sku is not None
    pieces_given = args.pieces is not None

    if excel_given and (sku_given or pieces_given):
        parser.error("Use either an Excel path OR --sku/--pieces, not both.")
    if not excel_given and not (sku_given and pieces_given):
        parser.error("Provide either an Excel path or both --sku and --pieces.")
    if sku_given and pieces_given and len(args.sku) != len(args.pieces):
        parser.error(
            f"--sku and --pieces must have the same count "
            f"(got {len(args.sku)} SKU(s) and {len(args.pieces)} piece value(s))."
        )
    if args.pieces is not None and any(p < 0 for p in args.pieces):
        parser.error("All --pieces values must be non-negative.")

    return args


def _normalize_pairs(raw_skus: list[str], raw_pieces: list[int]) -> dict[str, int]:
    """Uppercase, strip, dedupe (last value wins, matches Excel reader),
    reject XPCS- variants. Prints stderr-style warnings for skips."""
    desired: dict[str, int] = {}
    for raw_sku, pcs in zip(raw_skus, raw_pieces):
        sku = (raw_sku or "").strip().upper()
        if not sku:
            continue
        if _PCS_PREFIX.match(sku):
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

    # Deferred import: config.py validates env vars at import time, and
    # we want `--help` to work without that.
    from src import main as stock_main

    if args.excel_path is not None:
        return stock_main.run_excel_mode(args.excel_path, args.dry_run)

    desired = _normalize_pairs(args.sku, args.pieces)
    if not desired:
        print("✗ No valid SKUs after normalization.", file=sys.stderr)
        return 2

    return stock_main.run_stock_set_multi(desired, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())