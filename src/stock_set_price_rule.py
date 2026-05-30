"""Price-aware /stock_set runner for --sku/--pieces mode."""

from __future__ import annotations

from src import config, shopee_auth, shopee_client, telegram_sender, tiktokshop_client
from src.main import (
    _format_and_push_shopee,
    _make_set_skip_result,
    _send_single_set_telegram,
)
from src.stock_allocator import split_across_platforms
from src.stock_balance_price_rule import (
    _allocate_tiktokshop_balance,
    _enrich_tiktokshop_prices,
    _format_and_push_tiktokshop_allocations,
    _represented_pieces,
)


def run_stock_set_multi(desired: dict[str, int], dry_run: bool) -> int:
    if not desired:
        msg = "Tidak ada SKU diberikan ke set mode."
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg)
        return 1

    print("=" * 70)
    print(f"ITBisa Shop Stock Bot — Set mode {'(DRY RUN)' if dry_run else ''}")
    print("=" * 70)
    print(f"TikTok Shop unit cap per variant: {config.TIKTOKSHOP_MAX_UNITS_PER_VARIANT}")
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
        msg = f"🔐 Otorisasi Shopee kadaluarsa. Mohon otorisasi ulang. ({e})"
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
                base_sku,
                total_pieces,
                shopee_catalog,
                tiktokshop_catalog,
                dry_run,
            )
        )
        print()

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


def _set_one_sku(
        base_sku: str,
        total_pieces: int,
        shopee_catalog: dict,
        tiktokshop_catalog: dict,
        dry_run: bool,
) -> dict:
    on_shopee = base_sku in shopee_catalog
    on_tiktokshop = base_sku in tiktokshop_catalog

    if not on_shopee and not on_tiktokshop:
        reason = f"SKU `{base_sku}` tidak ditemukan di Shopee maupun TikTok Shop."
        print(f"✗ {reason}")
        return _make_set_skip_result(base_sku, total_pieces, reason)

    if not on_shopee:
        reason = f"SKU `{base_sku}` hanya ada di TikTok Shop (tidak di Shopee). Tidak diproses."
        print(f"✗ {reason}")
        return _make_set_skip_result(base_sku, total_pieces, reason)

    if not on_tiktokshop:
        reason = f"SKU `{base_sku}` hanya ada di Shopee (tidak di TikTok Shop). Tidak diproses."
        print(f"✗ {reason}")
        return _make_set_skip_result(base_sku, total_pieces, reason)

    shopee_target_pieces, tiktokshop_target_pieces = split_across_platforms(total_pieces)
    tiktokshop_variants = tiktokshop_catalog[base_sku]
    _enrich_tiktokshop_prices(tiktokshop_variants)
    tiktokshop_allocations = _allocate_tiktokshop_balance(
        tiktokshop_target_pieces,
        tiktokshop_variants,
    )
    tiktokshop_pieces = _represented_pieces(tiktokshop_allocations)
    leftover_for_shopee = tiktokshop_target_pieces - tiktokshop_pieces
    shopee_pieces = shopee_target_pieces + leftover_for_shopee

    if leftover_for_shopee:
        print(
            f"TikTok Shop target {tiktokshop_target_pieces} pcs hanya bisa "
            f"direpresentasikan {tiktokshop_pieces} pcs; "
            f"{leftover_for_shopee} pcs dialihkan ke Shopee."
        )

    shopee_lines, shopee_status = _format_and_push_shopee(
        base_sku,
        shopee_pieces,
        shopee_catalog[base_sku],
        dry_run,
    )
    tiktokshop_lines, tiktokshop_status = _format_and_push_tiktokshop_allocations(
        tiktokshop_allocations,
        dry_run,
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
