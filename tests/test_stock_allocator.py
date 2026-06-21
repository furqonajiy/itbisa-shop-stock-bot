"""Unit tests for the pure stock-allocation logic.

This is the one module that enforces the "never lose stock" golden rule and
the 50:50 split. It has no I/O, so it is fully unit-testable. These tests
encode the documented invariants so they cannot silently regress.
"""

import pytest

from src.stock_allocator import (
    TIKTOKSHOP_1PCS_RESERVE_BASE_SKUS,
    allocate_pack_sizes,
    parse_sku,
    split_across_platforms,
    verify_allocation,
)


def _units_by_multiplier(allocations):
    return {v["multiplier"]: u for v, u in allocations}


# ----------------------------------------------------------------------
# parse_sku
# ----------------------------------------------------------------------
def test_parse_sku_plain_is_multiplier_one_and_uppercased():
    assert parse_sku("ITBISA-IC-NE555P-DIP8") == ("ITBISA-IC-NE555P-DIP8", 1)
    assert parse_sku("itbisa-lower") == ("ITBISA-LOWER", 1)


def test_parse_sku_pack_size_variant():
    assert parse_sku("25PCS-ITBISA-IC-NE555P-DIP8") == ("ITBISA-IC-NE555P-DIP8", 25)


def test_parse_sku_uppercases_base_for_cross_platform_match():
    # Shopee/TikTok Shop case differences collapse to one key.
    assert parse_sku("25PCS-itbisa-pcb-5x7") == ("ITBISA-PCB-5X7", 25)


def test_parse_sku_zero_multiplier_falls_back_safely():
    # 0PCS- is not a real pack size; treat the whole thing as multiplier 1.
    assert parse_sku("0PCS-ITBISA-FOO") == ("0PCS-ITBISA-FOO", 1)


# ----------------------------------------------------------------------
# split_across_platforms (50:50, Shopee absorbs the +1 on odd totals)
# ----------------------------------------------------------------------
def test_split_even():
    assert split_across_platforms(10000) == (5000, 5000)


def test_split_odd_gives_shopee_plus_one():
    assert split_across_platforms(10001) == (5001, 5000)


def test_split_zero_and_one():
    assert split_across_platforms(0) == (0, 0)
    assert split_across_platforms(1) == (1, 0)


def test_split_never_loses_a_piece():
    for total in (0, 1, 2, 3, 7, 99, 100, 12345):
        shopee, tiktokshop = split_across_platforms(total)
        assert shopee + tiktokshop == total


def test_split_negative_raises():
    with pytest.raises(ValueError):
        split_across_platforms(-1)


def test_split_70_30_shopee_heavy():
    assert split_across_platforms(100, 70) == (70, 30)
    assert split_across_platforms(10, 70) == (7, 3)
    assert split_across_platforms(1, 70) == (1, 0)  # Shopee absorbs the remainder
    assert split_across_platforms(0, 70) == (0, 0)


def test_split_custom_percent_never_loses_a_piece():
    for percent in (0, 30, 50, 70, 100):
        for total in (0, 1, 2, 3, 7, 99, 100, 12345):
            shopee, tiktokshop = split_across_platforms(total, percent)
            assert shopee + tiktokshop == total
            assert shopee >= 0 and tiktokshop >= 0


def test_split_invalid_percent_raises():
    with pytest.raises(ValueError):
        split_across_platforms(100, 150)


# ----------------------------------------------------------------------
# allocate_pack_sizes — Shopee (no cap), equal-share
# ----------------------------------------------------------------------
def test_allocate_shopee_documented_example():
    variants = [{"multiplier": 1}, {"multiplier": 20}, {"multiplier": 500}]
    result = allocate_pack_sizes(5000, variants)
    assert _units_by_multiplier(result) == {1: 1840, 20: 83, 500: 3}
    assert verify_allocation(5000, result) == 0


def test_allocate_shopee_remainder_goes_to_smallest():
    variants = [{"multiplier": 1}, {"multiplier": 10}]
    result = allocate_pack_sizes(25, variants)
    # Everything representable: 25 = 25*1 (+ 0*10) once remainder lands on m=1.
    assert verify_allocation(25, result) == 0


# ----------------------------------------------------------------------
# allocate_pack_sizes — TikTok Shop (capped, smallest-first, overflow→largest)
# ----------------------------------------------------------------------
def test_allocate_tiktokshop_documented_example():
    variants = [
        {"multiplier": 1},
        {"multiplier": 10},
        {"multiplier": 50},
        {"multiplier": 200},
    ]
    result = allocate_pack_sizes(3500, variants, tiktokshop_unit_cap=400)
    assert _units_by_multiplier(result) == {1: 400, 10: 310, 50: 0, 200: 0}
    assert verify_allocation(3500, result) == 0


def test_allocate_tiktokshop_overflow_stacks_on_largest_and_loses_nothing():
    variants = [
        {"multiplier": 1},
        {"multiplier": 10},
        {"multiplier": 50},
        {"multiplier": 200},
    ]
    result = allocate_pack_sizes(150000, variants, tiktokshop_unit_cap=400)
    units = _units_by_multiplier(result)
    assert units == {1: 400, 10: 400, 50: 400, 200: 628}
    # Golden rule: overflow goes onto the largest variant, no pieces dropped.
    assert verify_allocation(150000, result) == 0


def test_allocate_zero_pieces_sets_everything_to_zero():
    variants = [{"multiplier": 1}, {"multiplier": 10}]
    assert _units_by_multiplier(allocate_pack_sizes(0, variants, 400)) == {1: 0, 10: 0}


# ----------------------------------------------------------------------
# TikTok Shop 1PCS-reserve exception
# ----------------------------------------------------------------------
def test_tiktokshop_1pcs_reserve_keeps_one_on_the_1pcs_variant():
    base = next(iter(TIKTOKSHOP_1PCS_RESERVE_BASE_SKUS))
    variants = [
        {"multiplier": 1, "raw_sku": f"1PCS-{base}"},
        {"multiplier": 10, "raw_sku": f"10PCS-{base}"},
        {"multiplier": 50, "raw_sku": f"50PCS-{base}"},
    ]
    result = allocate_pack_sizes(100, variants, tiktokshop_unit_cap=400)
    units = _units_by_multiplier(result)
    assert units[1] == 1  # reserved to exactly one unit
    assert units[10] == 9  # remaining 99 pcs balanced across the others


def test_non_reserve_sku_does_not_reserve_the_1pcs_variant():
    variants = [
        {"multiplier": 1, "raw_sku": "1PCS-ITBISA-NOT-IN-RESERVE-LIST"},
        {"multiplier": 10, "raw_sku": "10PCS-ITBISA-NOT-IN-RESERVE-LIST"},
    ]
    result = allocate_pack_sizes(25, variants, tiktokshop_unit_cap=400)
    units = _units_by_multiplier(result)
    assert units[1] == 25  # normal capped fill, not reserved


# ----------------------------------------------------------------------
# verify_allocation — surfaces unrepresentable ("lost") pieces
# ----------------------------------------------------------------------
def test_verify_allocation_reports_lost_pieces():
    # 10 pieces against a 20-pack-only SKU cannot be represented.
    variants = [{"multiplier": 20}]
    result = allocate_pack_sizes(10, variants)
    assert _units_by_multiplier(result) == {20: 0}
    assert verify_allocation(10, result) == 10


# ----------------------------------------------------------------------
# input validation
# ----------------------------------------------------------------------
def test_empty_variants_raises():
    with pytest.raises(ValueError):
        allocate_pack_sizes(100, [])


def test_negative_pieces_raises():
    with pytest.raises(ValueError):
        allocate_pack_sizes(-1, [{"multiplier": 1}])


def test_negative_cap_raises():
    with pytest.raises(ValueError):
        allocate_pack_sizes(100, [{"multiplier": 1}], tiktokshop_unit_cap=-1)
