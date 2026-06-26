"""Unit tests for the /harga_set tiered-pricing pure logic."""

import pytest

from src.harga_set_price import (
    charm_round_up_to_nines,
    compute_shopee_pricing,
    parse_tiers,
    unit_price_for_quantity,
)


# ----------------------------------------------------------------------
# charm_round_up_to_nines (TikTok listing prices end in 99/999/9999)
# ----------------------------------------------------------------------
def test_charm_round_keeps_top_two_digits_and_nine_fills():
    assert charm_round_up_to_nines(1599) == 1599      # already ends 99
    assert charm_round_up_to_nines(7995) == 7999      # 4-digit → …99
    assert charm_round_up_to_nines(31980) == 31999    # 5-digit → …999
    assert charm_round_up_to_nines(77450) == 77999    # 5-digit → …999
    assert charm_round_up_to_nines(154900) == 159999  # 6-digit → …9999
    assert charm_round_up_to_nines(749500) == 749999  # 6-digit → …9999


def test_charm_round_never_undercharges_and_small_prices_untouched():
    for p in (100, 101, 250, 999, 1000, 12345, 999999):
        assert charm_round_up_to_nines(p) >= p
    assert charm_round_up_to_nines(99) == 99    # < 100 left alone
    assert charm_round_up_to_nines(50) == 50
    assert charm_round_up_to_nines(100) == 109  # 3-digit → keep "10", end in 9


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


# ----------------------------------------------------------------------
# compute_shopee_pricing (base price + Harga Grosir wholesale tiers)
# ----------------------------------------------------------------------
def test_shopee_pricing_documented_example():
    base, wholesale = compute_shopee_pricing([(1, 749), (50, 739), (100, 699)])
    assert base == 749
    assert wholesale == [(50, 99, 739), (100, 999999, 699)]


def test_shopee_pricing_single_tier_has_no_wholesale():
    base, wholesale = compute_shopee_pricing([(1, 749)])
    assert base == 749
    assert wholesale == []


def test_shopee_pricing_three_bulk_bands_are_contiguous():
    base, wholesale = compute_shopee_pricing([(1, 100), (10, 90), (50, 80), (100, 70)])
    assert base == 100
    assert wholesale == [(10, 49, 90), (50, 99, 80), (100, 999999, 70)]


def test_shopee_pricing_falls_back_to_lowest_tier_when_no_qty1():
    # No tier starts at 1: base falls back to the lowest tier's price.
    base, wholesale = compute_shopee_pricing([(50, 739), (100, 699)])
    assert base == 739
    assert wholesale == [(50, 99, 739), (100, 999999, 699)]
