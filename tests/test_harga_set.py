"""Unit tests for the /harga_set tiered-pricing pure logic."""

import pytest

from src.harga_set_price import parse_tiers, unit_price_for_quantity


# ----------------------------------------------------------------------
# parse_tiers
# ----------------------------------------------------------------------
def test_parse_tiers_basic_sorted():
    assert parse_tiers(["1", "749", "50", "739", "100", "699"]) == [
        (1, 749),
        (50, 739),
        (100, 699),
    ]


def test_parse_tiers_sorts_unordered_input():
    assert parse_tiers(["100", "699", "1", "749", "50", "739"]) == [
        (1, 749),
        (50, 739),
        (100, 699),
    ]


def test_parse_tiers_single_tier():
    assert parse_tiers(["1", "749"]) == [(1, 749)]


def test_parse_tiers_odd_token_count_raises():
    with pytest.raises(ValueError):
        parse_tiers(["1", "749", "50"])


def test_parse_tiers_empty_raises():
    with pytest.raises(ValueError):
        parse_tiers([])


def test_parse_tiers_non_integer_raises():
    with pytest.raises(ValueError):
        parse_tiers(["1", "749", "x", "739"])


def test_parse_tiers_qty_below_one_raises():
    with pytest.raises(ValueError):
        parse_tiers(["0", "749"])


def test_parse_tiers_negative_price_raises():
    with pytest.raises(ValueError):
        parse_tiers(["1", "-5"])


def test_parse_tiers_duplicate_start_qty_raises():
    with pytest.raises(ValueError):
        parse_tiers(["50", "739", "50", "699"])


# ----------------------------------------------------------------------
# unit_price_for_quantity (tier banding by highest start_qty <= qty)
# ----------------------------------------------------------------------
TIERS = [(1, 749), (50, 739), (100, 699)]


def test_band_documented_examples():
    assert unit_price_for_quantity(TIERS, 1) == 749
    assert unit_price_for_quantity(TIERS, 10) == 749
    assert unit_price_for_quantity(TIERS, 49) == 749
    assert unit_price_for_quantity(TIERS, 50) == 739
    assert unit_price_for_quantity(TIERS, 99) == 739
    assert unit_price_for_quantity(TIERS, 100) == 699
    assert unit_price_for_quantity(TIERS, 1000) == 699


def test_band_below_lowest_tier_is_none():
    # Lowest tier starts at 5: quantities 1-4 cannot be banded.
    tiers = [(5, 100), (50, 90)]
    assert unit_price_for_quantity(tiers, 1) is None
    assert unit_price_for_quantity(tiers, 4) is None
    assert unit_price_for_quantity(tiers, 5) == 100
    assert unit_price_for_quantity(tiers, 49) == 100
    assert unit_price_for_quantity(tiers, 50) == 90


def test_band_variant_listing_price_is_unit_times_pack():
    # 50PCS variant at the 50-tier: listing price = 739 * 50.
    unit = unit_price_for_quantity(TIERS, 50)
    assert unit * 50 == 36950
