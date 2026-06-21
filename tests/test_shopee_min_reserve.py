"""Unit tests for the Shopee minimum-purchase reserve split (pure logic)."""

import pytest

from src.stock_allocator import shopee_min_reserve_units, split_with_shopee_min_reserve

MIN = 15000  # SHOPEE_MIN_PURCHASE_IDR default


# ----------------------------------------------------------------------
# shopee_min_reserve_units
# ----------------------------------------------------------------------
def test_reserve_basic_examples():
    assert shopee_min_reserve_units(100, 1000, MIN) == 15
    assert shopee_min_reserve_units(100, 5000, MIN) == 3
    assert shopee_min_reserve_units(100, 20000, MIN) == 1


def test_reserve_rounds_up():
    # 15000 / 700 = 21.43 -> 22
    assert shopee_min_reserve_units(100, 700, MIN) == 22


def test_reserve_capped_at_total():
    # Can't reserve more than exists: total 10 < 15 required -> give all 10.
    assert shopee_min_reserve_units(10, 1000, MIN) == 10


def test_reserve_zero_when_price_unknown_or_nonpositive():
    assert shopee_min_reserve_units(100, None, MIN) == 0
    assert shopee_min_reserve_units(100, 0, MIN) == 0
    assert shopee_min_reserve_units(100, -5, MIN) == 0


def test_reserve_zero_when_minimum_disabled():
    assert shopee_min_reserve_units(100, 1000, 0) == 0


def test_reserve_zero_when_no_stock():
    assert shopee_min_reserve_units(0, 1000, MIN) == 0


# ----------------------------------------------------------------------
# split_with_shopee_min_reserve
# ----------------------------------------------------------------------
def test_split_reserves_then_50_50():
    # reserve 15; remainder 85 -> (43, 42) -> Shopee 58, TikTok Shop 42
    assert split_with_shopee_min_reserve(100, 1000, MIN) == (58, 42)


def test_split_high_price_reserves_one():
    # reserve 1; remainder 99 -> (50, 49) -> (51, 49)
    assert split_with_shopee_min_reserve(100, 20000, MIN) == (51, 49)


def test_split_falls_back_to_plain_when_price_unknown():
    assert split_with_shopee_min_reserve(100, None, MIN) == (50, 50)


def test_split_gives_all_to_shopee_when_below_minimum():
    assert split_with_shopee_min_reserve(10, 1000, MIN) == (10, 0)


@pytest.mark.parametrize("total", [0, 1, 2, 3, 7, 15, 16, 99, 100, 5001, 123456])
@pytest.mark.parametrize("price", [None, 0, 700, 1000, 5000, 20000])
def test_split_never_loses_a_piece(total, price):
    shopee, tiktokshop = split_with_shopee_min_reserve(total, price, MIN)
    assert shopee + tiktokshop == total
    assert shopee >= 0 and tiktokshop >= 0
