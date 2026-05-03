"""
main.py
-------
Orchestrator. Three run modes, one set of helpers:

  Mode A — Excel:        run_excel_mode(path, dry_run)
    Reads stock.xlsx, iterates over every SKU, and pushes the split
    to both platforms. One Telegram summary at the end.

  Mode B — Single SKU:   run_single_sku_mode(base_sku, total_pieces, dry_run)
    Triggered by /stock_set from the Telegram bot. Same logic as one
    row of Mode A, but Telegram output is more detailed because we
    have the room (one SKU = one message).

  Mode C — Stock get:    run_stock_get_mode(base_sku)
    Triggered by /stock_get from the Telegram bot. READ-ONLY: walks
    both platform catalogs, then sends a Telegram summary listing every
    XPCS- variant with stock units, weight, and per-variant totals.

All modes share:
  - Catalog walk on each platform (one HTTP traffic burst at the start)
  - Skip-with-warning behaviour for SKUs missing on one or both platforms

Mode A and Mode B additionally share:
  - Per-SKU split/allocate/push pipeline
  - Dry-run support that exercises everything except the actual write call

Per-platform allocation rules:
  - Shopee:      equal-share allocation with no TikTok Shop small-pack
                 reserve. Shopee variants can be separate products, so
                 the stock is spread across discovered pack-size variants.
  - TikTok Shop: order-aware. Keep a small physical stock reserve on the
                 smallest pack-size variant, then put the remaining stock
                 on the largest pack-size variant to avoid blocking large
                 one-order purchases.

Failure model:
  - One SKU's failure does NOT abort the run. We accumulate failures
    and report them in the summary.
  - One PLATFORM failing for a SKU does NOT abort the other platform
    for the same SKU. Shopee may succeed while TikTok Shop fails; we
    report a partial success.
  - A Shopee refresh-token-expired exception aborts the whole run and
    sends a manual-intervention alert.
"""

from __future__ import annotations

import time
from pathlib import Path

from src import (
    config,
    excel_reader,
    shopee_auth,
    shopee_client,
    telegram_sender,
    tiktokshop_client,
)
from src.stock_allocator import (
    allocate_pack_sizes,
    split_across_platforms,
    verify_allocation,
)


# ============================================================
# Public entry points (called from scripts/stock_set.py and stock_get.py)
# ============================================================

def run_excel_mode(excel_path: Path, dry_run: bool) -> int:
    """
    Reads excel_path and pushes every SKU. Returns process exit code.

    Exit codes: 0 ok, 1 catastrophic error, 2 hit MAX_SKUS_PER_RUN ceiling.
    """
    print("=" * 70)
    print(f"ITBisa Shop Stock Bot — Excel mode {'(DRY RUN)' if dry_run else ''}")
    print("=" * 70)
    print(f"Shopee:        {shopee_client.describe()}")
    print(f"TikTok Shop:   {tiktokshop_client.describe()} "
          f"(reserve {config.TIKTOKSHOP_SMALL_PACK_RESERVE_PIECES} pcs paket kecil)")
    print(f"Excel file:    {excel_path}")
    print()

    if not excel_path.exists():
        msg = f"Excel file tidak ditemukan: {excel_path}"
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg)
        return 1

    print("[1/4] Reading Excel...")
    desired = excel_reader.read_stock(excel_path)
    print(f"  → {len(desired)} base SKU(s) parsed")
    print()

    if len(desired) > config.MAX_SKUS_PER_RUN:
        msg = (
            f"Excel berisi {len(desired)} SKU, melebihi MAX_SKUS_PER_RUN="
            f"{config.MAX_SKUS_PER_RUN}. Run dibatalkan untuk keamanan. "
            f"Ubah MAX_SKUS_PER_RUN di src/config.py jika ini disengaja."
        )
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg)
        return 2

    if not desired:
        print("Tidak ada SKU untuk diproses. Selesai.")
        return 0

    try:
        print("[2/4] Walking Shopee catalog...")
        shopee_catalog = shopee_client.fetch_catalog()
        print(f"  → {len(shopee_catalog)} base SKU(s) discovered on Shopee")
        print()

        print("[3/4] Walking TikTok Shop catalog...")
        tiktokshop_catalog = tiktokshop_client.fetch_catalog()
        print(f"  → {len(tiktokshop_catalog)} base SKU(s) discovered on TikTok Shop")
        print()
    except shopee_auth.RefreshTokenExpiredError as e:
        msg = (
            f"🔐 Otorisasi Shopee kadaluarsa. Mohon otorisasi ulang aplikasi "
            f"di Shopee Open Platform Console, lalu update file "
            f"data/shopee_tokens.json di branch bot-state. ({e})"
        )
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg)
        return 1

    print("[4/4] Pushing per SKU...")
    succeeded: list[str] = []
    skipped_missing: list[str] = []
    skipped_one_side: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []

    for base_sku, total in desired.items():
        on_shopee = base_sku in shopee_catalog
        on_tiktokshop = base_sku in tiktokshop_catalog

        if not on_shopee and not on_tiktokshop:
            print(f"  ⏭️  {base_sku}: tidak ditemukan di Shopee maupun TikTok Shop — dilewati")
            skipped_missing.append(base_sku)
            continue
        if not on_shopee:
            print(f"  ⏭️  {base_sku}: hanya di TikTok Shop — dilewati (sesuai aturan)")
            skipped_one_side.append((base_sku, "TikTok Shop"))
            continue
        if not on_tiktokshop:
            print(f"  ⏭️  {base_sku}: hanya di Shopee — dilewati (sesuai aturan)")
            skipped_one_side.append((base_sku, "Shopee"))
            continue

        shopee_pieces, tiktokshop_pieces = split_across_platforms(total)

        # Try Shopee first, then TikTok Shop. Each platform result is reported.
        shopee_err = _push_shopee(
            base_sku, shopee_pieces, shopee_catalog[base_sku], dry_run
        )
        tiktokshop_err = _push_tiktokshop(
            base_sku, tiktokshop_pieces, tiktokshop_catalog[base_sku], dry_run
        )

        if shopee_err is None and tiktokshop_err is None:
            succeeded.append(base_sku)
        else:
            err_parts = []
            if shopee_err:
                err_parts.append(f"Shopee: {shopee_err}")
            if tiktokshop_err:
                err_parts.append(f"TikTok Shop: {tiktokshop_err}")
            failed.append((base_sku, " | ".join(err_parts)))

    print()
    print("=" * 70)
    print(
        f"Done. {len(succeeded)} ok, "
        f"{len(failed)} failed, "
        f"{len(skipped_missing)} missing, "
        f"{len(skipped_one_side)} one-side-only."
    )
    print("=" * 70)

    telegram_sender.send_run_summary({
        "mode": "excel",
        "excel_path": str(excel_path),
        "total_skus": len(desired),
        "succeeded": len(succeeded),
        "skipped_missing": skipped_missing,
        "skipped_one_side": skipped_one_side,
        "failed": failed,
        "dry_run": dry_run,
    })

    return 0


def run_single_sku_mode(base_sku: str, total_pieces: int, dry_run: bool) -> int:
    """
    Single-SKU push, used by /stock_set from Telegram.
    Returns process exit code.
    """
    print("=" * 70)
    print(f"ITBisa Shop Stock Bot — Single SKU mode {'(DRY RUN)' if dry_run else ''}")
    print("=" * 70)
    print(f"SKU:    {base_sku}")
    print(f"Total:  {total_pieces} pcs")
    print(f"TikTok Shop small-pack reserve: {config.TIKTOKSHOP_SMALL_PACK_RESERVE_PIECES} pcs")
    print()

    try:
        print("Walking catalogs...")
        shopee_catalog = shopee_client.fetch_catalog()
        tiktokshop_catalog = tiktokshop_client.fetch_catalog()
    except shopee_auth.RefreshTokenExpiredError as e:
        msg = f"🔐 Otorisasi Shopee kadaluarsa. ({e})"
        telegram_sender.send_alert(msg)
        return 1

    on_shopee = base_sku in shopee_catalog
    on_tiktokshop = base_sku in tiktokshop_catalog

    if not on_shopee and not on_tiktokshop:
        msg = f"SKU `{base_sku}` tidak ditemukan di Shopee maupun TikTok Shop. Periksa SKU dan coba lagi."
        telegram_sender.send_alert(msg)
        return 1

    if not on_shopee:
        msg = f"SKU `{base_sku}` hanya ada di TikTok Shop (tidak di Shopee). Tidak diproses (sesuai aturan)."
        telegram_sender.send_alert(msg)
        return 1

    if not on_tiktokshop:
        msg = f"SKU `{base_sku}` hanya ada di Shopee (tidak di TikTok Shop). Tidak diproses (sesuai aturan)."
        telegram_sender.send_alert(msg)
        return 1

    shopee_pieces, tiktokshop_pieces = split_across_platforms(total_pieces)

    shopee_lines, shopee_status = _format_and_push_shopee(
        base_sku, shopee_pieces, shopee_catalog[base_sku], dry_run
    )
    tiktokshop_lines, tiktokshop_status = _format_and_push_tiktokshop(
        base_sku, tiktokshop_pieces, tiktokshop_catalog[base_sku], dry_run
    )

    telegram_sender.send_single_sku_summary({
        "mode": "single",
        "base_sku": base_sku,
        "total_pieces": total_pieces,
        "shopee_pieces": shopee_pieces,
        "tiktokshop_pieces": tiktokshop_pieces,
        "shopee_lines": shopee_lines,
        "tiktokshop_lines": tiktokshop_lines,
        "shopee_status": shopee_status,
        "tiktokshop_status": tiktokshop_status,
        "dry_run": dry_run,
    })

    # Exit non-zero if either platform failed. Partial success needs manual attention.
    if "❌" in shopee_status or "❌" in tiktokshop_status:
        return 1
    return 0


def run_stock_get_mode(base_sku: str) -> int:
    """
    Read-only stock inspection for one base SKU across both platforms.
    Triggered by /stock_get SKU from the Telegram Worker.

    Returns process exit code:
      0 = found on at least one platform, summary sent
      1 = not found anywhere, or upstream auth failure
    """
    print("=" * 70)
    print("ITBisa Shop Stock Bot — Get mode (read-only)")
    print("=" * 70)
    print(f"SKU: {base_sku}")
    print()

    try:
        print("[1/2] Walking Shopee catalog...")
        shopee_catalog = shopee_client.fetch_catalog()
        print(f"  → {len(shopee_catalog)} base SKU(s) discovered on Shopee")

        print("[2/2] Walking TikTok Shop catalog...")
        tiktokshop_catalog = tiktokshop_client.fetch_catalog()
        print(f"  → {len(tiktokshop_catalog)} base SKU(s) discovered on TikTok Shop")
        print()
    except shopee_auth.RefreshTokenExpiredError as e:
        msg = (
            f"🔐 Otorisasi Shopee kadaluarsa. Mohon otorisasi ulang aplikasi "
            f"di Shopee Open Platform Console, lalu update file "
            f"data/shopee_tokens.json di branch bot-state. ({e})"
        )
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg)
        return 1

    shopee_variants = shopee_catalog.get(base_sku, [])
    tiktokshop_variants = tiktokshop_catalog.get(base_sku, [])

    if not shopee_variants and not tiktokshop_variants:
        msg = (
            f"SKU `{base_sku}` tidak ditemukan di Shopee maupun TikTok Shop. "
            f"Periksa SKU dan coba lagi."
        )
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg)
        return 1

    # Console preview — useful when debugging from the Actions log.
    for label, variants in (("Shopee", shopee_variants), ("TikTok Shop", tiktokshop_variants)):
        print(f"  {label}: {len(variants)} varian")
        for v in variants:
            print(
                f"    • {v['raw_sku']} (×{v['multiplier']}): "
                f"{v['stock_units']} unit, {v['weight_grams']} g"
            )

    telegram_sender.send_stock_get_summary({
        "base_sku": base_sku,
        "shopee_variants": shopee_variants,
        "tiktokshop_variants": tiktokshop_variants,
    })
    return 0


# ============================================================
# Per-platform push helpers (Excel mode)
# ============================================================

def _push_shopee(
        base_sku: str,
        pieces: int,
        variants: list[dict],
        dry_run: bool,
) -> str | None:
    """Pushes pieces to Shopee. Returns error message string or None on success."""
    try:
        # Shopee: equal-share allocation with no TikTok Shop reserve.
        allocations = allocate_pack_sizes(pieces, variants)
    except ValueError as e:
        return f"allocate failed: {e}"

    lost = verify_allocation(pieces, allocations)
    print(
        f"  → Shopee {base_sku}: {pieces} pcs across {len(variants)} variant(s)"
        + (f", {lost} pcs unrepresentable" if lost else "")
    )

    for variant, units in allocations:
        raw = variant["raw_sku"]
        mult = variant["multiplier"]
        print(f"      • {raw} (×{mult}) ← {units} unit(s) (= {units * mult} pcs)")
        if dry_run:
            continue
        try:
            shopee_client.update_stock(
                item_id=variant["item_id"],
                model_id=variant["model_id"],
                new_stock=units,
            )
        except Exception as e:
            return f"{raw}: {e}"
        time.sleep(config.DELAY_BETWEEN_CALLS_SECONDS)

    return None


def _push_tiktokshop(
        base_sku: str,
        pieces: int,
        variants: list[dict],
        dry_run: bool,
) -> str | None:
    """Pushes pieces to TikTok Shop. Returns error message string or None.

    TikTok Shop-only: reserve small-pack stock, then push bulk stock to
    the largest pack-size variant to support large one-order purchases.
    """
    try:
        allocations = allocate_pack_sizes(
            pieces,
            variants,
            small_pack_reserve_pieces=config.TIKTOKSHOP_SMALL_PACK_RESERVE_PIECES,
        )
    except ValueError as e:
        return f"allocate failed: {e}"

    lost = verify_allocation(pieces, allocations)
    print(
        f"  → TikTok Shop {base_sku}: {pieces} pcs across {len(variants)} variant(s) "
        f"(small-pack reserve {config.TIKTOKSHOP_SMALL_PACK_RESERVE_PIECES} pcs)"
        + (f", {lost} pcs unrepresentable" if lost else "")
    )

    # Group allocations by product_id. TikTok Shop requires same-product batches.
    by_product: dict[str, list[tuple[str, str, int]]] = {}
    for variant, units in allocations:
        raw = variant["raw_sku"]
        mult = variant["multiplier"]
        print(f"      • {raw} (×{mult}) ← {units} unit(s) (= {units * mult} pcs)")
        by_product.setdefault(variant["product_id"], []).append(
            (variant["sku_id"], variant["warehouse_id"], units)
        )

    if dry_run:
        return None

    for product_id, sku_updates in by_product.items():
        try:
            tiktokshop_client.update_stock_batch(product_id, sku_updates)
        except Exception as e:
            return f"product {product_id}: {e}"
        time.sleep(config.DELAY_BETWEEN_CALLS_SECONDS)

    return None


# ============================================================
# Single-SKU push helpers (return formatted lines + status for Telegram)
# ============================================================

def _format_and_push_shopee(
        base_sku: str,
        pieces: int,
        variants: list[dict],
        dry_run: bool,
) -> tuple[list[str], str]:
    """Returns (formatted_lines, status_string)."""
    try:
        allocations = allocate_pack_sizes(pieces, variants)
    except ValueError as e:
        return [], f"❌ gagal: {e}"

    lines: list[str] = []
    push_err: str | None = None

    for variant, units in allocations:
        raw = variant["raw_sku"]
        mult = variant["multiplier"]
        lines.append(f"  • `{raw}`: {units} unit (= {units * mult} pcs)")
        if dry_run or push_err is not None:
            continue
        try:
            shopee_client.update_stock(
                item_id=variant["item_id"],
                model_id=variant["model_id"],
                new_stock=units,
            )
        except Exception as e:
            push_err = f"{raw}: {e}"
        time.sleep(config.DELAY_BETWEEN_CALLS_SECONDS)

    if push_err:
        return lines, f"❌ gagal: {push_err}"
    if dry_run:
        return lines, "🔍 dry-run"
    return lines, "✅ berhasil"


def _format_and_push_tiktokshop(
        base_sku: str,
        pieces: int,
        variants: list[dict],
        dry_run: bool,
) -> tuple[list[str], str]:
    """Returns (formatted_lines, status_string).

    TikTok Shop-only: reserve small-pack stock, then push bulk stock to
    the largest pack-size variant.
    """
    try:
        allocations = allocate_pack_sizes(
            pieces,
            variants,
            small_pack_reserve_pieces=config.TIKTOKSHOP_SMALL_PACK_RESERVE_PIECES,
        )
    except ValueError as e:
        return [], f"❌ gagal: {e}"

    lines: list[str] = []
    by_product: dict[str, list[tuple[str, str, int]]] = {}

    for variant, units in allocations:
        raw = variant["raw_sku"]
        mult = variant["multiplier"]
        lines.append(f"  • `{raw}`: {units} unit (= {units * mult} pcs)")
        by_product.setdefault(variant["product_id"], []).append(
            (variant["sku_id"], variant["warehouse_id"], units)
        )

    if dry_run:
        return lines, "🔍 dry-run"

    push_err: str | None = None
    for product_id, sku_updates in by_product.items():
        try:
            tiktokshop_client.update_stock_batch(product_id, sku_updates)
        except Exception as e:
            push_err = f"product {product_id}: {e}"
            break
        time.sleep(config.DELAY_BETWEEN_CALLS_SECONDS)

    if push_err:
        return lines, f"❌ gagal: {push_err}"
    return lines, "✅ berhasil"
