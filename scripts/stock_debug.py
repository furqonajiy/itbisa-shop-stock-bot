#!/usr/bin/env python3
"""
stock_debug.py
--------------
Diagnostic CLI for investigating "/stock_get says SKU not found" cases.

Walks both Shopee and TikTok Shop catalogs using the SAME clients
/stock_get uses, then reports every way the target SKU might be hiding:

  1. Exact case-sensitive lookup (matches what /stock_get does today).
  2. Case-insensitive scan over catalog base SKU keys.
  3. Case-insensitive substring scan over every variant's raw_sku.

For each hit it prints the stored base_sku key (exact bytes), and every
variant under it: raw_sku, multiplier, stock_units, ids. That lets us
diff "what the operator typed" against "what the platform actually
returned" — usually a case mismatch or trailing character.

Usage:
    python scripts/stock_debug.py --sku ITBISA-PCB-5X7-COPPER-SINGLE-SIDE

Substring search is on by default so partial SKUs work too:
    python scripts/stock_debug.py --sku PCB-5X7

Workflow-wise this script needs the same env vars as /stock_get
(SHOPEE_PARTNER_ID/KEY/SHOP_ID, TIKTOKSHOP_APP_KEY/SECRET/SHOP_ID,
TELEGRAM_*). The runtime token files in data/ are also required.
Read-only: never calls any write API.
"""

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diagnose why a SKU is missing from /stock_get",
    )
    parser.add_argument(
        "--sku",
        type=str,
        required=True,
        help="SKU (or substring) to investigate. Matched case-insensitively.",
    )
    parser.add_argument(
        "--show-samples",
        type=int,
        default=10,
        help="Print this many sample catalog keys from each platform "
             "for context (default: 10). Set 0 to disable.",
    )
    return parser.parse_args()


def _dump_variant(v: dict, indent: str = "      ") -> None:
    """Pretty-print one variant dict. Field set differs between platforms,
    so we print only what's present."""
    fields = ["raw_sku", "multiplier", "stock_units", "weight_grams"]
    for k in fields:
        if k in v:
            print(f"{indent}{k:13s} = {v[k]!r}")

    # Platform-specific ids — only print whichever exist.
    for k in ("item_id", "model_id", "product_id", "sku_id", "warehouse_id"):
        if k in v:
            print(f"{indent}{k:13s} = {v[k]!r}")


def _diagnose_platform(
        platform_label: str,
        catalog: dict[str, list[dict]],
        target: str,
        target_lower: str,
        show_samples: int,
) -> None:
    print()
    print("=" * 70)
    print(f"{platform_label}: {len(catalog)} base SKU(s) discovered")
    print("=" * 70)

    # Always print the literal repr of the target so any invisible
    # whitespace or odd characters in the CLI argument become obvious.
    print(f"Looking for: {target!r}  (len={len(target)})")
    print()

    # --- 1. Exact case-sensitive lookup (mirrors /stock_get behaviour) ---
    exact = catalog.get(target)
    if exact:
        print(f"[1] EXACT case-sensitive lookup: HIT ({len(exact)} variant(s))")
        for v in exact:
            print(f"    variant:")
            _dump_variant(v)
        print()
    else:
        print("[1] EXACT case-sensitive lookup: MISS")
        print("    → this is what /stock_get reports today")
        print()

    # --- 2. Case-insensitive lookup over base keys ---
    ci_key_matches = [k for k in catalog.keys() if k.lower() == target_lower]
    if ci_key_matches:
        print(f"[2] Case-INSENSITIVE base-key match: {len(ci_key_matches)} hit(s)")
        for k in ci_key_matches:
            print(f"    stored key (repr): {k!r}  (len={len(k)})")
            print(f"    {len(catalog[k])} variant(s) under this key:")
            for v in catalog[k]:
                print(f"      variant:")
                _dump_variant(v, indent="        ")
            print()
        if not exact:
            print("    ⚠ DIAGNOSIS: the SKU exists but with different case.")
            print("      The /stock_get lookup is case-sensitive on dict keys.")
            print()
    else:
        print("[2] Case-INSENSITIVE base-key match: 0 hits")
        print("    → SKU isn't stored under this base name on this platform")
        print("      (it might be grouped under a different base — see [3])")
        print()

    # --- 3. Substring scan across every variant raw_sku on this platform ---
    substring_hits: list[tuple[str, dict]] = []
    for base_key, variants in catalog.items():
        for v in variants:
            raw = v.get("raw_sku", "")
            if raw and target_lower in raw.lower():
                substring_hits.append((base_key, v))

    if substring_hits:
        print(f"[3] Substring scan over variant raw_sku: {len(substring_hits)} hit(s)")
        for base_key, v in substring_hits:
            print(f"    grouped under base key: {base_key!r}")
            print(f"    variant:")
            _dump_variant(v)
            print()
    else:
        print("[3] Substring scan over variant raw_sku: 0 hits")
        print("    → the substring does not appear in ANY active variant SKU")
        print("      on this platform. Either the product is inactive (the")
        print("      Shopee walker only includes status=NORMAL), or the SKU")
        print("      on the platform genuinely differs from what was typed.")
        print()

    # --- 4. Sample catalog keys for context ---
    if show_samples > 0:
        sample = sorted(catalog.keys())[:show_samples]
        print(f"[4] Sample of {len(sample)} catalog keys (sorted):")
        for k in sample:
            print(f"    {k!r}")
        print()


def main() -> int:
    args = parse_args()

    target = args.sku.strip()
    target_lower = target.lower()

    if not target:
        print("✗ --sku must not be empty.", file=sys.stderr)
        return 2

    # Deferred import: config.py validates env vars at import time.
    from src import shopee_client, tiktokshop_client

    print("=" * 70)
    print("ITBisa Shop Stock Bot — Debug mode (read-only)")
    print("=" * 70)
    print(f"Target SKU: {target!r}")
    print()

    print("[1/2] Walking Shopee catalog...")
    shopee_catalog = shopee_client.fetch_catalog()
    print(f"  → {len(shopee_catalog)} base SKU(s) on Shopee")

    print("[2/2] Walking TikTok Shop catalog...")
    tiktokshop_catalog = tiktokshop_client.fetch_catalog()
    print(f"  → {len(tiktokshop_catalog)} base SKU(s) on TikTok Shop")

    _diagnose_platform(
        "SHOPEE", shopee_catalog, target, target_lower, args.show_samples,
    )
    _diagnose_platform(
        "TIKTOK SHOP", tiktokshop_catalog, target, target_lower, args.show_samples,
    )

    print("=" * 70)
    print("Done. Paste the output above so we can pinpoint the mismatch.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())