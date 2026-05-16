"""
main.py
-------
Orchestrator. Four run modes, one set of helpers:

  Mode A — Excel:        run_excel_mode(path, dry_run)
    Reads stock.xlsx, iterates over every SKU, and pushes the split
    to both platforms. One Telegram summary at the end.

  Mode B — Set (single or multi SKU):
                         run_single_sku_mode(base_sku, total_pieces, dry_run)
                         run_stock_set_multi(desired, dry_run)
    Triggered by /stock_set from the Telegram bot. Single-SKU is a thin
    wrapper around run_stock_set_multi. Walks both catalogs ONCE, loops
    per SKU.

    Telegram output strategy:
      - 1 SKU  -> detailed summary (per-variant lines).
      - 2+ SKU -> ONE compact end-of-run summary listing each SKU with
                  per-platform pieces and status. Keeps long batches
                  (e.g. 20 SKU) from spamming the operator chat.

  Mode C — Stock get:    run_stock_get_mode(base_sku)
    Triggered by /stock_get from the Telegram bot. READ-ONLY: walks
    both platform catalogs, then sends a Telegram summary listing every
    XPCS- variant with stock units, weight, and per-variant totals.

  Mode D — Stock balance: run_stock_balance_mode(base_sku, dry_run)
                           run_stock_balance_multi(base_skus, dry_run)
    Triggered by /stock_balance from the Telegram bot, and by order bots
    at end-of-run for the full list of shipped base SKUs in a single
    dispatch. Walks both catalogs ONCE, loops per SKU.

    Telegram output strategy:
      - 1 SKU  -> existing detailed summary (per-variant lines).
      - 2+ SKU -> ONE compact end-of-run summary listing each SKU with
                  per-platform before -> after pieces. Keeps long batches
                  (e.g. 20 SKU) from spamming the operator chat.

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
# Public entry points (called from scripts/stock_set.py, stock_get.py,
# and stock_balance.py)
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
        f"Done. "
        f"{len(succeeded)} ok, "
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
    Single-SKU set entry point. Thin wrapper around run_stock_set_multi
    so the detailed-vs-compact summary switch lives in one place.
    """
    return run_stock_set_multi({base_sku: total_pieces}, dry_run)


def run_stock_set_multi(desired: dict[str, int], dry_run: bool) -> int:
    """
    Set absolute stock for one or more base SKUs via 50:50 split.

    Triggered by /stock_set from the Telegram Worker (single or multi-SKU)
    and by manual workflow_dispatch with parallel --sku/--pieces lists.

    Walks both catalogs ONCE and loops per SKU.

    Telegram output:
      - 1 SKU  -> detailed summary via send_single_sku_summary
                  (preserves the per-variant breakdown).
      - 2+ SKU -> ONE compact summary via send_stock_set_multi_summary
                  listing each SKU with per-platform pieces and status.

    Returns process exit code:
      0 = all SKUs ok (or dry-run completed)
      1 = at least one SKU skipped or failed (or upstream auth failure)
      2 = hit MAX_SKUS_PER_RUN ceiling
    """
    if not desired:
        msg = "Tidak ada SKU diberikan ke set mode."
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg)
        return 1

    print("=" * 70)
    print(f"ITBisa Shop Stock Bot — Set mode {'(DRY RUN)' if dry_run else ''}")
    print("=" * 70)
    print(f"TikTok Shop small-pack reserve: {config.TIKTOKSHOP_SMALL_PACK_RESERVE_PIECES} pcs")
    if len(desired) == 1:
        only_sku, only_total = next(iter(desired.items()))
        print(f"SKU:   {only_sku}")
        print(f"Total: {only_total} pcs")
    else:
        print(f"SKUs ({len(desired)}):")
        for sku, total in desired.items():
            print(f"  • {sku} = {total} pcs")
    print()

    if len(desired) > config.MAX_SKUS_PER_RUN:
        msg = (
            f"Set mode menerima {len(desired)} SKU, melebihi MAX_SKUS_PER_RUN="
            f"{config.MAX_SKUS_PER_RUN}. Run dibatalkan untuk keamanan."
        )
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg)
        return 2

    try:
        print("Walking catalogs...")
        shopee_catalog = shopee_client.fetch_catalog()
        tiktokshop_catalog = tiktokshop_client.fetch_catalog()
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

    results: list[dict] = []
    for base_sku, total_pieces in desired.items():
        if len(desired) > 1:
            print("-" * 70)
            print(f"Setting: {base_sku} = {total_pieces} pcs")
            print("-" * 70)
        results.append(
            _set_one_sku(
                base_sku, total_pieces, shopee_catalog, tiktokshop_catalog, dry_run
            )
        )
        print()

    # One Telegram message per run, regardless of SKU count.
    if len(results) == 1:
        _send_single_set_telegram(results[0], dry_run)
    else:
        telegram_sender.send_stock_set_multi_summary({
            "results": results,
            "dry_run": dry_run,
        })

    if any(r["status"] not in ("ok", "dry_run") for r in results):
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
        telegram_sender.send_alert(msg, mode="Get Stock")
        return 1

    shopee_variants = shopee_catalog.get(base_sku, [])
    tiktokshop_variants = tiktokshop_catalog.get(base_sku, [])

    if not shopee_variants and not tiktokshop_variants:
        msg = (
            f"SKU `{base_sku}` tidak ditemukan di Shopee maupun TikTok Shop. "
            f"Periksa SKU dan coba lagi."
        )
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg, mode="Get Stock")
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


def run_stock_balance_mode(base_sku: str, dry_run: bool) -> int:
    """
    Single-SKU balance entry point. Thin wrapper around
    run_stock_balance_multi so the detailed-vs-compact summary
    switch lives in one place.
    """
    return run_stock_balance_multi([base_sku], dry_run)


def run_stock_balance_multi(base_skus: list[str], dry_run: bool) -> int:
    """
    Rebalance one or more base SKUs across Shopee and TikTok Shop.

    Walks both catalogs ONCE and loops per SKU. The grand total per
    SKU is preserved; only the per-platform share changes (50:50 split).

    Telegram output:
      - 1 SKU  -> detailed summary via send_stock_balance_summary
                  (operator typed /stock_balance for that one SKU).
      - 2+ SKU -> ONE compact summary via send_stock_balance_multi_summary
                  listing each SKU with per-platform before -> after.

    Returns process exit code:
      0 = all SKUs ok (or dry-run completed)
      1 = at least one SKU skipped (missing/zero-total) or failed
    """
    if not base_skus:
        msg = "Tidak ada SKU diberikan ke balance mode."
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg, mode="Balance Stock")
        return 1

    print("=" * 70)
    print(f"ITBisa Shop Stock Bot — Balance mode {'(DRY RUN)' if dry_run else ''}")
    print("=" * 70)
    if len(base_skus) == 1:
        print(f"SKU: {base_skus[0]}")
    else:
        print(f"SKUs ({len(base_skus)}): {', '.join(base_skus)}")
    print()

    try:
        shopee_catalog, tiktokshop_catalog = _walk_balance_catalogs()
    except shopee_auth.RefreshTokenExpiredError as e:
        msg = (
            f"🔐 Otorisasi Shopee kadaluarsa. Mohon otorisasi ulang aplikasi "
            f"di Shopee Open Platform Console, lalu update file "
            f"data/shopee_tokens.json di branch bot-state. ({e})"
        )
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg, mode="Balance Stock")
        return 1

    results: list[dict] = []
    for base_sku in base_skus:
        if len(base_skus) > 1:
            print("-" * 70)
            print(f"Balancing: {base_sku}")
            print("-" * 70)
        results.append(
            _balance_one_sku(base_sku, shopee_catalog, tiktokshop_catalog, dry_run)
        )
        print()

    # One Telegram message per run, regardless of SKU count. Single-SKU
    # gets the detailed format; multi-SKU gets the compact format.
    if len(results) == 1:
        _send_single_balance_telegram(results[0], dry_run)
    else:
        telegram_sender.send_stock_balance_multi_summary({
            "results": results,
            "dry_run": dry_run,
        })

    if any(r["status"] not in ("ok", "dry_run") for r in results):
        return 1
    return 0


# ============================================================
# Set helpers
# ============================================================

def _set_one_sku(
        base_sku: str,
        total_pieces: int,
        shopee_catalog: dict,
        tiktokshop_catalog: dict,
        dry_run: bool,
) -> dict:
    """
    Push absolute stock for one base SKU against pre-fetched catalogs.

    No Telegram side effects — the caller decides between detailed (1 SKU)
    and compact (2+ SKU) summaries.

    Returns:
      {
        "base_sku":          str,
        "status":            "ok" | "dry_run" | "skipped" | "failed",
        "reason":            str,       # populated for skipped / failed
        "total_pieces":      int,
        "shopee_pieces":     int,
        "tiktokshop_pieces": int,
        "shopee_lines":      list[str],
        "tiktokshop_lines":  list[str],
        "shopee_status":     str,
        "tiktokshop_status": str,
      }
    """
    on_shopee = base_sku in shopee_catalog
    on_tiktokshop = base_sku in tiktokshop_catalog

    if not on_shopee and not on_tiktokshop:
        reason = f"SKU `{base_sku}` tidak ditemukan di Shopee maupun TikTok Shop."
        print(f"✗ {reason}")
        return _make_set_skip_result(base_sku, total_pieces, reason)

    if not on_shopee:
        reason = (
            f"SKU `{base_sku}` hanya ada di TikTok Shop (tidak di Shopee). "
            f"Tidak diproses (sesuai aturan)."
        )
        print(f"✗ {reason}")
        return _make_set_skip_result(base_sku, total_pieces, reason)

    if not on_tiktokshop:
        reason = (
            f"SKU `{base_sku}` hanya ada di Shopee (tidak di TikTok Shop). "
            f"Tidak diproses (sesuai aturan)."
        )
        print(f"✗ {reason}")
        return _make_set_skip_result(base_sku, total_pieces, reason)

    shopee_pieces, tiktokshop_pieces = split_across_platforms(total_pieces)

    shopee_lines, shopee_status = _format_and_push_shopee(
        base_sku, shopee_pieces, shopee_catalog[base_sku], dry_run
    )
    tiktokshop_lines, tiktokshop_status = _format_and_push_tiktokshop(
        base_sku, tiktokshop_pieces, tiktokshop_catalog[base_sku], dry_run
    )

    failed = "❌" in shopee_status or "❌" in tiktokshop_status
    if failed:
        reason_parts = []
        if "❌" in shopee_status:
            reason_parts.append(f"Shopee {shopee_status}")
        if "❌" in tiktokshop_status:
            reason_parts.append(f"TikTok Shop {tiktokshop_status}")
        status_label = "failed"
        reason = " | ".join(reason_parts)
    else:
        status_label = "dry_run" if dry_run else "ok"
        reason = ""

    return {
        "base_sku": base_sku,
        "status": status_label,
        "reason": reason,
        "total_pieces": total_pieces,
        "shopee_pieces": shopee_pieces,
        "tiktokshop_pieces": tiktokshop_pieces,
        "shopee_lines": shopee_lines,
        "tiktokshop_lines": tiktokshop_lines,
        "shopee_status": shopee_status,
        "tiktokshop_status": tiktokshop_status,
    }


def _make_set_skip_result(base_sku: str, total_pieces: int, reason: str) -> dict:
    """Result shape for a SKU that never got past presence checks."""
    return {
        "base_sku": base_sku,
        "status": "skipped",
        "reason": reason,
        "total_pieces": total_pieces,
        "shopee_pieces": 0,
        "tiktokshop_pieces": 0,
        "shopee_lines": [],
        "tiktokshop_lines": [],
        "shopee_status": "",
        "tiktokshop_status": "",
    }


def _send_single_set_telegram(result: dict, dry_run: bool) -> None:
    """Detailed Telegram for single-SKU set — preserves the per-variant
    breakdown that operators see when typing /stock_set SKU JUMLAH.

    Skipped means presence check failed (no lines worth showing) → alert.
    Everything else (ok, dry_run, failed) has lines worth showing → summary.
    """
    if result["status"] == "skipped":
        telegram_sender.send_alert(result["reason"])
        return

    telegram_sender.send_single_sku_summary({
        "mode": "single",
        "base_sku": result["base_sku"],
        "total_pieces": result["total_pieces"],
        "shopee_pieces": result["shopee_pieces"],
        "tiktokshop_pieces": result["tiktokshop_pieces"],
        "shopee_lines": result["shopee_lines"],
        "tiktokshop_lines": result["tiktokshop_lines"],
        "shopee_status": result["shopee_status"],
        "tiktokshop_status": result["tiktokshop_status"],
        "dry_run": dry_run,
    })


# ============================================================
# Balance helpers
# ============================================================

def _walk_balance_catalogs() -> tuple[dict, dict]:
    """
    Walks both platform catalogs once. Raises
    shopee_auth.RefreshTokenExpiredError to the caller.
    """
    print("[1/2] Walking Shopee catalog...")
    shopee_catalog = shopee_client.fetch_catalog()
    print(f"  → {len(shopee_catalog)} base SKU(s) on Shopee")

    print("[2/2] Walking TikTok Shop catalog...")
    tiktokshop_catalog = tiktokshop_client.fetch_catalog()
    print(f"  → {len(tiktokshop_catalog)} base SKU(s) on TikTok Shop")
    print()
    return shopee_catalog, tiktokshop_catalog


def _make_skip_result(base_sku: str, reason: str) -> dict:
    """Result shape for a SKU that never got past presence/total checks."""
    return {
        "base_sku": base_sku,
        "status": "skipped",
        "reason": reason,
        "total_pieces": 0,
        "shopee_before_pieces": 0,
        "tiktokshop_before_pieces": 0,
        "shopee_after_pieces": 0,
        "tiktokshop_after_pieces": 0,
        "shopee_lines": [],
        "tiktokshop_lines": [],
        "shopee_status": "",
        "tiktokshop_status": "",
    }


def _balance_one_sku(
        base_sku: str,
        shopee_catalog: dict,
        tiktokshop_catalog: dict,
        dry_run: bool,
) -> dict:
    """
    Rebalance one base SKU against pre-fetched catalogs.

    Returns a result dict (no Telegram side effect — the caller decides
    whether to send detailed or compact output):
      {
        "base_sku":                  str,
        "status":                    "ok" | "dry_run" | "skipped" | "failed",
        "reason":                    str,           # for skipped/failed
        "total_pieces":              int,
        "shopee_before_pieces":      int,
        "tiktokshop_before_pieces":  int,
        "shopee_after_pieces":       int,
        "tiktokshop_after_pieces":   int,
        "shopee_lines":              list[str],
        "tiktokshop_lines":          list[str],
        "shopee_status":             str,
        "tiktokshop_status":         str,
      }
    """
    on_shopee = base_sku in shopee_catalog
    on_tiktokshop = base_sku in tiktokshop_catalog

    # Balance requires BOTH platforms — nothing to redistribute if only
    # one side carries the SKU.
    if not on_shopee and not on_tiktokshop:
        reason = (
            f"SKU `{base_sku}` tidak ditemukan di Shopee maupun TikTok Shop."
        )
        print(f"✗ {reason}")
        return _make_skip_result(base_sku, reason)

    if not on_shopee:
        reason = (
            f"SKU `{base_sku}` hanya ada di TikTok Shop (tidak di Shopee). "
            f"Balance membutuhkan kedua platform."
        )
        print(f"✗ {reason}")
        return _make_skip_result(base_sku, reason)

    if not on_tiktokshop:
        reason = (
            f"SKU `{base_sku}` hanya ada di Shopee (tidak di TikTok Shop). "
            f"Balance membutuhkan kedua platform."
        )
        print(f"✗ {reason}")
        return _make_skip_result(base_sku, reason)

    shopee_variants = shopee_catalog[base_sku]
    tiktokshop_variants = tiktokshop_catalog[base_sku]

    # Sum physical pieces per platform: stock_units * pack-size multiplier.
    shopee_before_pieces = sum(
        v["stock_units"] * v["multiplier"] for v in shopee_variants
    )
    tiktokshop_before_pieces = sum(
        v["stock_units"] * v["multiplier"] for v in tiktokshop_variants
    )
    total_pieces = shopee_before_pieces + tiktokshop_before_pieces

    print(
        f"Sebelum: Shopee={shopee_before_pieces} + "
        f"TikTok Shop={tiktokshop_before_pieces} = {total_pieces} pcs"
    )

    if total_pieces == 0:
        reason = (
            f"SKU `{base_sku}` total stok = 0 di kedua platform. "
            f"Tidak ada yang perlu di-rebalance."
        )
        print(f"✗ {reason}")
        result = _make_skip_result(base_sku, reason)
        result["shopee_before_pieces"] = shopee_before_pieces
        result["tiktokshop_before_pieces"] = tiktokshop_before_pieces
        return result

    # Reuse the same split logic /stock_set uses, so behaviour stays
    # consistent across commands.
    shopee_after_pieces, tiktokshop_after_pieces = split_across_platforms(total_pieces)

    shopee_lines, shopee_status = _format_and_push_shopee(
        base_sku, shopee_after_pieces, shopee_variants, dry_run
    )
    tiktokshop_lines, tiktokshop_status = _format_and_push_tiktokshop(
        base_sku, tiktokshop_after_pieces, tiktokshop_variants, dry_run
    )

    failed = "❌" in shopee_status or "❌" in tiktokshop_status
    if failed:
        reason_parts = []
        if "❌" in shopee_status:
            reason_parts.append(f"Shopee {shopee_status}")
        if "❌" in tiktokshop_status:
            reason_parts.append(f"TikTok Shop {tiktokshop_status}")
        status_label = "failed"
        reason = " | ".join(reason_parts)
    else:
        status_label = "dry_run" if dry_run else "ok"
        reason = ""

    return {
        "base_sku": base_sku,
        "status": status_label,
        "reason": reason,
        "total_pieces": total_pieces,
        "shopee_before_pieces": shopee_before_pieces,
        "tiktokshop_before_pieces": tiktokshop_before_pieces,
        "shopee_after_pieces": shopee_after_pieces,
        "tiktokshop_after_pieces": tiktokshop_after_pieces,
        "shopee_lines": shopee_lines,
        "tiktokshop_lines": tiktokshop_lines,
        "shopee_status": shopee_status,
        "tiktokshop_status": tiktokshop_status,
    }


def _send_single_balance_telegram(result: dict, dry_run: bool) -> None:
    """Detailed Telegram for single-SKU balance — preserves the existing
    per-variant breakdown that operators see when typing /stock_balance SKU."""
    if result["status"] in ("ok", "dry_run"):
        telegram_sender.send_stock_balance_summary({
            "base_sku": result["base_sku"],
            "total_pieces": result["total_pieces"],
            "shopee_before_pieces": result["shopee_before_pieces"],
            "tiktokshop_before_pieces": result["tiktokshop_before_pieces"],
            "shopee_after_pieces": result["shopee_after_pieces"],
            "tiktokshop_after_pieces": result["tiktokshop_after_pieces"],
            "shopee_lines": result["shopee_lines"],
            "tiktokshop_lines": result["tiktokshop_lines"],
            "shopee_status": result["shopee_status"],
            "tiktokshop_status": result["tiktokshop_status"],
            "dry_run": dry_run,
        })
    else:
        telegram_sender.send_alert(result["reason"], mode="Balance Stock")


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
        lines.append(f"• `{raw}`: {units} unit (= {units * mult} pcs)")
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
        lines.append(f"• `{raw}`: {units} unit (= {units * mult} pcs)")
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