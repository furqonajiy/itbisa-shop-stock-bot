"""Unit tests for the Shopee minimum-purchase reserve split (pure logic)."""

import pytest

from src.stock_allocator import (
    shopee_min_buy_units,
    shopee_min_reserve_units,
    split_with_shopee_min_reserve,
)

MIN = 15000  # arbitrary reserve value exercising the pure-function math


# ----------------------------------------------------------------------
# shopee_min_buy_units (listing minimum-purchase target = ceil(idr / base))
# ----------------------------------------------------------------------
def test_min_buy_units_rounds_up():
    assert shopee_min_buy_units(2199, 20000) == 10   # ceil(9.09)
    assert shopee_min_buy_units(3199, 20000) == 7     # ceil(6.25)
    assert shopee_min_buy_units(1000, 20000) == 20
    assert shopee_min_buy_units(20000, 20000) == 1
    assert shopee_min_buy_units(25000, 20000) == 1    # ceil(0.8)


def test_min_buy_units_unknown_or_disabled():
    assert shopee_min_buy_units(None, 20000) is None
    assert shopee_min_buy_units(0, 20000) is None
    assert shopee_min_buy_units(2199, 0) is None       # disabled


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


# ----------------------------------------------------------------------
# Production config: Rp200.000 reserve + 70:30 remainder split
# ----------------------------------------------------------------------
RESERVE = 200000  # SHOPEE_RESERVE_IDR
PCT = 70          # SHOPEE_SPLIT_PERCENT


def test_reserve_200k_examples():
    assert shopee_min_reserve_units(1000, 1000, RESERVE) == 200   # 200000/1000
    assert shopee_min_reserve_units(1000, 5000, RESERVE) == 40
    assert shopee_min_reserve_units(100, 1000, RESERVE) == 100    # total < 200 -> all


def test_split_reserve_then_70_30():
    # total 1000, price 1.000 -> reserve 200; remainder 800 -> Shopee 560 / TikTok 240
    assert split_with_shopee_min_reserve(1000, 1000, RESERVE, PCT) == (760, 240)


def test_split_no_price_is_plain_70_30():
    assert split_with_shopee_min_reserve(1000, None, RESERVE, PCT) == (700, 300)


def test_split_reserve_70_30_never_loses_a_piece():
    for total in (0, 1, 50, 199, 200, 201, 1000, 99999):
        for price in (None, 1000, 5000):
            shopee, tiktokshop = split_with_shopee_min_reserve(total, price, RESERVE, PCT)
            assert shopee + tiktokshop == total
            assert shopee >= 0 and tiktokshop >= 0
