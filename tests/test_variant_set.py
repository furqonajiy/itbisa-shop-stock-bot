"""Unit tests for the /variant_set Edit-Product payload builder (pure logic)."""

from src.variant_set_tiktok import build_edit_payload

# Synthetic detail mirroring the real ITBISA-IC-PC817-DIP4 structure.
_DETAIL = {
    "id": "P1",
    "title": "IC PC817",
    "description": "<p>desc</p>",
    "category_chains": [
        {"id": "601755", "is_leaf": False},
        {"id": "825992", "is_leaf": True},
    ],
    "main_images": [{"uri": "tos-img/abc.jpg", "urls": ["https://x"]}],
    "package_weight": {"unit": "KILOGRAM", "value": "0.21"},
    "package_dimensions": {"height": "0", "length": "0", "width": "0", "unit": "CENTIMETER"},
    "product_attributes": [
        {"id": "100107", "name": "Garansi", "values": [{"id": "1000057", "name": "Tanpa"}]},
    ],
    "skus": [
        {
            "id": "s1", "seller_sku": "ITBISA-IC-PC817-DIP4",
            "inventory": [{"warehouse_id": "WH1", "quantity": 1}],
            "price": {"currency": "IDR", "sale_price": "599"},
            "sku_weight": {"unit": "KILOGRAM", "value": "0.001"},
            "sales_attributes": [{"id": "ATTR", "name": "Packing", "value_id": "V1", "value_name": "1PCS"}],
        },
        {
            "id": "s5", "seller_sku": "5PCS-ITBISA-IC-PC817-DIP4",
            "inventory": [{"warehouse_id": "WH1", "quantity": 170}],
            "price": {"currency": "IDR", "sale_price": "2980"},
            "sku_weight": {"unit": "KILOGRAM", "value": "0.003"},
            "sales_attributes": [{"id": "ATTR", "name": "Packing", "value_id": "V5", "value_name": "5PCS"}],
        },
        {
            "id": "s20", "seller_sku": "20PCS-ITBISA-IC-PC817-DIP4",
            "inventory": [{"warehouse_id": "WH1", "quantity": 0}],
            "price": {"currency": "IDR", "sale_price": "11860"},
            "sku_weight": {"unit": "KILOGRAM", "value": "0.009"},
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
    return {
        s["sales_attributes"][0]["value_name"]: s
        for s in payload["skus"]
    }


def test_payload_has_exactly_requested_packs_plus_bubble_wrap():
    payload = build_edit_payload(_DETAIL, "ITBISA-IC-PC817-DIP4", [1, 20, 50, 500, 1000])
    names = [s["sales_attributes"][0]["value_name"] for s in payload["skus"]]
    assert names == ["1PCS", "20PCS", "50PCS", "500PCS", "1000PCS", "Bubble Wrap"]
    # 5PCS (dropped) is gone
    assert "5PCS" not in names


def test_seller_skus_and_1pcs_has_no_prefix():
    payload = build_edit_payload(_DETAIL, "ITBISA-IC-PC817-DIP4", [1, 20, 1000])
    by = _by_value(payload)
    assert by["1PCS"]["seller_sku"] == "ITBISA-IC-PC817-DIP4"
    assert by["20PCS"]["seller_sku"] == "20PCS-ITBISA-IC-PC817-DIP4"
    assert by["1000PCS"]["seller_sku"] == "1000PCS-ITBISA-IC-PC817-DIP4"
    assert by["Bubble Wrap"]["seller_sku"] == "ITBISA-BUBBLE-WRAP"


def test_existing_values_keep_value_id_and_price_new_ones_scale():
    payload = build_edit_payload(_DETAIL, "ITBISA-IC-PC817-DIP4", [1, 20, 50, 1000])
    by = _by_value(payload)
    # existing 20PCS keeps its value_id and price
    assert by["20PCS"]["sales_attributes"][0].get("value_id") == "V20"
    assert by["20PCS"]["price"]["amount"] == "11860"
    # new 50PCS: no value_id, price scaled from 1PCS (599 * 50)
    assert "value_id" not in by["50PCS"]["sales_attributes"][0]
    assert by["50PCS"]["price"]["amount"] == str(599 * 50)
    assert by["1000PCS"]["price"]["amount"] == str(599 * 1000)


def test_bubble_wrap_is_price_100_and_stock_zero():
    payload = build_edit_payload(_DETAIL, "ITBISA-IC-PC817-DIP4", [1, 20])
    bw = _by_value(payload)["Bubble Wrap"]
    assert bw["price"]["amount"] == "100"
    assert bw["inventory"][0]["quantity"] == 0
    assert bw["sales_attributes"][0].get("value_id") == "VBW"


def test_all_new_variants_created_at_zero_stock():
    payload = build_edit_payload(_DETAIL, "ITBISA-IC-PC817-DIP4", [1, 20, 50, 500, 1000])
    for s in payload["skus"]:
        assert s["inventory"][0]["quantity"] == 0
        assert s["inventory"][0]["warehouse_id"] == "WH1"


def test_product_level_fields_preserved():
    payload = build_edit_payload(_DETAIL, "ITBISA-IC-PC817-DIP4", [1, 20])
    # No recommended_categories on this fixture → no V2 leaf → category_id is
    # omitted so TikTok keeps the product's current category (the V1 chain leaf
    # would be rejected with error 12052217).
    assert "category_id" not in payload
    assert payload["main_images"] == [{"uri": "tos-img/abc.jpg"}]
    assert payload["title"] == "IC PC817"
    assert payload["product_attributes"] == [{"id": "100107", "values": [{"id": "1000057"}]}]


def test_recommended_v2_category_is_sent_when_available():
    detail = dict(_DETAIL)
    # When the detail offers a V2 recommendation, send its leaf.
    detail["recommended_categories"] = [
        {"id": "900001", "is_leaf": False, "local_name": "Elektronik"},
        {"id": "900042", "is_leaf": True, "local_name": "Komponen Elektronik"},
    ]
    payload = build_edit_payload(detail, "ITBISA-IC-PC817-DIP4", [1, 20])
    assert payload["category_id"] == "900042"
