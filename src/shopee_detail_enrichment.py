"""Best-effort Shopee price enrichment for Telegram summaries."""

from __future__ import annotations

from typing import Any

from src import shopee_client

_BASE_INFO_BATCH_SIZE = 50


def enrich_shopee_prices(variants: list[dict]) -> None:
    """Attach price_idr to Shopee variants when Shopee detail APIs expose it.

    The stock catalog already carries item/model ids and weight. This helper is
    read-only and only enriches summary metadata; it does not affect allocation
    or stock writes.
    """
    if not variants:
        return

    item_ids = sorted({int(v["item_id"]) for v in variants if v.get("item_id")})
    item_price_by_id: dict[int, int | None] = {}
    model_price_by_key: dict[tuple[int, int], int | None] = {}

    for start in range(0, len(item_ids), _BASE_INFO_BATCH_SIZE):
        batch = item_ids[start:start + _BASE_INFO_BATCH_SIZE]
        items = _fetch_item_base_info(batch)
        for item in items:
            item_id = int(item["item_id"])
            item_price_by_id[item_id] = _extract_price_idr(item)
            if item.get("has_model"):
                for model in _fetch_model_data(item_id):
                    model_price_by_key[(item_id, int(model["model_id"]))] = _extract_price_idr(model)

    for variant in variants:
        if variant.get("price_idr") is not None:
            continue
        item_id = int(variant["item_id"])
        model_id = variant.get("model_id")
        price_idr = None
        if model_id is not None:
            price_idr = model_price_by_key.get((item_id, int(model_id)))
        if price_idr is None:
            price_idr = item_price_by_id.get(item_id)
        if price_idr is not None:
            variant["price_idr"] = price_idr


def _fetch_item_base_info(item_ids: list[int]) -> list[dict]:
    data = shopee_client._signed_get(  # noqa: SLF001 - internal client reuse
        "/api/v2/product/get_item_base_info",
        {"item_id_list": ",".join(str(item_id) for item_id in item_ids)},
    )
    return (data.get("response") or {}).get("item_list", [])


def _fetch_model_data(item_id: int) -> list[dict]:
    data = shopee_client._signed_get(  # noqa: SLF001 - internal client reuse
        "/api/v2/product/get_model_list",
        {"item_id": item_id},
    )
    return (data.get("response") or {}).get("model", [])


def _extract_price_idr(value: Any) -> int | None:
    """Extract IDR price from common Shopee item/model price shapes."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        digits = "".join(ch for ch in stripped if ch.isdigit())
        return int(digits) if digits else None
    if isinstance(value, list):
        for item in value:
            parsed = _extract_price_idr(item)
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, dict):
        for key in (
            "current_price",
            "price",
            "original_price",
            "discounted_price",
            "model_price",
            "item_price",
            "min_price",
            "max_price",
            "price_info",
            "price_info_list",
        ):
            if key in value:
                parsed = _extract_price_idr(value[key])
                if parsed is not None:
                    return parsed
    return None
