"""Unit tests for the Shopee wholesale verify helper (_wholesale_satisfied).

Shopee's Open API silently ignores wholesale writes (update_item returns HTTP
200 without applying `wholesales`), so set_wholesale re-reads the live tiers and
only counts a write that actually took effect."""

from src.shopee_client import _wholesale_satisfied


def test_satisfied_when_all_requested_tiers_present():
    want = [(50, 99, 3149), (100, 999999, 3099)]
    assert _wholesale_satisfied(want, [(50, 99, 3149), (100, 999999, 3099)])


def test_ignores_max_count_normalisation():
    # Shopee may normalise the open-ended top band's max_count; match on
    # (min_count, unit_price) only.
    want = [(50, 99, 3149), (100, 999999, 3099)]
    live = [(50, 120, 3149), (100, 99999, 3099)]
    assert _wholesale_satisfied(want, live)


def test_not_satisfied_when_tiers_are_stale():
    # The real bug: base price applied but Harga Grosir stayed at old values.
    want = [(50, 99, 3149), (100, 999999, 3099)]
    stale = [(100, 999, 2599), (1000, 9999, 2499)]
    assert not _wholesale_satisfied(want, stale)


def test_not_satisfied_when_live_is_empty():
    assert not _wholesale_satisfied([(50, 99, 3149)], [])


def test_partial_match_is_not_satisfied():
    want = [(50, 99, 3149), (100, 999999, 3099)]
    live = [(50, 99, 3149)]  # missing the 100+ tier
    assert not _wholesale_satisfied(want, live)
