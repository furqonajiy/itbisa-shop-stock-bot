"""Price-aware /stock_set runner for --sku/--pieces mode."""

from __future__ import annotations

from src import config, shopee_auth, shopee_client, telegram_sender, tiktokshop_client
from src.main import (
    _format_and_push_shopee,
    _make_set_skip_result,
)
from src.shopee_detail_enrichment import enrich_shopee_prices
from src.stock_allocator import shopee_min_reserve_units, split_with_shopee_min_reserve
from src.stock_balance_price_rule import (
    _allocate_tiktokshop_balance,
    _build_shopee_detail_variants,
    _build_tiktokshop_detail_variants,
    _enrich_tiktokshop_details,
    _format_and_push_tiktokshop_allocations,
    _represented_pieces,
    _shopee_unit_price,
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

    # Single-platform SKUs are valid: a SKU listed on only one platform has
    # nothing to split with, so the full requested total goes to the platform
    # that has it (instead of skipping the SKU).
    if not on_tiktokshop:
        return _set_shopee_only(base_sku, total_pieces, shopee_catalog[base_sku], dry_run)

    if not on_shopee:
        return _set_tiktokshop_only(base_sku, total_pieces, tiktokshop_catalog[base_sku], dry_run)

    shopee_variants = shopee_catalog[base_sku]
    tiktokshop_variants = tiktokshop_catalog[base_sku]

    # Best-effort Shopee price lookup so we can reserve the minimum-purchase
    # quantity before the split — identical to /stock_balance. A price hiccup
    # must never break the set.
    try:
        enrich_shopee_prices(shopee_variants)
    except Exception as e:  # noqa: BLE001 - best-effort enrichment
        print(f"  [shopee] price enrichment failed; skipping min-purchase reserve: {e}")
    _enrich_shopee_wholesale_display(shopee_variants)
    _enrich_tiktokshop_details(tiktokshop_variants)

    # Same balancing logic as /stock_balance: reserve enough units to Shopee to
    # clear the reserve value, then split the remainder by SHOPEE_SPLIT_PERCENT.
    shopee_unit_price = _shopee_unit_price(shopee_variants)
    reserve_units = shopee_min_reserve_units(
        total_pieces, shopee_unit_price, config.SHOPEE_RESERVE_IDR
    )
    if reserve_units > 0:
        print(
            f"Shopee reserve: Rp{config.SHOPEE_RESERVE_IDR:,} ÷ "
            f"Rp{shopee_unit_price:,}/unit → {reserve_units} unit di-reserve ke "
            f"Shopee dulu, sisanya dibagi "
            f"{config.SHOPEE_SPLIT_PERCENT}:{100 - config.SHOPEE_SPLIT_PERCENT}."
        )
    shopee_target_pieces, tiktokshop_target_pieces = split_with_shopee_min_reserve(
        total_pieces,
        shopee_unit_price,
        config.SHOPEE_RESERVE_IDR,
        config.SHOPEE_SPLIT_PERCENT,
    )
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
        shopee_variants,
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
        "shopee_detail_variants": _build_shopee_display_variants(
            shopee_pieces,
            shopee_variants,
        ),
        "tiktokshop_detail_variants": _build_tiktokshop_detail_variants(
            tiktokshop_allocations,
        ),
        "shopee_status": shopee_status,
        "tiktokshop_status": tiktokshop_status,
    }


def _set_shopee_only(
        base_sku: str,
        total_pieces: int,
        shopee_variants: list[dict],
        dry_run: bool,
) -> dict:
    """SKU exists only on Shopee → set the full total there (no split)."""
    print(
        f"SKU `{base_sku}` hanya ada di Shopee → set 100% "
        f"({total_pieces} pcs) ke Shopee."
    )
    try:
        enrich_shopee_prices(shopee_variants)
    except Exception as e:  # noqa: BLE001 - best-effort enrichment (display only)
        print(f"  [shopee] price enrichment failed: {e}")
    _enrich_shopee_wholesale_display(shopee_variants)

    shopee_lines, shopee_status = _format_and_push_shopee(
        base_sku, total_pieces, shopee_variants, dry_run
    )

    if "❌" in shopee_status:
        status_label = "failed"
        reason = f"Shopee {shopee_status}"
    else:
        status_label = "dry_run" if dry_run else "ok"
        reason = ""

    return {
        "base_sku": base_sku,
        "status": status_label,
        "reason": reason,
        "total_pieces": total_pieces,
        "shopee_pieces": total_pieces,
        "tiktokshop_pieces": 0,
        "shopee_lines": shopee_lines,
        "tiktokshop_lines": [],
        "shopee_detail_variants": _build_shopee_display_variants(
            total_pieces, shopee_variants
        ),
        "tiktokshop_detail_variants": [],
        "shopee_status": shopee_status,
        "tiktokshop_status": "tidak ada di TikTok Shop",
    }


def _set_tiktokshop_only(
        base_sku: str,
        total_pieces: int,
        tiktokshop_variants: list[dict],
        dry_run: bool,
) -> dict:
    """SKU exists only on TikTok Shop → set the full total there (no split)."""
    print(
        f"SKU `{base_sku}` hanya ada di TikTok Shop → set 100% "
        f"({total_pieces} pcs) ke TikTok Shop."
    )
    _enrich_tiktokshop_details(tiktokshop_variants)

    allocations = _allocate_tiktokshop_balance(total_pieces, tiktokshop_variants)
    tiktokshop_pieces = _represented_pieces(allocations)
    leftover = total_pieces - tiktokshop_pieces
    if leftover:
        # No Shopee listing to absorb an unrepresentable remainder.
        print(
            f"⚠️ TikTok Shop hanya bisa merepresentasikan {tiktokshop_pieces}/"
            f"{total_pieces} pcs; {leftover} pcs tidak terwakili "
            f"(tidak ada Shopee untuk menampung)."
        )

    tiktokshop_lines, tiktokshop_status = _format_and_push_tiktokshop_allocations(
        allocations, dry_run
    )

    if "❌" in tiktokshop_status:
        status_label = "failed"
        reason = f"TikTok Shop {tiktokshop_status}"
    else:
        status_label = "dry_run" if dry_run else "ok"
        reason = ""

    return {
        "base_sku": base_sku,
        "status": status_label,
        "reason": reason,
        "total_pieces": total_pieces,
        "shopee_pieces": 0,
        "tiktokshop_pieces": tiktokshop_pieces,
        "shopee_lines": [],
        "tiktokshop_lines": tiktokshop_lines,
        "shopee_detail_variants": [],
        "tiktokshop_detail_variants": _build_tiktokshop_detail_variants(allocations),
        "shopee_status": "tidak ada di Shopee",
        "tiktokshop_status": tiktokshop_status,
    }


def _build_shopee_display_variants(target_pieces: int, variants: list[dict]) -> list[dict]:
    detail_variants = _build_shopee_detail_variants(target_pieces, variants)
    wholesale_by_raw_sku = {
        variant["raw_sku"]: variant.get("wholesale_tiers")
        for variant in variants
        if variant.get("wholesale_tiers")
    }
    for detail_variant in detail_variants:
        wholesale_tiers = wholesale_by_raw_sku.get(detail_variant.get("raw_sku"))
        if wholesale_tiers:
            detail_variant["wholesale_tiers"] = wholesale_tiers
    return detail_variants


def _enrich_shopee_wholesale_display(variants: list[dict]) -> None:
    """Best-effort: attach Shopee Harga Grosir tiers for Telegram display only."""
    by_item: dict[int, list[tuple[int, int, int]]] = {}
    for variant in variants:
        item_id = variant.get("item_id")
        if item_id is None:
            continue
        try:
            numeric_item_id = int(item_id)
            if numeric_item_id not in by_item:
                by_item[numeric_item_id] = shopee_client.get_wholesale(numeric_item_id)
            variant["wholesale_tiers"] = by_item[numeric_item_id]
        except Exception as e:  # noqa: BLE001 - display-only enrichment
            print(f"  [shopee] wholesale enrichment failed for item {item_id}: {e}")


def _send_single_set_telegram(result: dict, dry_run: bool) -> None:
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
        "shopee_detail_variants": result.get("shopee_detail_variants"),
        "tiktokshop_detail_variants": result.get("tiktokshop_detail_variants"),
        "shopee_status": result["shopee_status"],
        "tiktokshop_status": result["tiktokshop_status"],
        "dry_run": dry_run,
    })
