"""Unit tests for the catalog-audit pure rule logic (no network/openpyxl)."""

from src.catalog_audit import (
    audit_sku,
    grosir_ok,
    has_pack_variant,
    needs_tiktok_packs,
    shopee_min_buy_units,
    tiktok_min_buy_target,
)


# ---- Rule 3: Shopee minimum purchase = ceil(20.000 / base) ----
def test_shopee_min_buy_units_rounds_up():
    assert shopee_min_buy_units(3199) == 7      # ceil(20000/3199) = 7
    assert shopee_min_buy_units(1000) == 20
    assert shopee_min_buy_units(20000) == 1
    assert shopee_min_buy_units(25000) == 1     # ceil(0.8) = 1


def test_shopee_min_buy_units_handles_missing_price():
    assert shopee_min_buy_units(None) is None
    assert shopee_min_buy_units(0) is None


# ---- Rule 4: TikTok minimum purchase = 2 when base <= 5000 ----
def test_tiktok_min_buy_target():
    assert tiktok_min_buy_target(3199) == 2
    assert tiktok_min_buy_target(5000) == 2     # boundary inclusive
    assert tiktok_min_buy_target(5001) == 1
    assert tiktok_min_buy_target(None) is None


# ---- Rule 2: low-price TikTok SKUs need pack variants ----
def test_needs_tiktok_packs_threshold():
    assert needs_tiktok_packs(3199) is True
    assert needs_tiktok_packs(5000) is True
    assert needs_tiktok_packs(5001) is False
    assert needs_tiktok_packs(None) is False


def test_has_pack_variant_ignores_bubble_wrap():
    only_1pcs = [{"multiplier": 1, "raw_sku": "ITBISA-X"}]
    assert not has_pack_variant(only_1pcs)
    with_bubble = [
        {"multiplier": 1, "raw_sku": "ITBISA-X"},
        {"multiplier": 1, "raw_sku": "ITBISA-BUBBLE-WRAP"},
    ]
    assert not has_pack_variant(with_bubble)          # bubble wrap isn't a pack
    with_pack = [
        {"multiplier": 1, "raw_sku": "ITBISA-X"},
        {"multiplier": 20, "raw_sku": "20PCS-ITBISA-X"},
    ]
    assert has_pack_variant(with_pack)


# ---- Rule 1: Shopee Harga Grosir >= 3 layers ----
def test_grosir_ok():
    assert grosir_ok(3)
    assert grosir_ok(5)
    assert not grosir_ok(2)
    assert not grosir_ok(0)
    assert not grosir_ok(None)


# ---- audit_sku composition ----
def _variants(*mults):
    return [{"multiplier": m, "raw_sku": ("ITBISA-X" if m == 1 else f"{m}PCS-ITBISA-X")} for m in mults]


def test_audit_sku_flags_low_price_tiktok_without_packs():
    r = audit_sku(
        "ITBISA-X",
        shopee_variants=_variants(1),
        tiktok_variants=_variants(1),
        shopee_price=3199,
        grosir_layers=3,           # rule 1 OK
        tiktok_price=3199,         # <= 5000, only 1PCS -> rule 2 fails
    )
    assert r["rule1_grosir_ok"] is True
    assert r["tiktok_pack_ok"] is False
    assert r["violations"] == ["TikTok Shop belum punya varian pack (xxPCS)"]
    assert r["shopee_min_buy_target"] == 7
    assert r["tiktok_min_buy_target"] == 2


def test_audit_sku_flags_thin_grosir():
    r = audit_sku(
        "ITBISA-X",
        shopee_variants=_variants(1),
        tiktok_variants=_variants(1, 20),   # has a pack -> rule 2 OK
        shopee_price=1000,
        grosir_layers=1,                    # rule 1 fails
        tiktok_price=1000,
    )
    assert r["rule1_grosir_ok"] is False
    assert r["tiktok_pack_ok"] is True
    assert any("Harga Grosir" in v for v in r["violations"])


def test_audit_sku_fully_standardized_has_no_violations():
    r = audit_sku(
        "ITBISA-X",
        shopee_variants=_variants(1),
        tiktok_variants=_variants(1, 20),
        shopee_price=3199,
        grosir_layers=3,
        tiktok_price=3199,
    )
    assert r["violations"] == []


def test_audit_sku_high_price_tiktok_not_required_to_have_packs():
    r = audit_sku(
        "ITBISA-X",
        shopee_variants=[],
        tiktok_variants=_variants(1),       # only 1PCS but price > 5000
        shopee_price=None,
        grosir_layers=0,
        tiktok_price=9000,
    )
    assert r["tiktok_pack_ok"] is None      # rule 2 doesn't apply
    assert r["violations"] == []
    assert r["tiktok_min_buy_target"] == 1
