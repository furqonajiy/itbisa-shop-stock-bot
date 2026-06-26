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
import time

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


def _v1_leaf_name(detail: dict) -> str | None:
    """The local_name of the product's current (V1) leaf category."""
    chains = detail.get("category_chains") or []
    for c in chains:
        if c.get("is_leaf") and c.get("local_name"):
            return c["local_name"]
    return chains[-1].get("local_name") if chains else None


def _match_v2_category_by_name(categories: list[dict], leaf_name: str | None) -> str | None:
    """Find the V2 leaf category whose local_name equals the V1 leaf name.

    Edit Product requires a V2 category and this product has no recommendation,
    so we map by name — TikTok kept the same leaf names across the V1→V2
    migration (e.g. "Unit Catu Daya"). Returns the matched V2 leaf id, or None.
    """
    if not leaf_name:
        return None
    target = leaf_name.strip().casefold()
    for c in categories:
        if c.get("is_leaf") and (c.get("local_name") or "").strip().casefold() == target:
            return c.get("id")
    return None


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


def _variant_value_name(variant: dict) -> str:
    """The Packing value_name a catalog variant represents."""
    if variant.get("raw_sku") == BUBBLE_WRAP_SELLER_SKU:
        return BUBBLE_WRAP_VALUE_NAME
    return _value_name(variant.get("multiplier", 1))


def _resolve_global_value_ids(needed_names: set[str], catalog: dict) -> dict[str, str]:
    """Resolve the shop-global Packing value_id for each needed value name.

    "Packing" values are shared shop-wide, so a value already in use by ANY
    product carries an id we must reuse (sending it by name without the id makes
    Edit Product silently drop the variant). For each needed name we find a donor
    product that uses it and read the id from that product's detail. Names used
    by no current product can't be resolved here and are left for TikTok to
    create fresh. Best-effort: a donor detail failure just skips that name.
    """
    if not needed_names:
        return {}
    donor: dict[str, tuple[str, str]] = {}  # value_name -> (product_id, sku_id)
    for variants in catalog.values():
        for v in variants:
            name = _variant_value_name(v)
            if name in needed_names and name not in donor and v.get("sku_id") and v.get("product_id"):
                donor[name] = (v["product_id"], v["sku_id"])
        if len(donor) == len(needed_names):
            break

    by_product: dict[str, list[tuple[str, str]]] = {}
    for name, (pid, sid) in donor.items():
        by_product.setdefault(pid, []).append((name, sid))

    resolved: dict[str, str] = {}
    for pid, items in by_product.items():
        try:
            detail = tiktokshop_client.fetch_product_detail_raw(pid)
        except Exception as e:  # noqa: BLE001 - best-effort per donor
            print(f"  [variant] donor detail {pid} failed: {e}")
            continue
        sid_to_vid = {
            s.get("id"): ((s.get("sales_attributes") or [{}])[0]).get("value_id")
            for s in (detail.get("skus") or [])
        }
        for name, sid in items:
            vid = sid_to_vid.get(sid)
            if vid:
                resolved[name] = vid
    return resolved


def _edit_attributes(product_attributes) -> list[dict]:
    out = []
    for a in product_attributes or []:
        vals = [{"id": v["id"]} for v in (a.get("values") or []) if v.get("id")]
        if a.get("id") and vals:
            out.append({"id": a["id"], "values": vals})
    return out


def _build_sku(attr_id, attr_name, existing, value_name, seller_sku,
               price_idr, weight_kg, warehouse_id, qty, extra_value_ids=None):
    sales_attr = {"id": attr_id, "name": attr_name, "value_name": value_name}
    # Attach the value_id so TikTok references the EXISTING shop-global "Packing"
    # value instead of trying to re-create it. "Packing" values (1PCS, 20PCS, …)
    # are shared shop-wide: a value sent by name WITHOUT its id is silently
    # dropped on Edit Product (the variant never gets created, yet the call
    # returns success). Prefer the id from this product's own variant; else fall
    # back to the shop-global id resolved from a donor product. Only a value name
    # that exists nowhere is sent without an id (TikTok then creates it fresh).
    value_id = None
    if existing:
        value_id = ((existing.get("sales_attributes") or [{}])[0]).get("value_id")
    if not value_id and extra_value_ids:
        value_id = extra_value_ids.get(value_name)
    if value_id:
        sales_attr["value_id"] = value_id
    sku = {
        "seller_sku": seller_sku,
        "sales_attributes": [sales_attr],
        "price": {"amount": str(int(price_idr)), "currency": _CURRENCY},
        "inventory": [{"warehouse_id": warehouse_id, "quantity": int(qty)}],
        "package_weight": {"value": f"{weight_kg:g}", "unit": "KILOGRAM"},
    }
    return sku


def build_edit_payload(detail: dict, base_sku: str, pack_sizes: list[int],
                       extra_value_ids: dict | None = None) -> dict:
    """Construct the Edit Product payload that sets `Packing` to exactly the
    given pack sizes (+ Bubble Wrap). Pure given `detail` + `extra_value_ids`.

    New pack variants get stock 0 and a placeholder price/weight scaled from the
    1PCS variant (refined later by /harga_set and /weight_set); existing values
    keep their value_id, price, and weight. `extra_value_ids` ({value_name:
    value_id}) supplies the shop-global value_id for pack sizes not on this
    product but already in use elsewhere — without it TikTok drops them.
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
            price, weight, warehouse_id, qty=0, extra_value_ids=extra_value_ids,
        ))

    bw = existing.get(BUBBLE_WRAP_VALUE_NAME)
    target.append(_build_sku(
        attr_id, attr_name, bw, BUBBLE_WRAP_VALUE_NAME, BUBBLE_WRAP_SELLER_SKU,
        BUBBLE_WRAP_PRICE_IDR, _sku_weight_kg(bw) or BUBBLE_WRAP_WEIGHT_KG,
        warehouse_id, qty=0, extra_value_ids=extra_value_ids,
    ))

    payload = {
        "title": detail.get("title"),
        "description": detail.get("description"),
        # Declare V2 categories — Edit Product otherwise validates category_id
        # against the V1 taxonomy and rejects it (error 12052217), even when the
        # id is a valid V2 leaf.
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
    # Send the product's recommended V2 leaf when available; otherwise the
    # runner resolves one by name from the V2 category tree.
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
        # Resolve shop-global value_ids for the requested pack sizes that are NOT
        # already on this product, so existing "Packing" values attach instead of
        # being dropped (the silent-drop bug). Names on this product carry their
        # own id; names used by no product are created fresh by TikTok.
        target_names = (
            [_value_name(int(m)) for m in sorted(set(int(p) for p in pack_sizes))]
            + [BUBBLE_WRAP_VALUE_NAME]
        )
        on_product = {
            ((s.get("sales_attributes") or [{}])[0]).get("value_name")
            for s in (detail.get("skus") or [])
        }
        needed = {n for n in target_names if n not in on_product}
        extra_value_ids = _resolve_global_value_ids(needed, catalog)
        print(f"  [variant] on product: {sorted(n for n in target_names if n in on_product)}")
        print(f"  [variant] resolved global value_ids: {extra_value_ids}")
        brand_new = sorted(needed - set(extra_value_ids))
        if brand_new:
            print(f"  [variant] created fresh (no existing value_id): {brand_new}")
        payload = build_edit_payload(detail, base_sku, pack_sizes, extra_value_ids=extra_value_ids)
        # Edit Product requires a V2 category_id. If the detail offered no V2
        # recommendation, map the product's current V1 leaf name to the
        # same-named V2 leaf from the category tree (names carried over the
        # V1→V2 migration).
        if "category_id" not in payload:
            v1_name = _v1_leaf_name(detail)
            categories = tiktokshop_client.fetch_categories()
            matched = _match_v2_category_by_name(categories, v1_name)
            print(f"  [variant] V1 leaf name = {v1_name!r}; V2 tree size = {len(categories)}; "
                  f"matched V2 category_id = {matched}")
            if matched:
                payload["category_id"] = matched
        print(f"  [variant] category_id in payload = {payload.get('category_id')}")
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

    # Best-effort verify. The Edit Product call already returned success (code 0
    # — a hard API rejection would have raised above). Newly-created TikTok
    # variants can take a few MINUTES to become visible in the product detail, so
    # a brief re-read may not see them yet. Treat "not yet visible" as an
    # informational note (still ✅ + exit 0), NOT a failure — don't repeat the
    # earlier false-negative that reported failure on variants that did get made.
    missing = _verify_variants_created(product_id, value_names)
    if not missing:
        status = "✅ berhasil"
    else:
        status = "✅ berhasil — varian baru bisa perlu beberapa menit tampil di TikTok (cek `/stock_get`)"
        print(f"  [variant] not yet visible after edit (TikTok propagation): {missing}")

    telegram_sender.send_variant_set_summary({
        "base_sku": base_sku, "value_names": value_names,
        "status": status, "dry_run": False,
    })
    return 0


def _verify_variants_created(product_id: str, expected_value_names: list[str],
                             attempts: int = 3, delay_s: int = 5) -> list[str]:
    """Re-read the product (a few retries) and return value names not yet visible.

    Returns [] once all requested values appear. New variants propagate on
    TikTok's clock (often minutes), so a non-empty result means "not visible
    yet", not necessarily "failed" — the caller treats it as an info note. A
    read that always fails yields [] (don't punish a successful write for a
    transient read hiccup)."""
    last_present: set | None = None
    for i in range(attempts):
        try:
            after = tiktokshop_client.fetch_product_detail_raw(product_id)
        except Exception as e:  # noqa: BLE001 - verification is best-effort
            print(f"  [variant] verify read {i + 1}/{attempts} failed: {e}")
            after = None
        if after is not None:
            last_present = {
                ((s.get("sales_attributes") or [{}])[0]).get("value_name")
                for s in (after.get("skus") or [])
            }
            if all(n in last_present for n in expected_value_names):
                return []
        if i < attempts - 1:
            time.sleep(delay_s)
    if last_present is None:
        return []
    return [n for n in expected_value_names if n not in last_present]
