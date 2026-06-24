"""
variant_set_tiktok.py
---------------------
/variant_set — rebuild a TikTok Shop product's pack-size variation to an exact
set of pack sizes via the Edit Product (202309) API.

**TikTok Shop only.** Full-replace semantics: after the run the product's
variation ("Packing") has exactly the requested pack sizes PLUS a standard
`ITBISA-BUBBLE-WRAP` value (stock 0, price 100) — any other existing values
(e.g. an unwanted 5PCS/100PCS) are dropped.

Stock-safe flow (operator): save the combined total first, run /variant_set,
then `/stock_set <base_sku> <total>` to re-apply + split the saved stock across
the rebuilt variants. New variants are created at stock 0.

Naming: the 1PCS variant's seller_sku is the bare base SKU; pack variants are
`<M>PCS-<base_sku>`. Variant images are NOT set — the "Packing" variation is
text-only, so TikTok shows the product main image by default.

The Edit Product request schema is best-effort and pending live verification
(login-gated docs); always run with --dry-run first and inspect the logged
payload before a live submit.
"""

from __future__ import annotations

import json

from src import shopee_auth, telegram_sender, tiktokshop_client

BUBBLE_WRAP_SELLER_SKU = "ITBISA-BUBBLE-WRAP"
BUBBLE_WRAP_VALUE_NAME = "Bubble Wrap"
BUBBLE_WRAP_PRICE_IDR = 100
BUBBLE_WRAP_WEIGHT_KG = 0.001
PACKING_ATTR_NAME = "Packing"
_CURRENCY = "IDR"


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------
def _value_name(multiplier: int) -> str:
    return f"{multiplier}PCS"


def _seller_sku(base_sku: str, multiplier: int) -> str:
    return base_sku if multiplier == 1 else f"{multiplier}PCS-{base_sku}"


def _sku_price_idr(sku: dict | None) -> int | None:
    if not sku:
        return None
    price = sku.get("price") or {}
    for key in ("sale_price", "amount", "tax_exclusive_price"):
        if price.get(key) not in (None, ""):
            try:
                return int(float(price[key]))
            except (TypeError, ValueError):
                continue
    return None


def _sku_weight_kg(sku: dict | None) -> float | None:
    if not sku:
        return None
    w = sku.get("sku_weight") or sku.get("package_weight") or {}
    try:
        return float(w.get("value"))
    except (TypeError, ValueError):
        return None


def _leaf_category_id(detail: dict) -> str | None:
    chains = detail.get("category_chains") or []
    for c in chains:
        if c.get("is_leaf"):
            return c.get("id")
    return chains[-1].get("id") if chains else None


def _v2_category_id(detail: dict) -> str | None:
    """Return a V2 leaf category id if the product detail offers one, else None.

    Edit Product rejects the product's legacy V1 `category_chains` leaf
    (error 12052217 "must use V2 categories"). Only `recommended_categories`
    (when non-empty) carries V2 nodes — prefer its leaf. We deliberately do NOT
    fall back to the V1 chain leaf (that triggers the rejection); when no V2 id
    is available we omit category_id entirely so TikTok keeps the product's
    current category and skips re-validation.
    """
    recs = detail.get("recommended_categories") or []
    leaf = None
    for c in recs:
        if c.get("is_leaf"):
            leaf = c.get("id")
    if leaf:
        return leaf
    if recs and recs[-1].get("id"):
        return recs[-1]["id"]
    return None


def _edit_attributes(product_attributes) -> list[dict]:
    out = []
    for a in product_attributes or []:
        vals = [{"id": v["id"]} for v in (a.get("values") or []) if v.get("id")]
        if a.get("id") and vals:
            out.append({"id": a["id"], "values": vals})
    return out


def _build_sku(attr_id, attr_name, existing, value_name, seller_sku,
               price_idr, weight_kg, warehouse_id, qty):
    sales_attr = {"id": attr_id, "name": attr_name, "value_name": value_name}
    # Reuse the existing value_id when the value already exists, so TikTok keeps
    # it; omit value_id for brand-new values (TikTok creates them by name).
    if existing:
        ex_attr = (existing.get("sales_attributes") or [{}])[0]
        if ex_attr.get("value_id"):
            sales_attr["value_id"] = ex_attr["value_id"]
    sku = {
        "seller_sku": seller_sku,
        "sales_attributes": [sales_attr],
        "price": {"amount": str(int(price_idr)), "currency": _CURRENCY},
        "inventory": [{"warehouse_id": warehouse_id, "quantity": int(qty)}],
        "package_weight": {"value": f"{weight_kg:g}", "unit": "KILOGRAM"},
    }
    return sku


def build_edit_payload(detail: dict, base_sku: str, pack_sizes: list[int]) -> dict:
    """Construct the Edit Product payload that sets `Packing` to exactly the
    given pack sizes (+ Bubble Wrap). Pure given `detail`.

    New pack variants get stock 0 and a placeholder price/weight scaled from the
    1PCS variant (refined later by /harga_set and /weight_set); existing values
    keep their value_id, price, and weight.
    """
    skus_in = detail.get("skus") or []
    if not skus_in:
        raise ValueError("product has no SKUs to read the variation/warehouse from")

    first_attr = (skus_in[0].get("sales_attributes") or [{}])[0]
    attr_id = first_attr.get("id")
    attr_name = first_attr.get("name") or PACKING_ATTR_NAME
    warehouse_id = ((skus_in[0].get("inventory") or [{}])[0]).get("warehouse_id")

    existing: dict[str, dict] = {}
    ref_unit_price = None
    ref_unit_weight = None
    for s in skus_in:
        vn = ((s.get("sales_attributes") or [{}])[0]).get("value_name")
        if vn:
            existing[vn] = s
        if vn == _value_name(1):
            ref_unit_price = _sku_price_idr(s)
            ref_unit_weight = _sku_weight_kg(s)
    if ref_unit_price is None:
        ref_unit_price = _sku_price_idr(skus_in[0]) or 1
    if ref_unit_weight is None:
        ref_unit_weight = _sku_weight_kg(skus_in[0]) or 0.001

    target: list[dict] = []
    for m in sorted(set(int(p) for p in pack_sizes)):
        vn = _value_name(m)
        ex = existing.get(vn)
        price = _sku_price_idr(ex) if ex else ref_unit_price * m
        weight = _sku_weight_kg(ex) if ex else round(ref_unit_weight * m, 4)
        target.append(_build_sku(
            attr_id, attr_name, ex, vn, _seller_sku(base_sku, m),
            price, weight, warehouse_id, qty=0,
        ))

    bw = existing.get(BUBBLE_WRAP_VALUE_NAME)
    target.append(_build_sku(
        attr_id, attr_name, bw, BUBBLE_WRAP_VALUE_NAME, BUBBLE_WRAP_SELLER_SKU,
        BUBBLE_WRAP_PRICE_IDR, _sku_weight_kg(bw) or BUBBLE_WRAP_WEIGHT_KG,
        warehouse_id, qty=0,
    ))

    payload = {
        "title": detail.get("title"),
        "description": detail.get("description"),
        "main_images": [
            {"uri": img["uri"]}
            for img in (detail.get("main_images") or []) if img.get("uri")
        ],
        "package_weight": detail.get("package_weight"),
        "package_dimensions": detail.get("package_dimensions"),
        "product_attributes": _edit_attributes(detail.get("product_attributes")),
        "skus": target,
    }
    # Only send category_id when a V2 leaf is available; otherwise omit it so
    # TikTok keeps the product's current category (Edit Product rejects the
    # legacy V1 leaf with error 12052217).
    v2_category = _v2_category_id(detail)
    if v2_category:
        payload["category_id"] = v2_category
    return payload


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------
def run_variant_set(base_sku: str, pack_sizes: list[int], dry_run: bool) -> int:
    print("=" * 70)
    print(f"ITBisa Shop Stock Bot — Variant set {'(DRY RUN)' if dry_run else ''}")
    print("=" * 70)
    print(f"SKU: {base_sku}")
    print(f"Pack sizes: {sorted(set(int(p) for p in pack_sizes))} (+ Bubble Wrap)")
    print()

    try:
        print("Walking TikTok Shop catalog...")
        catalog = tiktokshop_client.fetch_catalog()
    except shopee_auth.RefreshTokenExpiredError as e:
        msg = f"🔐 Otorisasi kadaluarsa. ({e})"
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg, mode="Variant")
        return 1
    except Exception as e:  # noqa: BLE001
        msg = f"Gagal membaca katalog TikTok Shop: {e}"
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg, mode="Variant")
        return 1

    variants = catalog.get(base_sku)
    if not variants:
        msg = f"SKU `{base_sku}` tidak ditemukan di TikTok Shop."
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg, mode="Variant")
        return 1

    product_id = variants[0]["product_id"]
    try:
        detail = tiktokshop_client.fetch_product_detail_raw(product_id)
        payload = build_edit_payload(detail, base_sku, pack_sizes)
        print(f"  [variant] V2 category resolved = {_v2_category_id(detail)} "
              f"(category_id {'sent' if 'category_id' in payload else 'OMITTED — keep current'})")
    except Exception as e:  # noqa: BLE001
        msg = f"Gagal menyusun payload Edit Product untuk `{base_sku}`: {e}"
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg, mode="Variant")
        return 1

    value_names = [
        (s.get("sales_attributes") or [{}])[0].get("value_name") for s in payload["skus"]
    ]
    print(f"Product: {product_id}")
    print(f"Target variation values: {value_names}")
    print(f"EDIT PRODUCT PAYLOAD: {json.dumps(payload, ensure_ascii=False)[:5500]}")

    if dry_run:
        telegram_sender.send_variant_set_summary({
            "base_sku": base_sku, "value_names": value_names,
            "status": "🔍 dry-run", "dry_run": True,
        })
        return 0

    try:
        tiktokshop_client.edit_product(product_id, payload)
    except Exception as e:  # noqa: BLE001
        msg = f"❌ Edit Product gagal untuk `{base_sku}`: {e}"
        print(msg)
        telegram_sender.send_variant_set_summary({
            "base_sku": base_sku, "value_names": value_names,
            "status": f"❌ gagal: {e}", "dry_run": False,
        })
        return 1

    telegram_sender.send_variant_set_summary({
        "base_sku": base_sku, "value_names": value_names,
        "status": "✅ berhasil", "dry_run": False,
    })
    return 0
