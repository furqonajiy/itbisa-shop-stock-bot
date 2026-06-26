"""Unit tests for the /weight_set Edit-Product payload builder (pure logic)."""

import pytest

from src.weight_set_tiktok import build_weight_edit_payload

# Synthetic detail mirroring a re-weight target: existing variants + Bubble Wrap.
_DETAIL = {
    "id": "P1",
    "title": "IC PC817",
    "description": "<p>desc</p>",
    "category_chains": [{"id": "825992", "is_leaf": True, "local_name": "Unit Catu Daya"}],
    "main_images": [{"uri": "tos-img/abc.jpg"}],
    "package_weight": {"unit": "KILOGRAM", "value": "0.21"},
    "package_dimensions": {"height": "0", "length": "0", "width": "0", "unit": "CENTIMETER"},
    "product_attributes": [
        {"id": "100107", "name": "Garansi", "values": [{"id": "1000057", "name": "Tanpa"}]},
    ],
    "skus": [
        {
            "id": "s1", "seller_sku": "ITBISA-IC-PC817-DIP4",
            "inventory": [{"warehouse_id": "WH1", "quantity": 12}],
            "price": {"currency": "IDR", "sale_price": "599"},
            "sku_weight": {"unit": "KILOGRAM", "value": "0.5"},
            "sales_attributes": [{"id": "ATTR", "name": "Packing", "value_id": "V1", "value_name": "1PCS"}],
        },
        {
            "id": "s20", "seller_sku": "20PCS-ITBISA-IC-PC817-DIP4",
            "inventory": [{"warehouse_id": "WH1", "quantity": 7}],
            "price": {"currency": "IDR", "sale_price": "11860"},
            "sku_weight": {"unit": "KILOGRAM", "value": "9"},
            "sales_attributes": [{"id": "ATTR", "name": "Packing", "value_id": "V20", "value_name": "20PCS"}],
        },
        {
            "id": "sbw", "seller_sku": "ITBISA-BUBBLE-WRAP",
            "inventory": [{"warehouse_id": "WH1", "quantity": 0}],
            "price": {"currency": "IDR", "sale_price": "100"},
            "sku_weight": {"unit": "KILOGRAM", "value": "0.001"},
            "sales_attributes": [{"id": "ATTR", "name": "Packing", "value_id": "VBW", "value_name": "Bubble Wrap"}],
        },
    ],
}


def _by_value(payload):
    return {s["sales_attributes"][0]["value_name"]: s for s in payload["skus"]}


def test_per_piece_weight_scales_by_multiplier():
    # 1700 g / 1000 = 1.7 g/pcs -> 1PCS rounds up to 2 g, 20PCS = 34 g, in GRAM.
    payload = build_weight_edit_payload(_DETAIL, "ITBISA-IC-PC817-DIP4", 1000, 1700)
    by = _by_value(payload)
    assert by["1PCS"]["sku_weight"] == {"value": "2", "unit": "GRAM"}
    assert by["20PCS"]["sku_weight"] == {"value": "34", "unit": "GRAM"}


def test_reference_100pcs_850g_rounds_up_to_integer_grams():
    # /weight_set ... 100 850 -> 8.5 g/pcs (the operator's relay case). TikTok
    # rejects decimal grams (error 12019011), so 8.5 g rounds UP to 9 g.
    payload = build_weight_edit_payload(_DETAIL, "ITBISA-IC-PC817-DIP4", 100, 850)
    by = _by_value(payload)
    assert by["1PCS"]["sku_weight"] == {"value": "9", "unit": "GRAM"}    # 8.5 -> 9
    assert by["20PCS"]["sku_weight"] == {"value": "170", "unit": "GRAM"}


def test_all_weights_are_integer_grams_at_or_above_floor():
    # Every per-SKU weight is a whole number of grams, never 0 / kg / decimal.
    payload = build_weight_edit_payload(_DETAIL, "ITBISA-IC-PC817-DIP4", 1000, 100)
    by = _by_value(payload)
    # 100 g / 1000 = 0.1 g/pcs -> 1PCS floors to 1 g, 20PCS rounds up 2 -> 2 g.
    assert by["1PCS"]["sku_weight"] == {"value": "1", "unit": "GRAM"}
    assert by["20PCS"]["sku_weight"] == {"value": "2", "unit": "GRAM"}
    for s in payload["skus"]:
        w = s["sku_weight"]
        assert w["unit"] == "GRAM"
        assert "." not in w["value"]          # integer only
        assert int(w["value"]) >= 1           # at/above the 1 g floor


def test_product_package_weight_is_integer_grams():
    # The product-level package_weight is also normalised to integer grams
    # (error 12019011 "product package weight is invalid" otherwise).
    payload = build_weight_edit_payload(_DETAIL, "ITBISA-IC-PC817-DIP4", 100, 850)
    pkg = payload["package_weight"]
    assert pkg["unit"] == "GRAM"
    assert "." not in pkg["value"]
    assert pkg["value"] == "210"              # 0.21 kg -> 210 g


def test_bubble_wrap_keeps_its_own_weight():
    # Bubble Wrap is preserved (existing 0.001 kg = 1 g), not recomputed from the
    # per-piece reference (which would give 2 g for the 1000/1700 case).
    payload = build_weight_edit_payload(_DETAIL, "ITBISA-IC-PC817-DIP4", 1000, 1700)
    bw = _by_value(payload)["Bubble Wrap"]
    assert bw["sku_weight"] == {"value": "1", "unit": "GRAM"}


def test_stock_and_price_preserved():
    payload = build_weight_edit_payload(_DETAIL, "ITBISA-IC-PC817-DIP4", 1000, 1700)
    by = _by_value(payload)
    assert by["1PCS"]["inventory"] == [{"warehouse_id": "WH1", "quantity": 12}]
    assert by["20PCS"]["inventory"] == [{"warehouse_id": "WH1", "quantity": 7}]
    assert by["1PCS"]["price"]["amount"] == "599"
    assert by["20PCS"]["price"]["amount"] == "11860"


def test_per_sku_weight_uses_sku_weight_field_not_package_weight():
    # Per-variant weight must go in `sku_weight`; a per-SKU `package_weight` is
    # ignored by Edit Product and collapses every variant to the product weight.
    payload = build_weight_edit_payload(_DETAIL, "ITBISA-IC-PC817-DIP4", 1000, 1700)
    for s in payload["skus"]:
        assert "sku_weight" in s
        assert "package_weight" not in s


def test_variation_set_is_unchanged():
    payload = build_weight_edit_payload(_DETAIL, "ITBISA-IC-PC817-DIP4", 1000, 1700)
    names = [s["sales_attributes"][0]["value_name"] for s in payload["skus"]]
    assert names == ["1PCS", "20PCS", "Bubble Wrap"]


def test_declares_v2_category_version():
    payload = build_weight_edit_payload(_DETAIL, "ITBISA-IC-PC817-DIP4", 1000, 1700)
    assert payload["category_version"] == "v2"


def test_invalid_reference_rejected():
    with pytest.raises(ValueError):
        build_weight_edit_payload(_DETAIL, "ITBISA-IC-PC817-DIP4", 0, 1700)
    with pytest.raises(ValueError):
        build_weight_edit_payload(_DETAIL, "ITBISA-IC-PC817-DIP4", 1000, 0)
