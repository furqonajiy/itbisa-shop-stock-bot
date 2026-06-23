"""
shopee_client.py
----------------
Shopee Open API integration for the stock bot.

Three public functions, all platform-specific:

  fetch_catalog() -> dict[str, list[dict]]
      Walks the entire shop's product catalog and returns a mapping
      of base_sku -> list of variant dicts. Each variant dict has the
      shape:
        {
          "multiplier":   int,    # 1 for non-pack-size, 25 for "25PCS-...", etc.
          "item_id":      int,    # Shopee item_id (always set)
          "model_id":     int|None,  # set only when has_model=True
          "raw_sku":      str,    # the SKU string as Shopee returned it
          "stock_units":  int,    # current available stock units (read-only consumers)
          "weight_grams": int,    # item-level weight in grams (Shopee gives kg)
        }
      The same base_sku may have variants split across MULTIPLE Shopee
      products. Example: ITBISA-IC-NE555P-DIP8 (1pc, item_id=A) and
      25PCS-ITBISA-IC-NE555P-DIP8 (25pc, item_id=B) are two different
      products on Shopee but share base "ITBISA-IC-NE555P-DIP8".

      stock_units and weight_grams are populated for EVERY variant.
      The /stock_set write path ignores them; /stock_get reads them.

  update_stock(item_id, model_id, new_stock) -> None
      Sets absolute stock for one (item_id, model_id) target. Raises
      RuntimeError on platform-level failure or per-item fail_error.

  update_price(item_id, model_id, price_idr) -> None
      Sets the absolute base/normal price for one (item_id, model_id) via
      /api/v2/product/update_price. Used by /harga_set.

  set_wholesale(item_id, wholesale_tiers) -> None
      Replaces the item's "Harga Grosir" wholesale tiers (each
      {min_count, max_count, unit_price}); an empty list clears them.
      Used by /harga_set. NOTE: the exact v2 wholesale endpoint/field
      names are best-effort and pending live verification (the official
      Shopee docs are login-gated).

  get_wholesale(item_id) -> list[(min_count, max_count, unit_price)]
      Reads the item's current "Harga Grosir" wholesale tiers. Best-effort:
      returns [] on any error or when none are set. Used by /stock_get.

  describe() -> str
      One-line "live | sandbox" identifier for log/Telegram headers.

This module talks ONLY to Shopee. The cross-platform 50:50 split and
the pack-size allocation math live in stock_allocator.py.
"""

from __future__ import annotations

import hashlib
import hmac
import time

import requests

from src import config, shopee_auth
from src.stock_allocator import parse_sku

# Shopee paginates get_item_list at 100 items max per page.
_LIST_PAGE_SIZE = 100

# get_item_base_info accepts up to 50 item_ids per call.
_BASE_INFO_BATCH_SIZE = 50


# ============================================================
# Public surface
# ============================================================

def describe() -> str:
    if "test" in config.SHOPEE_API_BASE_URL:
        return "Shopee SANDBOX"
    return "Shopee LIVE"


def fetch_catalog() -> dict[str, list[dict]]:
    """
    Returns base_sku -> [variant_dict, ...].

    Variants are sorted ascending by multiplier so the allocator can
    deposit any remainder onto variants[0] (the smallest pack).

    Each variant carries stock_units and weight_grams alongside the
    structural fields. Both are populated on every variant; consumers
    that don't care (e.g., /stock_set) simply ignore them.
    """
    print(f"  [shopee] Walking catalog ({describe()})...")

    item_ids = _fetch_all_item_ids()
    print(f"  [shopee] Found {len(item_ids)} active products")

    base_to_variants: dict[str, list[dict]] = {}
    if not item_ids:
        return base_to_variants

    # Walk in batches of 50 (Shopee's max for get_item_base_info).
    for batch_start in range(0, len(item_ids), _BASE_INFO_BATCH_SIZE):
        batch = item_ids[batch_start:batch_start + _BASE_INFO_BATCH_SIZE]
        items = _fetch_item_base_info(batch)

        for item in items:
            item_id = item["item_id"]
            has_models = item.get("has_model", False)
            parent_sku = (item.get("item_sku") or "").strip()
            item_weight_grams = _kg_to_grams(item.get("weight"))

            if not has_models:
                # Whole item is one leaf SKU. Parse for pack-size.
                if not parent_sku:
                    continue
                base, mult = parse_sku(parent_sku)
                base_to_variants.setdefault(base, []).append({
                    "multiplier": mult,
                    "item_id": item_id,
                    "model_id": None,
                    "raw_sku": parent_sku,
                    "stock_units": _extract_stock_units(item.get("stock_info_v2")),
                    "weight_grams": item_weight_grams,
                })
            else:
                # Each model is its own leaf SKU. Parse each for pack-size.
                # In practice models are usually color/size, so multiplier=1
                # — but we parse anyway in case the operator publishes a
                # "25PCS-LED-RED" model under a "25PCS-LED" parent.
                # get_model_list does NOT expose per-model weight, so all
                # models inherit the item-level weight.
                for model in _fetch_model_data(item_id):
                    if not model["model_sku"]:
                        continue
                    base, mult = parse_sku(model["model_sku"])
                    base_to_variants.setdefault(base, []).append({
                        "multiplier": mult,
                        "item_id": item_id,
                        "model_id": model["model_id"],
                        "raw_sku": model["model_sku"],
                        "stock_units": model["stock_units"],
                        "weight_grams": item_weight_grams,
                    })

        time.sleep(config.DELAY_BETWEEN_CALLS_SECONDS)

    # Sort variants ascending by multiplier per base.
    for variants in base_to_variants.values():
        variants.sort(key=lambda v: v["multiplier"])

    return base_to_variants


def update_stock(item_id: int, model_id: int | None, new_stock: int) -> None:
    """
    POST /api/v2/product/update_stock for one item or model.

    Raises RuntimeError on platform-level error or per-item fail_error.
    """
    path = "/api/v2/product/update_stock"

    stock_info: dict = {"seller_stock": [{"stock": new_stock}]}
    if model_id is not None:
        stock_info["model_id"] = model_id

    body = {
        "item_id": item_id,
        "stock_list": [stock_info],
    }

    data = _signed_post(path, body)

    if data.get("error"):
        raise RuntimeError(f"{data.get('error')}: {data.get('message')}")

    # Per-item failures are nested in result_list[*].fail_error.
    result_list = (data.get("response") or {}).get("result_list", [])
    for r in result_list:
        if r.get("fail_error"):
            raise RuntimeError(
                f"{r.get('fail_error')}: {r.get('fail_message')}"
            )


def update_price(item_id: int, model_id: int | None, price_idr: int) -> None:
    """
    POST /api/v2/product/update_price — set the absolute base price for one
    (item_id, model_id). For an item without variations, model_id is None and
    the price applies to the item.

    Raises RuntimeError on platform-level error or per-model failure.
    """
    path = "/api/v2/product/update_price"

    entry: dict = {"original_price": price_idr}
    if model_id is not None:
        entry["model_id"] = model_id

    body = {"item_id": item_id, "price_list": [entry]}
    data = _signed_post(path, body)

    if data.get("error"):
        raise RuntimeError(f"{data.get('error')}: {data.get('message')}")
    failures = (data.get("response") or {}).get("failure_list") or []
    if failures:
        raise RuntimeError(f"price update failures: {failures}")


def set_wholesale(item_id: int, wholesale_tiers: list[tuple[int, int, int]]) -> None:
    """
    Set the item's "Harga Grosir" wholesale tiers. `wholesale_tiers` is a list
    of (min_count, max_count, unit_price). An empty list clears any existing
    wholesale (so the base price applies to all quantities).

    Tries update_wholesale first (replaces the tier list); falls back to
    add_wholesale when the item has none yet.

    NOTE: best-effort against the v2 product wholesale endpoints
    (`update_wholesale` / `add_wholesale` / `delete_wholesale`, field
    `wholesale_list` of `{min_count, max_count, unit_price}`). The official
    docs are login-gated, so the exact endpoint/field names are pending live
    verification — use `--dry-run` first.
    """
    if not wholesale_tiers:
        # No bulk tiers (single-tier price): clear any existing wholesale.
        data = _signed_post("/api/v2/product/delete_wholesale", {"item_id": item_id})
        if data.get("error") and "not" not in str(data.get("message", "")).lower():
            raise RuntimeError(f"{data.get('error')}: {data.get('message')}")
        return

    wholesale_list = [
        {"min_count": mn, "max_count": mx, "unit_price": price}
        for mn, mx, price in wholesale_tiers
    ]
    body = {"item_id": item_id, "wholesale_list": wholesale_list}

    data = _signed_post("/api/v2/product/update_wholesale", body)
    if data.get("error"):
        # Likely no wholesale exists yet → create it.
        data_add = _signed_post("/api/v2/product/add_wholesale", body)
        if data_add.get("error"):
            raise RuntimeError(
                f"update_wholesale {data.get('error')}: {data.get('message')}; "
                f"add_wholesale {data_add.get('error')}: {data_add.get('message')}"
            )


def get_wholesale(item_id: int) -> list[tuple[int, int, int]]:
    """
    Read the item's "Harga Grosir" wholesale tiers.

    Shopee v2 has NO standalone get_wholesale endpoint (it 404s); wholesale is
    a field on the item, so we read it from get_item_base_info. Returns a list
    of (min_count, max_count, unit_price), ascending; [] on any error / none.

    Best-effort + verbose diagnostics: logs the item's field keys and any
    wholesale-like field so the real shape can be confirmed from the Actions
    log. Parses `wholesales`/`wholesale_list` with `min_count`/`min`,
    `max_count`/`max`, `unit_price`/`price` spellings.
    """
    try:
        data = _signed_get(
            "/api/v2/product/get_item_base_info",
            {"item_id_list": str(item_id)},
        )
    except Exception as e:  # noqa: BLE001 - read-only, never break /stock_get
        print(f"  [shopee] wholesale probe({item_id}): get_item_base_info failed: {e}")
        return []

    if data.get("error"):
        print(
            f"  [shopee] wholesale probe({item_id}): "
            f"error={data.get('error')!r} message={data.get('message')!r}"
        )
        return []

    items = (data.get("response") or {}).get("item_list") or []
    if not items:
        print(f"  [shopee] wholesale probe({item_id}): empty item_list")
        return []

    item = items[0]
    keys = sorted(item.keys())
    print(f"  [shopee] wholesale probe({item_id}): item keys={keys}")
    print(f"  [shopee] wholesale probe({item_id}): full item={str(item)[:2500]}")
    for k in keys:
        if any(s in k.lower() for s in ("whole", "grosir", "tier", "bulk", "price")):
            print(f"  [shopee] wholesale probe({item_id}): {k}={str(item[k])[:400]}")

    raw = item.get("wholesales")
    if raw is None:
        raw = item.get("wholesale_list") or []

    tiers: list[tuple[int, int, int]] = []
    for w in (raw or []):
        if not isinstance(w, dict):
            continue
        mn = w.get("min_count", w.get("min"))
        mx = w.get("max_count", w.get("max"))
        price = w.get("unit_price", w.get("price"))
        try:
            tiers.append((int(mn), int(mx), int(price)))
        except (TypeError, ValueError):
            continue
    tiers.sort(key=lambda t: t[0])
    return tiers


# ============================================================
# Catalog walk helpers
# ============================================================

def _fetch_all_item_ids() -> list[int]:
    """Paginated walk of get_item_list, status NORMAL only."""
    path = "/api/v2/product/get_item_list"
    out: list[int] = []
    offset = 0

    while True:
        params = {
            "offset": offset,
            "page_size": _LIST_PAGE_SIZE,
            "item_status": "NORMAL",
        }
        data = _signed_get(path, params)

        items = (data.get("response") or {}).get("item", [])
        out.extend(item["item_id"] for item in items)

        has_next = (data.get("response") or {}).get("has_next_page", False)
        if not has_next:
            break
        offset += _LIST_PAGE_SIZE

    return out


def _fetch_item_base_info(item_ids: list[int]) -> list[dict]:
    """get_item_base_info for up to 50 ids; returns raw item dicts."""
    path = "/api/v2/product/get_item_base_info"
    params = {"item_id_list": ",".join(str(i) for i in item_ids)}
    data = _signed_get(path, params)
    return (data.get("response") or {}).get("item_list", [])


def _fetch_model_data(item_id: int) -> list[dict]:
    """Returns model dicts: {model_id, model_sku, stock_units}.

    Shopee's get_model_list does NOT expose a per-model weight field;
    weight is item-level only. The caller propagates item weight down
    to every model.
    """
    path = "/api/v2/product/get_model_list"
    params = {"item_id": item_id}
    data = _signed_get(path, params)
    models = (data.get("response") or {}).get("model", [])
    return [
        {
            "model_id": m["model_id"],
            "model_sku": (m.get("model_sku") or "").strip(),
            "stock_units": _extract_stock_units(m.get("stock_info_v2")),
        }
        for m in models
    ]


# ============================================================
# Stock + weight extraction helpers
# ============================================================

def _extract_stock_units(stock_info_v2) -> int:
    """Pulls total available stock from a Shopee stock_info_v2 dict.

    Falls back to summing seller_stock entries if summary_info is absent.
    Returns 0 on any malformed/missing data — never raises.
    """
    if not stock_info_v2:
        return 0
    summary = stock_info_v2.get("summary_info") or {}
    val = summary.get("total_available_stock")
    if val is not None:
        try:
            return int(val)
        except (TypeError, ValueError):
            return 0
    seller = stock_info_v2.get("seller_stock") or []
    total = 0
    for s in seller:
        try:
            total += int(s.get("stock") or 0)
        except (TypeError, ValueError):
            pass
    return total


def _kg_to_grams(kg_value) -> int:
    """Shopee returns weight in kg as a float or numeric string. Returns grams."""
    if kg_value in (None, "", 0):
        return 0
    try:
        return round(float(kg_value) * 1000)
    except (TypeError, ValueError):
        return 0


# ============================================================
# Signing — shop-level: base = partner_id + path + ts + access_token + shop_id
# ============================================================

def _signed_get(path: str, params: dict) -> dict:
    url = _build_signed_url(path)
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def _signed_post(path: str, body: dict) -> dict:
    url = _build_signed_url(path)
    response = requests.post(url, json=body, timeout=30)
    response.raise_for_status()
    return response.json()


def _build_signed_url(path: str) -> str:
    access_token = shopee_auth.get_valid_access_token()
    timestamp = int(time.time())

    base = (
        f"{config.SHOPEE_PARTNER_ID}{path}{timestamp}"
        f"{access_token}{config.SHOPEE_SHOP_ID}"
    )
    sign = hmac.new(
        config.SHOPEE_PARTNER_KEY.encode("utf-8"),
        base.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return (
        f"{config.SHOPEE_API_BASE_URL}{path}"
        f"?partner_id={config.SHOPEE_PARTNER_ID}"
        f"&timestamp={timestamp}"
        f"&access_token={access_token}"
        f"&shop_id={config.SHOPEE_SHOP_ID}"
        f"&sign={sign}"
    )
