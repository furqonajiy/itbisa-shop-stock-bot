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
        }
      The same base_sku may have variants split across MULTIPLE Shopee
      products. Example: ITBISA-IC-NE555P-DIP8 (1pc, item_id=A) and
      25PCS-ITBISA-IC-NE555P-DIP8 (25pc, item_id=B) are two different
      products on Shopee but share base "ITBISA-IC-NE555P-DIP8".

  update_stock(item_id, model_id, new_stock) -> None
      Sets absolute stock for one (item_id, model_id) target. Raises
      RuntimeError on platform-level failure or per-item fail_error.

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

            if not has_models:
                # Whole item is one leaf SKU. Parse for pack-size.
                if not parent_sku:
                    continue
                base, mult = parse_sku(parent_sku)
                base_to_variants.setdefault(base, []).append({
                    "multiplier": mult,
                    "item_id":    item_id,
                    "model_id":   None,
                    "raw_sku":    parent_sku,
                })
            else:
                # Each model is its own leaf SKU. Parse each for pack-size.
                # In practice models are usually color/size, so multiplier=1
                # — but we parse anyway in case the operator publishes a
                # "25PCS-LED-RED" model under a "25PCS-LED" parent.
                for model_id, model_sku in _fetch_model_skus(item_id):
                    if not model_sku:
                        continue
                    base, mult = parse_sku(model_sku)
                    base_to_variants.setdefault(base, []).append({
                        "multiplier": mult,
                        "item_id":    item_id,
                        "model_id":   model_id,
                        "raw_sku":    model_sku,
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
        "item_id":    item_id,
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
            "offset":      offset,
            "page_size":   _LIST_PAGE_SIZE,
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


def _fetch_model_skus(item_id: int) -> list[tuple[int, str]]:
    """Returns [(model_id, model_sku), ...] for an item with variants."""
    path = "/api/v2/product/get_model_list"
    params = {"item_id": item_id}
    data = _signed_get(path, params)
    models = (data.get("response") or {}).get("model", [])
    return [(m["model_id"], (m.get("model_sku") or "").strip()) for m in models]


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