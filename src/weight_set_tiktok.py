"""
weight_set_tiktok.py
--------------------
/weight_set — set the per-piece weight across a TikTok Shop product's existing
pack-size variants via the Edit Product (202309) API.

**TikTok Shop only.** Weight is a per-SKU `sku_weight` attribute with no
standalone update endpoint, so this rides the same full-replace Edit Product
PUT as `/variant_set` (and reuses its category-resolution helpers). NOTE: the
per-SKU field is `sku_weight`; sending it as `package_weight` (a product-level
field) is silently ignored and every variant collapses to the product weight.
`sku_weight` is sent in GRAM (not KILOGRAM — a small per-piece weight like 8.5 g
becomes 0.0085 kg and TikTok rounds it to zero → error 12052181) with a 1 g
minimum floor.

Input is a reference pack + its total weight, e.g. `/weight_set <BASE_SKU>
1000PCS 1700g` → per-piece weight = 1700 g / 1000 = 1.7 g/pcs. Each variant's
weight is then `per_pcs × its multiplier` (1PCS → 1.7 g, 20PCS → 34 g, …). The
existing variation set, stock, and prices are PRESERVED — only weights change.
The always-present `ITBISA-BUBBLE-WRAP` value keeps its own weight (it is not a
pack of the product).
"""

from __future__ import annotations

import json

from src import shopee_auth, telegram_sender, tiktokshop_client
from src.stock_allocator import parse_sku
from src.variant_set_tiktok import (
    BUBBLE_WRAP_SELLER_SKU,
    BUBBLE_WRAP_VALUE_NAME,
    BUBBLE_WRAP_WEIGHT_G,
    MIN_SKU_WEIGHT_G,
    _edit_attributes,
    _match_v2_category_by_name,
    _sku_price_idr,
    _sku_weight_grams,
    _v1_leaf_name,
    _v2_category_id,
)

_CURRENCY = "IDR"


# ----------------------------------------------------------------------
# Pure helper
# ----------------------------------------------------------------------
def build_weight_edit_payload(
        detail: dict,
        base_sku: str,
        ref_multiplier: int,
        ref_weight_grams: float,
) -> dict:
    """Build the Edit Product payload that re-weights every existing variant.

    per_pcs = ref_weight_grams / ref_multiplier (grams); each pack variant's
    weight is `per_pcs × its multiplier`, sent in GRAM with a 1 g floor. Stock
    (inventory) and price are carried over unchanged; Bubble Wrap keeps its
    existing weight. Pure given `detail`.
    """
    skus_in = detail.get("skus") or []
    if not skus_in:
        raise ValueError("product has no SKUs to re-weight")
    if ref_multiplier <= 0:
        raise ValueError("reference pack size must be >= 1")
    if ref_weight_grams <= 0:
        raise ValueError("reference weight must be > 0 grams")

    per_pcs_g = ref_weight_grams / ref_multiplier

    target: list[dict] = []
    for s in skus_in:
        attr = (s.get("sales_attributes") or [{}])[0]
        value_name = attr.get("value_name")
        seller_sku = s.get("seller_sku") or ""

        if seller_sku == BUBBLE_WRAP_SELLER_SKU or value_name == BUBBLE_WRAP_VALUE_NAME:
            weight_g = _sku_weight_grams(s) or float(BUBBLE_WRAP_WEIGHT_G)
        else:
            _, mult = parse_sku(seller_sku)
            weight_g = per_pcs_g * mult
        weight_g = max(float(MIN_SKU_WEIGHT_G), round(weight_g, 2))

        sales_attr = {"id": attr.get("id"), "name": attr.get("name"), "value_name": value_name}
        if attr.get("value_id"):
            sales_attr["value_id"] = attr["value_id"]

        sku = {
            "seller_sku": seller_sku,
            "sales_attributes": [sales_attr],
            "inventory": [
                {"warehouse_id": inv.get("warehouse_id"), "quantity": int(inv.get("quantity") or 0)}
                for inv in (s.get("inventory") or []) if inv.get("warehouse_id")
            ],
            # Per-variant weight is `sku_weight` (matches the product detail),
            # sent in GRAM and floored at MIN_SKU_WEIGHT_G — TikTok rejects a
            # weight that rounds to zero (error 12052181) and 8.5 g sent as
            # 0.0085 kg rounds to 0. `package_weight` per SKU is ignored by Edit
            # Product (every variant would collapse to the product-level weight).
            "sku_weight": {"value": f"{weight_g:g}", "unit": "GRAM"},
        }
        price = _sku_price_idr(s)
        if price is not None:
            sku["price"] = {"amount": str(int(price)), "currency": _CURRENCY}
        target.append(sku)

    payload = {
        "title": detail.get("title"),
        "description": detail.get("description"),
        "category_version": "v2",
        "main_images": [
            {"uri": img["uri"]}
            for img in (detail.get("main_images") or []) if img.get("uri")
        ],
        "package_weight": detail.get("package_weight"),
        "package_dimensions": detail.get("package_dimensions"),
        "product_attributes": _edit_attributes(detail.get("product_attributes")),
        "skus": target,
    }
    v2_category = _v2_category_id(detail)
    if v2_category:
        payload["category_id"] = v2_category
    return payload


def _variant_weight_lines(payload: dict) -> list[str]:
    out = []
    for s in payload["skus"]:
        vn = (s.get("sales_attributes") or [{}])[0].get("value_name")
        w = (s.get("sku_weight") or {}).get("value")
        out.append(f"• {vn} = {w} g")
    return out


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------
def run_weight_set(base_sku: str, ref_multiplier: int, ref_weight_grams: float, dry_run: bool) -> int:
    per_pcs_g = ref_weight_grams / ref_multiplier if ref_multiplier else 0
    print("=" * 70)
    print(f"ITBisa Shop Stock Bot — Weight set {'(DRY RUN)' if dry_run else ''}")
    print("=" * 70)
    print(f"SKU: {base_sku}")
    print(f"Reference: {ref_multiplier}PCS = {ref_weight_grams} g  ->  {per_pcs_g:g} g/pcs")
    print()

    try:
        print("Walking TikTok Shop catalog...")
        catalog = tiktokshop_client.fetch_catalog()
    except shopee_auth.RefreshTokenExpiredError as e:
        msg = f"🔐 Otorisasi kadaluarsa. ({e})"
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg, mode="Weight")
        return 1
    except Exception as e:  # noqa: BLE001
        msg = f"Gagal membaca katalog TikTok Shop: {e}"
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg, mode="Weight")
        return 1

    variants = catalog.get(base_sku)
    if not variants:
        msg = f"SKU `{base_sku}` tidak ditemukan di TikTok Shop."
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg, mode="Weight")
        return 1

    product_id = variants[0]["product_id"]
    try:
        detail = tiktokshop_client.fetch_product_detail_raw(product_id)
        payload = build_weight_edit_payload(detail, base_sku, ref_multiplier, ref_weight_grams)
        # Edit Product requires a V2 category_id; resolve by name from the V2
        # tree when the detail offers no recommendation (same as /variant_set).
        if "category_id" not in payload:
            matched = _match_v2_category_by_name(
                tiktokshop_client.fetch_categories(), _v1_leaf_name(detail)
            )
            if matched:
                payload["category_id"] = matched
        print(f"  [weight] category_id in payload = {payload.get('category_id')}")
    except Exception as e:  # noqa: BLE001
        msg = f"Gagal menyusun payload Edit Product untuk `{base_sku}`: {e}"
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg, mode="Weight")
        return 1

    weight_lines = _variant_weight_lines(payload)
    print(f"Product: {product_id}")
    for line in weight_lines:
        print(f"  {line}")
    print(f"EDIT PRODUCT PAYLOAD: {json.dumps(payload, ensure_ascii=False)[:5500]}")

    summary = {
        "base_sku": base_sku,
        "per_pcs_g": per_pcs_g,
        "weight_lines": weight_lines,
        "dry_run": dry_run,
    }

    if dry_run:
        summary["status"] = "🔍 dry-run"
        telegram_sender.send_weight_set_summary(summary)
        return 0

    try:
        tiktokshop_client.edit_product(product_id, payload)
    except Exception as e:  # noqa: BLE001
        msg = f"❌ Set Weight gagal untuk `{base_sku}`: {e}"
        print(msg)
        summary["status"] = f"❌ gagal: {e}"
        telegram_sender.send_weight_set_summary(summary)
        return 1

    summary["status"] = "✅ berhasil"
    telegram_sender.send_weight_set_summary(summary)
    return 0
