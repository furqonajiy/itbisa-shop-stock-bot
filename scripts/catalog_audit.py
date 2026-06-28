#!/usr/bin/env python3
"""CLI entry for the read-only catalog standardization audit.

Walks the live Shopee + TikTok Shop catalogs and writes an Excel report listing
every base SKU that isn't standardized yet. Changes nothing on either platform.

    python scripts/catalog_audit.py [--output catalog_audit.xlsx]
"""

from __future__ import annotations

import argparse
import pathlib
import sys

# Bootstrap sys.path so `from src...` resolves when run as a script.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.catalog_audit import run_catalog_audit  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Catalog standardization audit (read-only).")
    parser.add_argument("--output", default="catalog_audit.xlsx", help="Excel output path.")
    args = parser.parse_args()
    return run_catalog_audit(args.output)


if __name__ == "__main__":
    raise SystemExit(main())
