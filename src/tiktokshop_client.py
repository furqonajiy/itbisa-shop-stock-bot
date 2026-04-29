"""
tiktokshop_client.py
--------------------
TikTok Shop Open API integration for the inventory bot.

Public functions:

  fetch_catalog() -> dict[str, list[dict]]
      Walks /product/202502/products/search across all pages and
      returns base_sku -> list of variant dicts. Variant dict shape:
        {
          "multiplier":   int,
          "sku_id":       str,    # TikTok SKU id within a product
          "product_id":   str,    # parent product_id
          "warehouse_id": str,    # which warehouse to update
          "raw_sku":      str,    # the seller_sku TikTok returned
        }
      All variants of a base SKU live under ONE product on TikTok, so
      the structure is simpler than Shopee's. We sort variants ascending
      by multiplier per base.

  update_stock_batch(product_id, sku_updates) -> None
      Single PUT carrying multiple SKU updates that all belong to the
      same product. sku_updates = list of (sku_id, warehouse_id, qty).

  describe() -> str
      Identifier for log/Telegram headers.

Notes on the SKU structure:
  TikTok returns one product with many `skus`, each with `seller_sku`,
  `id`, and `inventory[]`. We parse seller_sku for the "<n>PCS-" prefix.
  All siblings of one base end up in the same product, so the batch
  PUT covers the whole rebalance in one call.
"""

from __future__ import annotations

import hashlib
import hmac
import json as _json
import time

import requests

from src import config, tiktokshop_auth
from src.inventory_allocator import parse_sku


# Versions per endpoint. The signed `version` query param must match
# the path version, so we set it explicitly per call. 202502 is the
# newer, denser product/search; 202309 is still current for inventory.
_SEARCH_API_VERSION    = "202502"
_INVENTORY_API_VERSION = "202309"

# TikTok docs cap product/search at page_size=100.
_SEARCH_PAGE_SIZE = 100


# Cached shop_cipher for the duration of one process run. Fetched
# lazily on first signed call. Same pattern as the order bot.
_cached_shop_cipher: str | None = None


# ============================================================
# Public surface
# ============================================================

def describe() -> str:
    return "TikTok Shop"


def fetch_catalog() -> dict[str, list[dict]]:
    """Returns base_sku -> [variant_dict, ...] sorted ascending by multiplier."""
    print("  [tiktokshop] Walking catalog...")

    base_to_variants: dict[str, list[dict]] = {}
    page_token = ""
    page_num = 0
    total_seen = 0

    while True:
        page_num += 1
        extra_query: dict[str, str] = {
            "page_size": str(_SEARCH_PAGE_SIZE),
            "version":   _SEARCH_API_VERSION,
        }
        if page_token:
            extra_query["page_token"] = page_token

        # Body filter: only ACTIVATE products. Catalog walk is intentionally
        # broad — the warehouse needs to update any SKU.
        response = _call_signed(
            "POST",
            "/product/202502/products/search",
            extra_query=extra_query,
            body={"status": "ACTIVATE"},
        )
        _check_ok(response, context="product search")

        payload = response.json()["data"]
        products = payload.get("products") or []
        total_seen += len(products)

        if page_num == 1:
            total = payload.get("total_count", "?")
            print(f"  [tiktokshop] Catalog reports {total} total products")

        for product in products:
            product_id = product["id"]
            for sku in product.get("skus") or []:
                seller_sku = (sku.get("seller_sku") or "").strip()
                if not seller_sku:
                    continue

                sku_id = sku.get("id")
                inventories = sku.get("inventory") or []
                if not sku_id or not inventories:
                    # No existing inventory record means we don't know
                    # which warehouse_id to target — skip.
                    continue

                warehouse_id = inventories[0].get("warehouse_id")
                if not warehouse_id:
                    continue

                base, mult = parse_sku(seller_sku)
                base_to_variants.setdefault(base, []).append({
                    "multiplier":   mult,
                    "sku_id":       sku_id,
                    "product_id":   product_id,
                    "warehouse_id": warehouse_id,
                    "raw_sku":      seller_sku,
                })

        page_token = payload.get("next_page_token") or ""
        print(f"  [tiktokshop] Page {page_num}: {len(products)} products (running total: {total_seen})")
        if not page_token:
            break
        time.sleep(0.3)

    for variants in base_to_variants.values():
        variants.sort(key=lambda v: v["multiplier"])

    return base_to_variants


def update_stock_batch(
    product_id: str,
    sku_updates: list[tuple[str, str, int]],
) -> None:
    """
    POST /product/202309/products/{product_id}/inventory/update.

    Args:
      product_id:  TikTok product id (string).
      sku_updates: list of (sku_id, warehouse_id, quantity) tuples — all
                   SKUs MUST belong to product_id (TikTok's endpoint
                   rejects mixed-product batches).

    Raises RuntimeError on platform-level error or per-SKU failure.
    """
    path = f"/product/{_INVENTORY_API_VERSION}/products/{product_id}/inventory/update"
    body = {
        "skus": [
            {
                "id":        sku_id,
                "inventory": [{"warehouse_id": warehouse_id, "quantity": qty}],
            }
            for sku_id, warehouse_id, qty in sku_updates
        ],
    }

    response = _call_signed("POST", path, body=body)
    _check_ok(response, context=f"inventory update product={product_id}")

    data = response.json().get("data") or {}
    failures = data.get("errors") or data.get("failed_skus") or []
    if failures:
        raise RuntimeError(f"per-sku failures: {failures}")


# ============================================================
# Signed call helpers (also used by scripts/* — note _underscore name
# is intentional, see system prompt's TikTok inventory caveat)
# ============================================================

def _call_signed(
    method: str,
    path: str,
    *,
    extra_query: dict[str, str] | None = None,
    body: dict | list | None = None,
    include_cipher: bool = True,
) -> requests.Response:
    """
    Signs and dispatches an Open API call.

    Signing:
      1. Exclude 'sign' and 'access_token' from query params; drop empty values.
      2. Sort remaining params by key, concatenate as key+value (no separator).
      3. canonical = path + sorted_param_string + raw_body_string
      4. wrapped   = app_secret + canonical + app_secret
      5. sign      = HMAC-SHA256(app_secret, wrapped).hexdigest()
    """
    access_token = tiktokshop_auth.get_valid_access_token()
    timestamp = str(int(time.time()))

    query: dict[str, str] = {
        "app_key":   config.TIKTOKSHOP_APP_KEY,
        "shop_id":   str(config.TIKTOKSHOP_SHOP_ID),
        "timestamp": timestamp,
    }
    if extra_query:
        query.update(extra_query)

    if include_cipher:
        cipher = _get_shop_cipher(access_token)
        if cipher:
            query["shop_cipher"] = cipher

    # Body for signing: compact JSON if dict/list, else empty string.
    raw_body = ""
    if body is not None:
        raw_body = _json.dumps(body, separators=(",", ":"), ensure_ascii=False)

    sign = _compute_sign(path, query, raw_body)
    query["sign"]         = sign
    query["access_token"] = access_token  # transport-only, not signed

    url = f"{config.TIKTOKSHOP_OPEN_API_BASE_URL}{path}"
    headers = {
        "x-tts-access-token": access_token,
        "Content-Type":       "application/json",
    }

    if method.upper() == "GET":
        return requests.get(url, params=query, headers=headers, timeout=30)
    elif method.upper() == "POST":
        return requests.post(
            url,
            params=query,
            data=raw_body.encode("utf-8") if raw_body else None,
            headers=headers,
            timeout=30,
        )
    elif method.upper() == "PUT":
        return requests.put(
            url,
            params=query,
            data=raw_body.encode("utf-8") if raw_body else None,
            headers=headers,
            timeout=30,
        )
    else:
        raise ValueError(f"Unsupported method: {method}")


def _compute_sign(path: str, query: dict[str, str], raw_body: str) -> str:
    """See _call_signed docstring for the algorithm."""
    filtered = {
        k: v for k, v in query.items()
        if k not in ("sign", "access_token") and v not in (None, "")
    }
    sorted_params = "".join(f"{k}{v}" for k, v in sorted(filtered.items()))
    canonical = path + sorted_params + (raw_body or "")
    wrapped   = config.TIKTOKSHOP_APP_SECRET + canonical + config.TIKTOKSHOP_APP_SECRET
    return hmac.new(
        config.TIKTOKSHOP_APP_SECRET.encode("utf-8"),
        wrapped.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _check_ok(response: requests.Response, *, context: str) -> None:
    """Raises RuntimeError if HTTP non-2xx or payload code != 0."""
    if response.status_code >= 400:
        raise RuntimeError(
            f"TikTok Shop HTTP {response.status_code} on {context}: {response.text[:500]}"
        )
    payload = response.json()
    if payload.get("code") != 0:
        raise RuntimeError(
            f"TikTok Shop {context} failed: code={payload.get('code')} "
            f"message={payload.get('message')}"
        )


def _get_shop_cipher(access_token: str) -> str:
    """Lazy-loaded, cached for the run. The cipher is required on most
    Open API endpoints; the only exception is /authorization/202309/shops
    itself, which we call here with include_cipher=False."""
    global _cached_shop_cipher
    if _cached_shop_cipher is not None:
        return _cached_shop_cipher

    response = _call_signed(
        "GET",
        "/authorization/202309/shops",
        include_cipher=False,
    )
    _check_ok(response, context="get shop_cipher")

    shops = response.json()["data"].get("shops") or []
    target_shop_id = str(config.TIKTOKSHOP_SHOP_ID)
    for shop in shops:
        if str(shop.get("id")) == target_shop_id:
            _cached_shop_cipher = shop["cipher"]
            return _cached_shop_cipher

    raise RuntimeError(
        f"TikTok Shop ID {target_shop_id} not in /authorization/202309/shops response"
    )
