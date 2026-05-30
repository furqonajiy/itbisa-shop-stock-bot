"""Compact /stock_get runner with marketplace detail price and weight enrichment."""

from __future__ import annotations

from src import shopee_auth, shopee_client, telegram_sender, tiktokshop_client
from src.shopee_detail_enrichment import enrich_shopee_prices
from src.stock_balance_price_rule import _fetch_tiktokshop_sku_details


def parse_stock_get_skus(raw_sku: str) -> list[str]:
    """Parse one or more base SKUs from newline-separated input."""
    return [sku.strip().upper() for sku in (raw_sku or "").splitlines() if sku.strip()]


def run_stock_get_mode(base_sku: str) -> int:
    """Read-only stock inspection for one or more base SKUs across both platforms."""
    base_skus = parse_stock_get_skus(base_sku)
    if not base_skus:
        print("✗ --sku must not be empty.")
        return 2
    if len(base_skus) == 1:
        return _run_single_stock_get_mode(base_skus[0])
    return run_stock_get_multi_mode(base_skus)


def run_stock_get_multi_mode(base_skus: list[str]) -> int:
    """Read-only compact stock inspection for multiple base SKUs."""
    print("=" * 70)
    print("ITBisa Shop Stock Bot — Get mode (read-only, multi SKU)")
    print("=" * 70)
    print(f"SKU count: {len(base_skus)}")
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

    results: list[dict] = []
    for sku in base_skus:
        shopee_variants = shopee_catalog.get(sku, [])
        tiktokshop_variants = tiktokshop_catalog.get(sku, [])

        if not shopee_variants and not tiktokshop_variants:
            reason = (
                f"SKU `{sku}` tidak ditemukan di Shopee maupun TikTok Shop. "
                f"Periksa SKU dan coba lagi."
            )
            print(f"✗ {reason}")
            results.append({"base_sku": sku, "status": "failed", "reason": reason})
            continue

        shopee_total_pcs = _total_pieces(shopee_variants)
        tiktokshop_total_pcs = _total_pieces(tiktokshop_variants)
        print(f"✓ {sku}")
        print(f"  Shopee: {shopee_total_pcs} pcs")
        print(f"  TikTok Shop: {tiktokshop_total_pcs} pcs")
        results.append({
            "base_sku": sku,
            "status": "ok",
            "shopee_pieces": shopee_total_pcs,
            "tiktokshop_pieces": tiktokshop_total_pcs,
            "total_pieces": shopee_total_pcs + tiktokshop_total_pcs,
        })

    telegram_sender.send_stock_get_multi_summary({"results": results})
    return 0 if all(r["status"] == "ok" for r in results) else 1


def _run_single_stock_get_mode(base_sku: str) -> int:
    """Read-only stock inspection for one base SKU across both platforms."""
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

    enrich_shopee_prices(shopee_variants)
    _enrich_tiktokshop_detail(tiktokshop_variants)

    for label, variants in (("Shopee", shopee_variants), ("TikTok Shop", tiktokshop_variants)):
        print(f"  {label}: {len(variants)} varian")
        for v in variants:
            price_note = ""
            if v.get("price_idr") is not None:
                price_note = f", Rp{int(v['price_idr']):,}".replace(",", ".")
            print(
                f"    • {v['raw_sku']} (×{v['multiplier']}): "
                f"{v['stock_units']} unit, {v.get('weight_grams') or 0} g{price_note}"
            )

    telegram_sender.send_stock_get_summary({
        "base_sku": base_sku,
        "shopee_variants": shopee_variants,
        "tiktokshop_variants": tiktokshop_variants,
    })
    return 0


def _total_pieces(variants: list[dict]) -> int:
    return sum(int(v["stock_units"]) * int(v["multiplier"]) for v in variants)


def _enrich_tiktokshop_detail(variants: list[dict]) -> None:
    """Attach TikTok Shop price_idr and SKU weight_grams from product detail."""
    if not variants:
        return

    detail_by_sku_id: dict[str, dict[str, int | None]] = {}
    product_ids = {v["product_id"] for v in variants if v.get("product_id")}
    for product_id in product_ids:
        try:
            detail_by_sku_id.update(_fetch_tiktokshop_sku_details(product_id))
        except Exception as e:
            print(f"  [tiktokshop] product detail failed for {product_id}: {e}")

    for variant in variants:
        detail = detail_by_sku_id.get(variant.get("sku_id")) or {}
        if detail.get("price_idr") is not None:
            variant["price_idr"] = detail["price_idr"]
        if detail.get("weight_grams"):
            variant["weight_grams"] = detail["weight_grams"]
