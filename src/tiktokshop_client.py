"""
tiktokshop_client.py
--------------------
TikTok Shop Open API integration for the stock bot.

Public functions:

  fetch_catalog() -> dict[str, list[dict]]
      Walks /product/202502/products/search across all pages and
      returns base_sku -> list of variant dicts. Variant dict shape:
        {
          "multiplier":   int,
          "sku_id":       str,
          "product_id":   str,
          "warehouse_id": str,
          "raw_sku":      str,
          "stock_units":  int,
          "weight_grams": int,    # 0 when search omits SKU/product weight
        }
      All variants of a base SKU live under ONE product on TikTok Shop.
      We sort variants ascending by multiplier per base.

      stock_units is populated for every variant. weight_grams may come
      back as 0 from this endpoint because /product/202502/products/search
      omits weight fields in practice — call fetch_product_detail() to
      enrich for /stock_get.

  fetch_product_detail(product_id) -> dict[str, int]
      GET /product/202309/products/{product_id}. Returns
      {sku_id: weight_grams} for every SKU under the product, with the
      product-level package_weight used as a fallback when a SKU-level
      sku_weight value isn't published. Used by /stock_get to display
      per-SKU "berat". Emits verbose diagnostic prints — when weight comes
      back empty, the Actions log shows the raw response keys so we can
      see whether seller has weight configured or whether the field
      lives somewhere unexpected.

  update_stock_batch(product_id, sku_updates) -> None
      Absolute stock set. Path says inventory/update but it's a set, not
      a delta. All SKUs in one call must belong to product_id.

  update_price_batch(product_id, sku_prices) -> None
      Absolute price set via the 202309 Update Price API. sku_prices is a
      list of (sku_id, price_idr); each price is sent as
      {amount: "<int>", currency: "IDR"}. All SKUs must belong to product_id.

  describe() -> str
      Identifier for log/Telegram headers.
"""

from __future__ import annotations

import hashlib
import hmac
import json as _json
import time

import requests

from src import config, tiktokshop_auth
from src.stock_allocator import parse_sku

_SEARCH_API_VERSION = "202502"
_INVENTORY_API_VERSION = "202309"
_DETAIL_API_VERSION = "202309"
_PRICE_API_VERSION = "202309"
_PRODUCT_API_VERSION = "202309"
_PRICE_CURRENCY = "IDR"

_SEARCH_PAGE_SIZE = 100

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
            "version": _SEARCH_API_VERSION,
        }
        if page_token:
            extra_query["page_token"] = page_token

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
            product_weight_grams = _normalize_weight_to_grams(product.get("package_weight"))

            for sku in product.get("skus") or []:
                seller_sku = (sku.get("seller_sku") or "").strip()
                if not seller_sku:
                    continue

                sku_id = sku.get("id")
                inventories = sku.get("inventory") or []
                if not sku_id or not inventories:
                    continue

                warehouse_id = inventories[0].get("warehouse_id")
                if not warehouse_id:
                    continue

                stock_units = 0
                for inv in inventories:
                    try:
                        stock_units += int(inv.get("quantity") or 0)
                    except (TypeError, ValueError):
                        pass

                sku_weight_grams = _normalize_weight_to_grams(
                    sku.get("sku_weight") or sku.get("package_weight")
                )
                weight_grams = sku_weight_grams or product_weight_grams

                base, mult = parse_sku(seller_sku)
                base_to_variants.setdefault(base, []).append({
                    "multiplier": mult,
                    "sku_id": sku_id,
                    "product_id": product_id,
                    "warehouse_id": warehouse_id,
                    "raw_sku": seller_sku,
                    "stock_units": stock_units,
                    "weight_grams": weight_grams,
                })

        page_token = payload.get("next_page_token") or ""
        print(f"  [tiktokshop] Page {page_num}: {len(products)} products (running total: {total_seen})")
        if not page_token:
            break
        time.sleep(0.3)

    for variants in base_to_variants.values():
        variants.sort(key=lambda v: v["multiplier"])

    return base_to_variants


def fetch_product_detail(product_id: str) -> dict[str, int]:
    """
    GET /product/202309/products/{product_id}.

    Returns {sku_id: weight_grams} for every SKU under the product. Falls
    back to the product-level package_weight when a SKU-level value isn't
    published.

    Verbose logging surfaces the raw response shape so we can diagnose
    why weights come back empty:
      - HTTP status + payload code/message
      - Top-level data keys
      - product-level package_weight raw value
      - per-SKU sku_weight/package_weight raw value
    """
    path = f"/product/{_DETAIL_API_VERSION}/products/{product_id}"
    response = _call_signed(
        "GET",
        path,
        extra_query={"version": _DETAIL_API_VERSION},
    )

    print(f"  [tiktokshop] fetch_product_detail({product_id}): HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as e:
        print(f"  [tiktokshop] fetch_product_detail({product_id}): not JSON ({e})")
        print(f"  [tiktokshop] raw body (first 400 chars): {response.text[:400]}")
        return {}

    code = payload.get("code")
    message = payload.get("message")
    print(f"  [tiktokshop] fetch_product_detail({product_id}): code={code} message={message!r}")

    if response.status_code >= 400 or code != 0:
        print(f"  [tiktokshop] fetch_product_detail({product_id}): error body: {response.text[:400]}")
        return {}

    data = payload.get("data") or {}
    print(
        f"  [tiktokshop] fetch_product_detail({product_id}): "
        f"top-level data keys = {sorted(data.keys())}"
    )

    raw_product_weight = data.get("package_weight")
    print(
        f"  [tiktokshop] fetch_product_detail({product_id}): "
        f"product.package_weight = {raw_product_weight!r}"
    )
    product_weight_grams = _normalize_weight_to_grams(raw_product_weight)

    skus = data.get("skus") or []
    print(f"  [tiktokshop] fetch_product_detail({product_id}): {len(skus)} sku(s) under this product")

    result: dict[str, int] = {}
    for sku in skus:
        sku_id = sku.get("id")
        if not sku_id:
            continue
        raw_sku_weight = sku.get("sku_weight") or sku.get("package_weight")
        sku_weight_grams = _normalize_weight_to_grams(raw_sku_weight)
        final = sku_weight_grams or product_weight_grams
        print(
            f"  [tiktokshop]   sku {sku_id} ({sku.get('seller_sku')!r}): "
            f"sku_weight={sku.get('sku_weight')!r}, "
            f"package_weight={sku.get('package_weight')!r} -> {sku_weight_grams}g, "
            f"final={final}g"
        )
        result[sku_id] = final

    return result


def update_stock_batch(
        product_id: str,
        sku_updates: list[tuple[str, str, int]],
) -> None:
    """
    POST /product/202309/products/{product_id}/inventory/update.
    Absolute stock set; sku_updates = list of (sku_id, warehouse_id, qty).
    """
    path = f"/product/{_INVENTORY_API_VERSION}/products/{product_id}/inventory/update"
    body = {
        "skus": [
            {
                "id": sku_id,
                "inventory": [{"warehouse_id": warehouse_id, "quantity": qty}],
            }
            for sku_id, warehouse_id, qty in sku_updates
        ],
    }

    response = _call_signed(
        "POST",
        path,
        extra_query={"version": _INVENTORY_API_VERSION},
        body=body,
    )
    _check_ok(response, context=f"stock update product={product_id}")

    data = response.json().get("data") or {}
    failures = data.get("errors") or data.get("failed_skus") or []
    if failures:
        raise RuntimeError(f"per-sku failures: {failures}")


def update_price_batch(
        product_id: str,
        sku_prices: list[tuple[str, int]],
) -> None:
    """
    POST /product/202309/products/{product_id}/prices/update.
    Absolute price set; sku_prices = list of (sku_id, price_idr).

    Each price is sent as {amount: "<int>", currency: "IDR"} per the 202309
    schema (IDR has no minor units, so the amount is the whole-rupiah value as
    a string). All SKUs in one call must belong to product_id.
    """
    path = f"/product/{_PRICE_API_VERSION}/products/{product_id}/prices/update"
    body = {
        "skus": [
            {
                "id": sku_id,
                "price": {
                    "amount": str(int(price_idr)),
                    "currency": _PRICE_CURRENCY,
                },
            }
            for sku_id, price_idr in sku_prices
        ],
    }

    response = _call_signed(
        "POST",
        path,
        extra_query={"version": _PRICE_API_VERSION},
        body=body,
    )
    _check_ok(response, context=f"price update product={product_id}")

    data = response.json().get("data") or {}
    failures = data.get("errors") or data.get("failed_skus") or []
    if failures:
        raise RuntimeError(f"per-sku price failures: {failures}")


def fetch_product_detail_raw(product_id: str) -> dict:
    """GET /product/202309/products/{product_id} → the full `data` dict.

    Used by /variant_set to read a product's current structure before building
    an Edit Product payload. Raises RuntimeError on API error.
    """
    response = _call_signed(
        "GET",
        f"/product/{_PRODUCT_API_VERSION}/products/{product_id}",
        extra_query={"version": _PRODUCT_API_VERSION},
    )
    _check_ok(response, context=f"product detail {product_id}")
    return response.json().get("data") or {}


def edit_product(product_id: str, payload: dict) -> dict:
    """PUT /product/202309/products/{product_id} — Edit Product (full replace).

    Used by /variant_set to rebuild the variation set. Edit Product is a PUT
    (POST on this path is Create Product and returns HTTP 405 "Invalid method").
    The request schema is best-effort and pending live verification (the
    official docs are login-gated) — always exercise via the runner's dry-run
    first. Raises RuntimeError on API error.
    """
    response = _call_signed(
        "PUT",
        f"/product/{_PRODUCT_API_VERSION}/products/{product_id}",
        extra_query={"version": _PRODUCT_API_VERSION},
        body=payload,
    )
    _check_ok(response, context=f"edit product {product_id}")
    return response.json().get("data") or {}


# ============================================================
# Weight normalisation
# ============================================================
def _normalize_weight_to_grams(pkg_weight) -> int:
    """Converts a TikTok Shop {value, unit} weight dict to grams.

    Returns 0 on missing/invalid data. Logs unknown unit values so we
    can spot schema drift.
    """
    if not pkg_weight:
        return 0
    if not isinstance(pkg_weight, dict):
        print(f"  [tiktokshop] _normalize_weight_to_grams: unexpected type {type(pkg_weight).__name__} -> {pkg_weight!r}")
        return 0
    try:
        value = float(pkg_weight.get("value") or 0)
    except (TypeError, ValueError):
        return 0
    unit = (pkg_weight.get("unit") or "").upper()
    if unit == "KILOGRAM":
        return round(value * 1000)
    if unit == "POUND":
        return round(value * 453.59237)
    if unit == "GRAM":
        return round(value)
    if not unit:
        # Empty unit — TikTok Shop defaults to KILOGRAM in docs.
        return round(value * 1000)
    print(f"  [tiktokshop] _normalize_weight_to_grams: unknown unit {unit!r} on {pkg_weight!r}; assuming kg")
    return round(value * 1000)


# ============================================================
# Signed call helpers
# ============================================================
def _call_signed(
        method: str,
        path: str,
        *,
        extra_query: dict[str, str] | None = None,
        body: dict | list | None = None,
        include_cipher: bool = True,
) -> requests.Response:
    access_token = tiktokshop_auth.get_valid_access_token()
    timestamp = str(int(time.time()))

    query: dict[str, str] = {
        "app_key": config.TIKTOKSHOP_APP_KEY,
        "shop_id": str(config.TIKTOKSHOP_SHOP_ID),
        "timestamp": timestamp,
    }
    if extra_query:
        query.update(extra_query)

    if include_cipher:
        cipher = _get_shop_cipher(access_token)
        if cipher:
            query["shop_cipher"] = cipher

    raw_body = ""
    if body is not None:
        raw_body = _json.dumps(body, separators=(",", ":"), ensure_ascii=False)

    sign = _compute_sign(path, query, raw_body)
    query["sign"] = sign
    query["access_token"] = access_token

    url = f"{config.TIKTOKSHOP_OPEN_API_BASE_URL}{path}"
    headers = {
        "x-tts-access-token": access_token,
        "Content-Type": "application/json",
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
    filtered = {
        k: v for k, v in query.items()
        if k not in ("sign", "access_token") and v not in (None, "")
    }
    sorted_params = "".join(f"{k}{v}" for k, v in sorted(filtered.items()))
    canonical = path + sorted_params + (raw_body or "")
    wrapped = config.TIKTOKSHOP_APP_SECRET + canonical + config.TIKTOKSHOP_APP_SECRET
    return hmac.new(
        config.TIKTOKSHOP_APP_SECRET.encode("utf-8"),
        wrapped.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _check_ok(response: requests.Response, *, context: str) -> None:
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
    global _cached_shop_cipher
    if _cached_shop_cipher is not None:
        return _cached_shop_cipher

    response = _call_signed(
        "GET",
        "/authorization/202309/shops",
        extra_query={"version": "202309"},
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
