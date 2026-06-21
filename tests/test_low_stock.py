"""Unit tests for the /stock_low report (pure scan + throttle window)."""

from datetime import datetime, timedelta, timezone

from src.low_stock import find_low_stock
from src.low_stock_throttle import window_open


def _v(units, multiplier=1):
    return {"stock_units": units, "multiplier": multiplier, "raw_sku": "x"}


# ----------------------------------------------------------------------
# find_low_stock
# ----------------------------------------------------------------------
def test_flags_combined_total_below_threshold():
    shopee = {
        "A": [_v(5)],     # 5
        "B": [_v(10)],    # 10 shopee
        "C": [_v(30)],    # 30 shopee
    }
    tiktokshop = {
        "B": [_v(10)],            # +10 -> B total 20
        "C": [_v(30)],            # +30 -> C total 60 (>= 50, excluded)
        "D": [_v(2, 20)],         # 2 units * 20 = 40, TikTok-only
    }
    result = find_low_stock(shopee, tiktokshop, threshold=50)

    assert [r["base_sku"] for r in result] == ["A", "B", "D"]  # sorted by total
    assert [r["total"] for r in result] == [5, 20, 40]
    by_sku = {r["base_sku"]: r for r in result}
    assert (by_sku["A"]["shopee"], by_sku["A"]["tiktokshop"]) == (5, 0)
    assert (by_sku["B"]["shopee"], by_sku["B"]["tiktokshop"]) == (10, 10)
    assert (by_sku["D"]["shopee"], by_sku["D"]["tiktokshop"]) == (0, 40)


def test_threshold_is_strict_less_than():
    # Exactly at the threshold is NOT low.
    catalog = {"E": [_v(50)]}
    assert find_low_stock(catalog, {}, threshold=50) == []
    assert [r["base_sku"] for r in find_low_stock({"E": [_v(49)]}, {}, 50)] == ["E"]


def test_multiplier_counts_pieces_not_units():
    # 3 units of a 20-pack = 60 pieces -> not low at threshold 50.
    assert find_low_stock({"P": [_v(3, 20)]}, {}, 50) == []


def test_empty_catalogs():
    assert find_low_stock({}, {}, 50) == []


def test_union_of_both_catalogs():
    result = find_low_stock({"ONLY_SHOPEE": [_v(1)]}, {"ONLY_TT": [_v(1)]}, 50)
    assert {r["base_sku"] for r in result} == {"ONLY_SHOPEE", "ONLY_TT"}


# ----------------------------------------------------------------------
# throttle window
# ----------------------------------------------------------------------
def test_window_open_when_never_run():
    assert window_open({"last_run_at": None}) is True
    assert window_open({}) is True


def test_window_open_after_24h():
    now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    old = (now - timedelta(hours=24, minutes=1)).isoformat()
    assert window_open({"last_run_at": old}, now=now) is True


def test_window_closed_within_24h():
    now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    recent = (now - timedelta(hours=23)).isoformat()
    assert window_open({"last_run_at": recent}, now=now) is False


def test_window_open_on_corrupt_timestamp():
    assert window_open({"last_run_at": "not-a-date"}) is True
