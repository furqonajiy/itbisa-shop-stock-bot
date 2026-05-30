"""
stock_balance_price_rule.py
---------------------------
/stock_balance orchestration for TikTok Shop low-price 1PCS variants.

Rules:
  - Split the current grand total 50:50 between Shopee and TikTok Shop.
  - On TikTok Shop only, when the 1PCS variant price is below Rp5.000,
    cap that 1PCS variant at max 1 unit.
  - Allocate the rest of the TikTok Shop share to the other pack-size variants.
  - Move any TikTok Shop unrepresentable leftover to Shopee so the existing
    grand total is preserved.
"""

from __future__ import annotations

import re
import time
from typing import Any

from src import config, shopee_auth, telegram_sender, tiktokshop_client
from src.main import (
    _format_and_push_shopee,
    _make_skip_result,
    _send_single_balance_telegram,
    _walk_balance_catalogs,
)
from src.stock_allocator import (
    allocate_pack_sizes,
    split_across_platforms,
)

LOW_PRICE_1PCS_THRESHOLD_IDR = 5000
LOW_PRICE_1PCS_MAX_UNITS = 1


def run_stock_balance_multi(base_skus: list[str], dry_run: bool) -> int:
    """
    Rebalance one or more base SKUs with the low-price 1PCS TikTok Shop rule.

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
            _balance_one_sku(base_sku, shopee_catalog, tiktokshop_catalog, dry_run)
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


def _balance_one_sku(
        base_sku: str,
        shopee_catalog: dict,
        tiktokshop_catalog: dict,
        dry_run: bool,
) -> dict:
    """Balance one SKU; move TikTok Shop unrepresentable leftovers to Shopee."""
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

    _enrich_tiktokshop_prices(tiktokshop_variants)

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
    tiktokshop_allocations = _allocate_tiktokshop_balance(
        tiktokshop_target_pieces,
        tiktokshop_variants,
    )
    tiktokshop_after_pieces = _represented_pieces(tiktokshop_allocations)
    leftover_for_shopee = tiktokshop_target_pieces - tiktokshop_after_pieces
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
        "shopee_before_pieces": shopee_before_pieces,
        "tiktokshop_before_pieces": tiktokshop_before_pieces,
        "shopee_after_pieces": shopee_after_pieces,
        "tiktokshop_after_pieces": tiktokshop_after_pieces,
        "shopee_lines": shopee_lines,
        "tiktokshop_lines": tiktokshop_lines,
        "shopee_status": shopee_status,
        "tiktokshop_status": tiktokshop_status,
    }


def _allocate_tiktokshop_balance(
        target_pieces: int,
        variants: list[dict],
) -> list[tuple[dict, int]]:
    """Apply low-price 1PCS cap, then allocate the remaining TikTok Shop share."""
    variants = sorted(variants, key=lambda v: v["multiplier"])
    low_price_1pcs = _find_low_price_1pcs_variant(variants)
    if low_price_1pcs is None:
        return allocate_pack_sizes(
            target_pieces,
            variants,
            tiktokshop_unit_cap=config.TIKTOKSHOP_MAX_UNITS_PER_VARIANT,
        )

    allocations: list[tuple[dict, int]] = []
    reserved_units = min(LOW_PRICE_1PCS_MAX_UNITS, target_pieces)
    remaining_pieces = target_pieces - reserved_units
    other_variants: list[dict] = []

    for variant in variants:
        if variant is low_price_1pcs:
            allocations.append((variant, reserved_units))
        else:
            allocations.append((variant, 0))
            other_variants.append(variant)

    if remaining_pieces > 0 and other_variants:
        other_allocations = allocate_pack_sizes(
            remaining_pieces,
            other_variants,
            tiktokshop_unit_cap=config.TIKTOKSHOP_MAX_UNITS_PER_VARIANT,
        )
        other_units_by_id = {id(v): units for v, units in other_allocations}
        allocations = [
            (variant, units if variant is low_price_1pcs else other_units_by_id[id(variant)])
            for variant, units in allocations
        ]

    print(
        f"TikTok Shop 1PCS price Rp{low_price_1pcs.get('price_idr'):,} < "
        f"Rp{LOW_PRICE_1PCS_THRESHOLD_IDR:,}; 1PCS dibatasi max "
        f"{LOW_PRICE_1PCS_MAX_UNITS} unit."
    )
    return allocations


def _find_low_price_1pcs_variant(variants: list[dict]) -> dict | None:
    for variant in variants:
        if variant.get("multiplier") != 1:
            continue
        price_idr = variant.get("price_idr")
        if price_idr is not None and price_idr < LOW_PRICE_1PCS_THRESHOLD_IDR:
            return variant
    return None


def _format_and_push_tiktokshop_allocations(
        allocations: list[tuple[dict, int]],
        dry_run: bool,
) -> tuple[list[str], str]:
    """Returns (formatted_lines, status_string) for precomputed TikTok allocations."""
    lines: list[str] = []
    by_product: dict[str, list[tuple[str, str, int]]] = {}

    for variant, units in allocations:
        raw = variant["raw_sku"]
        mult = variant["multiplier"]
        price_note = _format_price_note(variant.get("price_idr"))
        lines.append(f"• `{raw}`: {units} unit (= {units * mult} pcs){price_note}")
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


def _enrich_tiktokshop_prices(variants: list[dict]) -> None:
    """Best-effort: attach price_idr to variants from product detail responses."""
    product_ids = {v["product_id"] for v in variants if v.get("product_id")}
    price_by_sku_id: dict[str, int | None] = {}
    for product_id in product_ids:
        price_by_sku_id.update(_fetch_tiktokshop_prices(product_id))

    for variant in variants:
        variant["price_idr"] = price_by_sku_id.get(variant.get("sku_id"))


def _fetch_tiktokshop_prices(product_id: str) -> dict[str, int | None]:
    path = f"/product/202309/products/{product_id}"
    response = tiktokshop_client._call_signed(  # noqa: SLF001 - stock bot internal API client reuse
        "GET",
        path,
        extra_query={"version": "202309"},
    )
    tiktokshop_client._check_ok(  # noqa: SLF001 - stock bot internal API client reuse
        response,
        context=f"product detail price product={product_id}",
    )

    data = response.json().get("data") or {}
    result: dict[str, int | None] = {}
    for sku in data.get("skus") or []:
        sku_id = sku.get("id")
        if sku_id:
            result[sku_id] = _extract_price_idr(sku)
    return result


def _extract_price_idr(value: Any) -> int | None:
    """Extract an IDR numeric price from common TikTok Shop price shapes."""
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return round(value)

    if isinstance(value, str):
        return _parse_price_string(value)

    if isinstance(value, list):
        for item in value:
            parsed = _extract_price_idr(item)
            if parsed is not None:
                return parsed
        return None

    if isinstance(value, dict):
        for key in (
            "price",
            "sale_price",
            "tax_exclusive_price",
            "retail_price",
            "original_price",
            "list_price",
            "amount",
            "value",
        ):
            parsed = _extract_price_idr(value.get(key))
            if parsed is not None:
                return parsed
    return None


def _parse_price_string(value: str) -> int | None:
    cleaned = value.strip()
    if not cleaned:
        return None

    # Handles API values like "5000", "5000.00", "Rp5.000",
    # "5,000", and Indonesian-style "Rp5.000,00".
    numeric = re.sub(r"[^0-9.,]", "", cleaned)
    if not numeric:
        return None

    if "," in numeric and "." in numeric:
        last_comma = numeric.rfind(",")
        last_dot = numeric.rfind(".")
        if last_comma > last_dot:
            numeric = numeric.replace(".", "").replace(",", ".")
        else:
            numeric = numeric.replace(",", "")
    elif "," in numeric:
        cents_or_decimals = numeric.rsplit(",", 1)[1]
        if len(cents_or_decimals) <= 2:
            numeric = numeric.replace(",", ".")
        else:
            numeric = numeric.replace(",", "")
    elif numeric.count(".") == 1:
        cents_or_decimals = numeric.rsplit(".", 1)[1]
        if len(cents_or_decimals) == 3:
            numeric = numeric.replace(".", "")

    try:
        return round(float(numeric))
    except ValueError:
        return None


def _represented_pieces(allocations: list[tuple[dict, int]]) -> int:
    return sum(variant["multiplier"] * units for variant, units in allocations)


def _format_price_note(price_idr: int | None) -> str:
    if price_idr is None:
        return ""
    return f" — Rp{price_idr:,}".replace(",", ".")
