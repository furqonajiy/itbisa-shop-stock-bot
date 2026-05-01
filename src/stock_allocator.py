"""
stock_allocator.py
------------------
Pure-math stock allocation logic shared by Shopee and TikTok Shop.

Two responsibilities, no I/O:

  1. split_across_platforms(total_pieces)
     Splits one warehouse stock count between Shopee and TikTok Shop 50:50.
     If total is odd, Shopee absorbs the +1 (operator decision).

  2. allocate_pack_sizes(pieces, variants, small_pack_reserve_pieces=None)
     Distributes a physical piece count across pack-size variants of the
     same base SKU. Returns whole units per variant such that the sum of
     (units * multiplier) is as close to `pieces` as possible.

     Two algorithms, switched by `small_pack_reserve_pieces`:

     - small_pack_reserve_pieces=None (Shopee): equal-share split. Each
       variant gets `pieces // N` pieces budgeted, rounded down to whole
       units, with any leftover absorbed by the smallest-multiplier
       variant. No per-variant cap.

     - small_pack_reserve_pieces=K (TikTok Shop): large-order-aware
       split. Reserve up to K physical pieces on the smallest pack-size
       variant so small buyers still see stock, then move the remaining
       stock to the largest pack-size variant. Any remainder that cannot
       fit into the largest pack is represented by smaller variants.

This module deliberately has no API client imports — it is the one
piece of logic that is identical on both platforms (parametrised by
the TikTok Shop reserve), and we want it unit-testable without network.
Both `shopee_client.py` and `tiktokshop_client.py` call into it with
platform-specific variant dicts; the only contract the allocator cares
about is the `multiplier` field on each variant.

Allocation algorithms (per platform, per base SKU):

  Shopee (no reserve):
    Given P pieces and N pack-size variants with multipliers m_1..m_N:
      1. share = P // N           # pieces per variant
      2. units_i = share // m_i   # whole units per pack size (rounded down)
      3. represented = sum(units_i * m_i)
      4. remainder = P - represented
      5. The smallest pack size absorbs (remainder // m_smallest) extra units.
         Anything below m_smallest cannot be allocated and is "lost".

  TikTok Shop (small-pack reserve = K physical pieces):
    Sort variants ascending by multiplier.
      1. If only one variant exists, allocate as many whole units as possible.
      2. Reserve up to K physical pieces on the smallest variant.
      3. Allocate the remaining stock to the largest variant first.
      4. Use middle/small variants to absorb leftover pieces below the
         largest multiplier.

    Worked example: P=2000, variants=[(m=1), (m=100)], K=200
      m=1 reserve:   min(200 pcs, 2000 pcs) = 200 units (= 200 pcs)
      m=100 bulk:    1800 // 100            =  18 units (= 1800 pcs)
      Verify: 200*1 + 18*100 = 2000 ✓

    This maximises the quantity a buyer can place in one order when
    TikTok Shop limits quantity per variant. Stock above that practical
    limit on the 1PCS variant does not help a large buyer; putting it
    into 100PCS does.
"""

from __future__ import annotations

import re
from typing import Iterable


# Pack-size variant pattern: "<digits>PCS-<base_sku>". Anchored so
# "ITBISA-25PCS-FOO" does NOT match (no digits at start). Case-sensitive
# because both shops publish the prefix in uppercase by convention.
PACK_SIZE_PATTERN = re.compile(r"^(\d+)PCS-(.+)$")


def parse_sku(seller_sku: str) -> tuple[str, int]:
    """
    Returns (base_sku, multiplier) for a seller SKU string.

    A SKU matching "<digits>PCS-<base>" is recognised as a pack-size
    variant. Anything else (including malformed inputs and multiplier 0)
    is treated as a non-variant with multiplier 1 — the degenerate case
    where the allocator just sets the SKU's stock to the input value.

    Examples:
      parse_sku("ITBISA-IC-NE555P-DIP8")        -> ("ITBISA-IC-NE555P-DIP8", 1)
      parse_sku("25PCS-ITBISA-IC-NE555P-DIP8")  -> ("ITBISA-IC-NE555P-DIP8", 25)
      parse_sku("0PCS-ITBISA-FOO")              -> ("0PCS-ITBISA-FOO", 1)  # safe fallback
    """
    match = PACK_SIZE_PATTERN.match(seller_sku)
    if match:
        multiplier = int(match.group(1))
        if multiplier > 0:
            return match.group(2), multiplier
    return seller_sku, 1


def split_across_platforms(total_pieces: int) -> tuple[int, int]:
    """
    Splits total_pieces between Shopee and TikTok Shop 50:50.

    On odd totals, Shopee absorbs the +1 piece. Reason: Shopee almost
    always has a 1-pc variant (or no variant at all), so the +1 is
    trivially representable. TikTok Shop then runs its own pack-size
    rebalance on a clean even number.

    Returns (shopee_pieces, tiktokshop_pieces).

    Examples:
      split_across_platforms(10000) -> (5000, 5000)
      split_across_platforms(10001) -> (5001, 5000)
      split_across_platforms(0)     -> (0, 0)
      split_across_platforms(1)     -> (1, 0)
    """
    if total_pieces < 0:
        raise ValueError(f"total_pieces must be non-negative, got {total_pieces}")
    tiktokshop_pieces = total_pieces // 2
    shopee_pieces = total_pieces - tiktokshop_pieces
    return shopee_pieces, tiktokshop_pieces


def allocate_pack_sizes(
        pieces: int,
        variants: list[dict],
        small_pack_reserve_pieces: int | None = None,
) -> list[tuple[dict, int]]:
    """
    Distributes `pieces` across pack-size variants of one base SKU.

    Args:
      pieces:                    total physical pieces to allocate.
      variants:                  list of dicts, each with at least a
                                 'multiplier' key. Variants may carry any
                                 other platform-specific fields (sku_id,
                                 item_id, model_id, warehouse_id, etc.);
                                 the allocator preserves them untouched.
      small_pack_reserve_pieces: if None, no TikTok Shop reserve
                                 (Shopee-style: equal share with smallest
                                 absorbing remainder).
                                 If set, reserve up to this many physical
                                 pieces on the smallest pack-size variant,
                                 then push the remaining stock to the
                                 largest pack-size variant first.

    Returns:
      List of (variant_dict, units_to_set) tuples in ascending multiplier
      order. units_to_set is the absolute unit count to push to that
      variant (NOT a delta).

    Raises:
      ValueError if variants list is empty (caller should skip such SKUs)
      or if pieces / small_pack_reserve_pieces is negative.

    Worked example WITHOUT reserve (Shopee), pieces=5000, variants=[m:1, m:20, m:500]:
      share = 5000 // 3 = 1666 pcs/variant
        m=1:   1666 // 1   = 1666 units
        m=20:  1666 // 20  =   83 units (= 1660 pcs)
        m=500: 1666 // 500 =    3 units (= 1500 pcs)
      represented = 4826; remainder = 174 → +174 units on m=1
      Final: [(v0, 1840), (v1, 83), (v2, 3)]
      Verify: 1840*1 + 83*20 + 3*500 = 5000 ✓

    Worked example WITH small_pack_reserve_pieces=200 (TikTok Shop),
    pieces=2000, variants=[m:1, m:100]:
      m=1 reserve: 200 units (= 200 pcs)
      m=100 bulk:   18 units (= 1800 pcs)
      Final: [(v0, 200), (v1, 18)]
      Verify: 200*1 + 18*100 = 2000 ✓
    """
    if not variants:
        raise ValueError("variants must contain at least one entry")
    if pieces < 0:
        raise ValueError(f"pieces must be non-negative, got {pieces}")
    if small_pack_reserve_pieces is not None and small_pack_reserve_pieces < 0:
        raise ValueError(
            f"small_pack_reserve_pieces must be non-negative, got {small_pack_reserve_pieces}"
        )

    # Defensive sort: caller is expected to sort, but a re-sort here is
    # near-free and prevents future caller bugs from corrupting output.
    variants = sorted(variants, key=lambda v: v["multiplier"])

    if small_pack_reserve_pieces is None:
        return _allocate_unconstrained(pieces, variants)
    return _allocate_tiktokshop_order_aware(pieces, variants, small_pack_reserve_pieces)


def verify_allocation(pieces: int, allocations: Iterable[tuple[dict, int]]) -> int:
    """
    Returns pieces_lost = pieces - sum(units * multiplier).

    Called by the dry-run formatter so the operator sees when an
    allocation cannot fully represent the input (e.g., 3 leftover pieces
    on a product whose smallest variant is 20-pack).

    Always >= 0. Zero means perfect allocation.
    """
    represented = sum(units * v["multiplier"] for v, units in allocations)
    return pieces - represented


# ============================================================
# Internals
# ============================================================

def _allocate_unconstrained(
        pieces: int,
        variants: list[dict],
) -> list[tuple[dict, int]]:
    """Shopee path: equal share, remainder onto smallest multiplier."""
    n = len(variants)
    share = pieces // n  # pieces budgeted per variant

    allocations: list[list] = []
    represented = 0
    for variant in variants:
        units = share // variant["multiplier"]
        allocations.append([variant, units])
        represented += units * variant["multiplier"]

    # Push leftover pieces onto the smallest pack size. If the smallest
    # multiplier is > 1 and remainder < smallest_multiplier, those
    # pieces simply cannot be represented and are lost — flag this in
    # the caller's logging, not here.
    remainder = pieces - represented
    if remainder > 0:
        smallest = allocations[0][0]
        extra_units = remainder // smallest["multiplier"]
        allocations[0][1] += extra_units

    return [(v, u) for v, u in allocations]


def _allocate_tiktokshop_order_aware(
        pieces: int,
        variants: list[dict],
        small_pack_reserve_pieces: int,
) -> list[tuple[dict, int]]:
    """
    TikTok Shop path: reserve small-pack stock, then push bulk stock to
    the largest pack size.

    Why this exists:
      TikTok Shop variants are siblings under one product. In practice,
      a large buyer may be limited by max quantity per variant in one
      order. Storing too much stock on 1PCS does not help that buyer,
      because they still cannot select unlimited 1PCS units. Bulk stock
      should therefore live on the largest pack variant.

    Example with 2000 platform pieces and [1PCS, 100PCS]:
      Old equal/capped style: 1000 x 1PCS + 10 x 100PCS.
      Better order-aware:     200 x 1PCS + 18 x 100PCS.
    """
    allocations: list[list] = [[variant, 0] for variant in variants]

    if pieces == 0:
        return [(v, u) for v, u in allocations]

    # Degenerate case: there is no smaller-vs-larger decision to make.
    if len(allocations) == 1:
        only_variant = allocations[0][0]
        allocations[0][1] = pieces // only_variant["multiplier"]
        return [(v, u) for v, u in allocations]

    remaining = pieces

    # Keep the smallest pack visible for small buyers, but cap the
    # reserve by PHYSICAL PIECES, not units. This avoids the bad case
    # where 200 units of a 10PCS smallest variant consumes 2000 pieces.
    smallest = allocations[0][0]
    reserve_units = min(
        small_pack_reserve_pieces // smallest["multiplier"],
        remaining // smallest["multiplier"],
    )
    allocations[0][1] = reserve_units
    remaining -= reserve_units * smallest["multiplier"]

    # Put the main bulk on the largest pack variant. This is what raises
    # the maximum purchasable pieces in one order.
    largest = allocations[-1][0]
    bulk_units = remaining // largest["multiplier"]
    allocations[-1][1] = bulk_units
    remaining -= bulk_units * largest["multiplier"]

    # Represent any leftover pieces below the largest multiplier using
    # the next-largest variants first, then smallest. This keeps the
    # final physical allocation as close to the requested stock as
    # possible without reducing the bulk allocation.
    for allocation in reversed(allocations[:-1]):
        if remaining <= 0:
            break
        variant = allocation[0]
        extra_units = remaining // variant["multiplier"]
        if extra_units <= 0:
            continue
        allocation[1] += extra_units
        remaining -= extra_units * variant["multiplier"]

    return [(v, u) for v, u in allocations]
