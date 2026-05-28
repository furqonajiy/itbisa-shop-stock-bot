"""
stock_balance_preserve.py
-------------------------
/stock_balance orchestration that preserves the existing grand total.

For TikTok Shop pack-size products where the allocator cannot represent the
50:50 target exactly (for example 1PCS fixed to 1 unit plus 25PCS packs), the
unrepresentable TikTok Shop leftover is assigned to Shopee before pushing.
"""

from __future__ import annotations

from src import shopee_auth, telegram_sender
from src.main import (
    _format_and_push_shopee,
    _format_and_push_tiktokshop,
    _make_skip_result,
    _send_single_balance_telegram,
    _walk_balance_catalogs,
)
from src.stock_allocator import (
    allocate_pack_sizes,
    split_across_platforms,
    verify_allocation,
)


def run_stock_balance_multi_preserve_total(base_skus: list[str], dry_run: bool) -> int:
    """
    Rebalance one or more base SKUs while preserving the grand total.

    Exact call site: scripts/stock_balance.py imports this function instead of
    src.main.run_stock_balance_multi for the /stock_balance CLI.
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
            _balance_one_sku_preserve_total(
                base_sku, shopee_catalog, tiktokshop_catalog, dry_run
            )
        )
        print()

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


def _balance_one_sku_preserve_total(
        base_sku: str,
        shopee_catalog: dict,
        tiktokshop_catalog: dict,
        dry_run: bool,
) -> dict:
    """Balance one SKU; move TikTok Shop unrepresentable leftover to Shopee."""
    on_shopee = base_sku in shopee_catalog
    on_tiktokshop = base_sku in tiktokshop_catalog

    if not on_shopee and not on_tiktokshop:
        reason = f"SKU `{base_sku}` tidak ditemukan di Shopee maupun TikTok Shop."
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

    shopee_target_pieces, tiktokshop_target_pieces = split_across_platforms(total_pieces)
    tiktokshop_after_pieces, leftover_for_shopee = _representable_tiktokshop_pieces(
        tiktokshop_target_pieces,
        tiktokshop_variants,
    )
    shopee_after_pieces = shopee_target_pieces + leftover_for_shopee

    if leftover_for_shopee:
        print(
            f"TikTok Shop target {tiktokshop_target_pieces} pcs hanya bisa "
            f"direpresentasikan {tiktokshop_after_pieces} pcs; "
            f"{leftover_for_shopee} pcs dialihkan ke Shopee."
        )

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


def _representable_tiktokshop_pieces(
        target_pieces: int,
        variants: list[dict],
) -> tuple[int, int]:
    """
    Return (represented_pieces, leftover_for_shopee) for TikTok Shop target.
    """
    allocations = allocate_pack_sizes(
        target_pieces,
        variants,
        tiktokshop_unit_cap=__import__("src.config", fromlist=["TIKTOKSHOP_MAX_UNITS_PER_VARIANT"])
        .TIKTOKSHOP_MAX_UNITS_PER_VARIANT,
    )
    leftover = verify_allocation(target_pieces, allocations)
    return target_pieces - leftover, leftover
