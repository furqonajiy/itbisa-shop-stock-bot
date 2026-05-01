"""
stock_allocator.py
------------------
Pure-math allocation logic shared by Shopee and TikTok Shop.

Two responsibilities, no I/O:

  1. split_across_platforms(total_pieces)
     Splits one warehouse stock count between Shopee and TikTok 50:50.
     If total is odd, Shopee absorbs the +1 (operator decision).

  2. allocate_pack_sizes(pieces, variants)
     Distributes a piece count across N pack-size variants of the same
     base SKU. Returns whole units per variant such that the sum of
     (units * multiplier) is as close to `pieces` as possible.

This module deliberately has no API client imports — it is the one
piece of logic that is identical on both platforms, and we want it
unit-testable without network. Both `shopee_client.py` and
`tiktokshop_client.py` call into it with platform-specific variant
dicts; the only contract the allocator cares about is the
`multiplier` field on each variant.

Allocation algorithm (per platform, per base SKU):
  Given P pieces and N pack-size variants with multipliers m_1..m_N:
    1. share = P // N           # pieces per variant
    2. units_i = share // m_i   # whole units per pack size (rounded down)
    3. represented = sum(units_i * m_i)
    4. remainder = P - represented
    5. The smallest pack size absorbs (remainder // m_smallest) extra units.
       Anything below m_smallest cannot be allocated and is "lost".
       In practice this only happens when there is no 1-pc variant,
       which is rare.
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
    trivially representable. TikTok then runs its own pack-size
    rebalance on a clean even number.

    Returns (shopee_pieces, tiktok_pieces).

    Examples:
      split_across_platforms(10000) -> (5000, 5000)
      split_across_platforms(10001) -> (5001, 5000)
      split_across_platforms(0)     -> (0, 0)
      split_across_platforms(1)     -> (1, 0)
    """
    if total_pieces < 0:
        raise ValueError(f"total_pieces must be non-negative, got {total_pieces}")
    tiktok_pieces = total_pieces // 2
    shopee_pieces = total_pieces - tiktok_pieces
    return shopee_pieces, tiktok_pieces


def allocate_pack_sizes(
        pieces: int,
        variants: list[dict],
) -> list[tuple[dict, int]]:
    """
    Distributes `pieces` across pack-size variants of one base SKU.

    Args:
      pieces:   total physical pieces to allocate.
      variants: list of dicts, each with at least a 'multiplier' key.
                MUST be sorted ascending by multiplier (smallest first)
                so the remainder can be deposited onto variants[0].
                Variants may carry any other platform-specific fields
                (sku_id, item_id, model_id, warehouse_id, etc.); the
                allocator preserves them untouched.

    Returns:
      List of (variant_dict, units_to_set) tuples in the same order
      as the input variants. units_to_set is the absolute unit count
      to push to that variant (NOT a delta).

    Raises:
      ValueError if variants list is empty (caller should skip such SKUs).

    Worked example: pieces=5000, variants=[{m:1}, {m:20}, {m:500}]:
      share = 5000 // 3 = 1666 pcs/variant
        m=1:   1666 // 1   = 1666 units (= 1666 pcs)
        m=20:  1666 // 20  =   83 units (= 1660 pcs)
        m=500: 1666 // 500 =    3 units (= 1500 pcs)
      represented = 1666 + 1660 + 1500 = 4826
      remainder = 5000 - 4826 = 174
      174 // 1 = 174 extra units to smallest (m=1)
      Final: [(v0, 1840), (v1, 83), (v2, 3)]
      Verify: 1840*1 + 83*20 + 3*500 = 1840 + 1660 + 1500 = 5000 ✓
    """
    if not variants:
        raise ValueError("variants must contain at least one entry")
    if pieces < 0:
        raise ValueError(f"pieces must be non-negative, got {pieces}")

    # Sanity: the caller is supposed to sort, but a defensive sort here
    # protects against future caller bugs at near-zero cost.
    variants = sorted(variants, key=lambda v: v["multiplier"])

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