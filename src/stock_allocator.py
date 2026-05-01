"""
stock_allocator.py
------------------
Pure-math allocation logic shared by Shopee and TikTok Shop.

Two responsibilities, no I/O:

  1. split_across_platforms(total_pieces)
     Splits one warehouse stock count between Shopee and TikTok 50:50.
     If total is odd, Shopee absorbs the +1 (operator decision).

  2. allocate_pack_sizes(pieces, variants, max_units_per_variant=None)
     Distributes a piece count across N pack-size variants of the same
     base SKU. Returns whole units per variant such that the sum of
     (units * multiplier) is as close to `pieces` as possible.

     Two algorithms, switched by `max_units_per_variant`:

     - max_units_per_variant=None (Shopee): equal-share split. Each
       variant gets `pieces // N` pieces budgeted, rounded down to whole
       units, with any leftover absorbed by the smallest-multiplier
       variant. No per-variant cap.

     - max_units_per_variant=K (TikTok): smallest-first cap. The
       smallest variant gets up to K units, leftover pieces flow to
       the next-smallest variant up to K units, and so on. Used because
       on TikTok every pack-size variant lives under ONE product and
       the warehouse wants no single SKU to display more than K units
       in stock.

This module deliberately has no API client imports — it is the one
piece of logic that is identical on both platforms (parametrised by
the cap), and we want it unit-testable without network. Both
`shopee_client.py` and `tiktokshop_client.py` call into it with
platform-specific variant dicts; the only contract the allocator cares
about is the `multiplier` field on each variant.

Allocation algorithms (per platform, per base SKU):

  Shopee (no cap):
    Given P pieces and N pack-size variants with multipliers m_1..m_N:
      1. share = P // N           # pieces per variant
      2. units_i = share // m_i   # whole units per pack size (rounded down)
      3. represented = sum(units_i * m_i)
      4. remainder = P - represented
      5. The smallest pack size absorbs (remainder // m_smallest) extra units.
         Anything below m_smallest cannot be allocated and is "lost".

  TikTok (cap = K units per variant):
    Sort variants ascending by multiplier. remaining = P.
    For each variant from smallest to largest:
      units_i = min(K, remaining // m_i)
      remaining -= units_i * m_i

    Worked example: P=5000, variants=[(m=1), (m=100)], K=200
      m=1:   min(200, 5000 // 1)   = 200 units (= 200 pcs); remaining=4800
      m=100: min(200, 4800 // 100) =  48 units (= 4800 pcs); remaining=0
      Verify: 200*1 + 48*100 = 5000 ✓

    If the run cannot be fully represented (e.g., very large totals on
    a SKU with only big pack sizes), the leftover is reported as
    "unrepresentable" — same convention as the Shopee path.
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
        max_units_per_variant: int | None = None,
) -> list[tuple[dict, int]]:
    """
    Distributes `pieces` across pack-size variants of one base SKU.

    Args:
      pieces:                total physical pieces to allocate.
      variants:              list of dicts, each with at least a
                             'multiplier' key. MUST be sorted ascending
                             by multiplier (smallest first). Variants
                             may carry any other platform-specific
                             fields (sku_id, item_id, model_id,
                             warehouse_id, etc.); the allocator
                             preserves them untouched.
      max_units_per_variant: if None, no cap (Shopee-style: equal share
                             with smallest absorbing remainder).
                             If set, each variant gets at most this
                             many units (TikTok-style: smallest first
                             up to cap, then move up).

    Returns:
      List of (variant_dict, units_to_set) tuples in the same order
      as the input variants. units_to_set is the absolute unit count
      to push to that variant (NOT a delta).

    Raises:
      ValueError if variants list is empty (caller should skip such SKUs)
      or if pieces / max_units_per_variant is negative.

    Worked example WITHOUT cap (Shopee), pieces=5000, variants=[m:1, m:20, m:500]:
      share = 5000 // 3 = 1666 pcs/variant
        m=1:   1666 // 1   = 1666 units
        m=20:  1666 // 20  =   83 units (= 1660 pcs)
        m=500: 1666 // 500 =    3 units (= 1500 pcs)
      represented = 4826; remainder = 174 → +174 units on m=1
      Final: [(v0, 1840), (v1, 83), (v2, 3)]
      Verify: 1840*1 + 83*20 + 3*500 = 5000 ✓

    Worked example WITH cap=200 (TikTok), pieces=5000, variants=[m:1, m:100]:
      remaining = 5000
        m=1:   units = min(200, 5000 // 1)   = 200; remaining = 4800
        m=100: units = min(200, 4800 // 100) =  48; remaining = 0
      Final: [(v0, 200), (v1, 48)]
      Verify: 200*1 + 48*100 = 5000 ✓
    """
    if not variants:
        raise ValueError("variants must contain at least one entry")
    if pieces < 0:
        raise ValueError(f"pieces must be non-negative, got {pieces}")
    if max_units_per_variant is not None and max_units_per_variant < 0:
        raise ValueError(
            f"max_units_per_variant must be non-negative, got {max_units_per_variant}"
        )

    # Defensive sort: caller is expected to sort, but a re-sort here is
    # near-free and prevents future caller bugs from corrupting output.
    variants = sorted(variants, key=lambda v: v["multiplier"])

    if max_units_per_variant is None:
        return _allocate_unconstrained(pieces, variants)
    return _allocate_capped(pieces, variants, max_units_per_variant)


def verify_allocation(pieces: int, allocations: Iterable[tuple[dict, int]]) -> int:
    """
    Returns pieces_lost = pieces - sum(units * multiplier).

    Called by the dry-run formatter so the operator sees when an
    allocation cannot fully represent the input (e.g., 3 leftover pieces
    on a product whose smallest variant is 20-pack, OR a TikTok run
    that exceeded the per-variant cap on every available variant).

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


def _allocate_capped(
        pieces: int,
        variants: list[dict],
        cap: int,
) -> list[tuple[dict, int]]:
    """
    TikTok path: smallest variant first up to `cap` units, then the
    next-smallest, and so on. The cap is in UNITS, not pieces.

    The smallest variant being capped first is the operator's stated
    preference: it ensures the smallest pack always shows non-zero
    inventory on the storefront (up to `cap`), which is what buyers
    pick first. Larger packs absorb the surplus.
    """
    allocations: list[list] = []
    remaining = pieces
    for variant in variants:
        units = min(cap, remaining // variant["multiplier"])
        allocations.append([variant, units])
        remaining -= units * variant["multiplier"]

    return [(v, u) for v, u in allocations]