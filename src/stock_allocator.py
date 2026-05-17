"""
stock_allocator.py
------------------
Pure-math stock allocation logic shared by Shopee and TikTok Shop.

Two responsibilities, no I/O:

  1. split_across_platforms(total_pieces)
     Splits one warehouse stock count between Shopee and TikTok Shop 50:50.
     If total is odd, Shopee absorbs the +1 (operator decision).

  2. allocate_pack_sizes(pieces, variants, tiktokshop_unit_cap=None)
     Distributes a physical piece count across pack-size variants of the
     same base SKU. Returns whole units per variant such that the sum of
     (units * multiplier) is as close to `pieces` as possible.

     Two algorithms, switched by `tiktokshop_unit_cap`:

     - tiktokshop_unit_cap=None (Shopee): equal-share split. Each
       variant gets `pieces // N` pieces budgeted, rounded down to whole
       units, with any leftover absorbed by the smallest-multiplier
       variant. No per-variant cap.

     - tiktokshop_unit_cap=C (TikTok Shop): smallest-first fill with a
       per-variant cap of C units. Each variant receives up to C units,
       starting from the smallest multiplier. Any pieces remaining after
       every variant hits the cap stack onto the largest variant
       (intentionally over the cap so no pieces are dropped).

This module deliberately has no API client imports — it is the one
piece of logic that is identical on both platforms (parametrised by
the TikTok Shop unit cap), and we want it unit-testable without network.
Both `shopee_client.py` and `tiktokshop_client.py` call into it with
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

  TikTok Shop (per-variant unit cap = C):
    Sort variants ascending by multiplier.
      1. For each variant smallest -> largest, set units to
         min(C, remaining_pieces // multiplier).
      2. Subtract used pieces from remaining.
      3. If pieces still remain after every variant hits C, stack the
         leftover onto the largest variant (over the cap). Any residue
         below the largest multiplier falls back to the smallest variant.

    Worked example: P=3500, variants=[1, 10, 50, 200], C=400
      1PCS:   min(400, 3500 // 1)  = 400 units (= 400 pcs);  remaining 3100
      10PCS:  min(400, 3100 // 10) = 310 units (= 3100 pcs); remaining 0
      50PCS:  0 units
      200PCS: 0 units
      Verify: 400*1 + 310*10 = 3500 ✓

    Worked example with overflow: P=150000, variants=[1, 10, 50, 200], C=400
      1PCS:   400 units (= 400 pcs)
      10PCS:  400 units (= 4000 pcs)
      50PCS:  400 units (= 20000 pcs)
      200PCS: 400 units (= 80000 pcs)   ← cap, but 45600 pcs left
      Overflow: 200PCS += 228 units (= 45600 pcs)
      Final 200PCS = 628 units, total = 150000 ✓

    Why TikTok Shop differs from Shopee: TikTok Shop limits buyers to
    ~20 units per SKU per order. Spreading stock across pack-size
    variants (1PCS, 10PCS, 50PCS, ...) widens the range of single-order
    quantities a buyer can place. The cap stops any one variant from
    hoarding stock so every pack size carries something whenever total
    stock allows.
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

    The returned base_sku is uppercased so it matches the operator's
    uppercased input regardless of how the platform happened to store
    case. Sellers occasionally publish the same SKU with different case
    on Shopee vs TikTok Shop (e.g. "ITBISA-PCB-5X7" on Shopee but
    "ITBISA-PCB-5x7" on TikTok Shop); without this normalization the
    cross-platform catalog lookup misses the SKU on one side. Each
    variant's original raw_sku is preserved separately on the variant
    dict so per-variant Telegram lines still show the platform's stored
    case.

    A SKU matching "<digits>PCS-<base>" is recognised as a pack-size
    variant. Anything else (including malformed inputs and multiplier 0)
    is treated as a non-variant with multiplier 1 — the degenerate case
    where the allocator just sets the SKU's stock to the input value.

    Examples:
      parse_sku("ITBISA-IC-NE555P-DIP8")        -> ("ITBISA-IC-NE555P-DIP8", 1)
      parse_sku("25PCS-ITBISA-IC-NE555P-DIP8")  -> ("ITBISA-IC-NE555P-DIP8", 25)
      parse_sku("25PCS-ITBISA-PCB-5x7")         -> ("ITBISA-PCB-5X7", 25)
      parse_sku("0PCS-ITBISA-FOO")              -> ("0PCS-ITBISA-FOO", 1)  # safe fallback
    """
    match = PACK_SIZE_PATTERN.match(seller_sku)
    if match:
        multiplier = int(match.group(1))
        if multiplier > 0:
            return match.group(2).upper(), multiplier
    return seller_sku.upper(), 1


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
        tiktokshop_unit_cap: int | None = None,
) -> list[tuple[dict, int]]:
    """
    Distributes `pieces` across pack-size variants of one base SKU.

    Args:
      pieces:              total physical pieces to allocate.
      variants:            list of dicts, each with at least a 'multiplier'
                           key. Variants may carry any other platform-
                           specific fields (sku_id, item_id, model_id,
                           warehouse_id, etc.); the allocator preserves
                           them untouched.
      tiktokshop_unit_cap: if None, Shopee equal-share (no per-variant
                           cap). If set, TikTok Shop smallest-first fill
                           with each variant capped at this many units.
                           Overflow beyond Σ(cap × multiplier) stacks
                           onto the largest variant (over the cap).

    Returns:
      List of (variant_dict, units_to_set) tuples in ascending multiplier
      order. units_to_set is the absolute unit count to push to that
      variant (NOT a delta).

    Raises:
      ValueError if variants list is empty (caller should skip such SKUs)
      or if pieces / tiktokshop_unit_cap is negative.

    Worked example WITHOUT cap (Shopee), pieces=5000, variants=[m:1, m:20, m:500]:
      share = 5000 // 3 = 1666 pcs/variant
        m=1:   1666 // 1   = 1666 units
        m=20:  1666 // 20  =   83 units (= 1660 pcs)
        m=500: 1666 // 500 =    3 units (= 1500 pcs)
      represented = 4826; remainder = 174 → +174 units on m=1
      Final: [(v0, 1840), (v1, 83), (v2, 3)]
      Verify: 1840*1 + 83*20 + 3*500 = 5000 ✓

    Worked example WITH tiktokshop_unit_cap=400, pieces=3500,
    variants=[m:1, m:10, m:50, m:200]:
      m=1:   min(400, 3500//1)  = 400 units (= 400 pcs);  remaining 3100
      m=10:  min(400, 3100//10) = 310 units (= 3100 pcs); remaining 0
      m=50:  0 units
      m=200: 0 units
      Final: [(v0, 400), (v1, 310), (v2, 0), (v3, 0)]
      Verify: 400*1 + 310*10 = 3500 ✓
    """
    if not variants:
        raise ValueError("variants must contain at least one entry")
    if pieces < 0:
        raise ValueError(f"pieces must be non-negative, got {pieces}")
    if tiktokshop_unit_cap is not None and tiktokshop_unit_cap < 0:
        raise ValueError(
            f"tiktokshop_unit_cap must be non-negative, got {tiktokshop_unit_cap}"
        )

    # Defensive sort: caller is expected to sort, but a re-sort here is
    # near-free and prevents future caller bugs from corrupting output.
    variants = sorted(variants, key=lambda v: v["multiplier"])

    if tiktokshop_unit_cap is None:
        return _allocate_unconstrained(pieces, variants)
    return _allocate_tiktokshop_capped(pieces, variants, tiktokshop_unit_cap)


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


def _allocate_tiktokshop_capped(
        pieces: int,
        variants: list[dict],
        unit_cap: int,
) -> list[tuple[dict, int]]:
    """
    TikTok Shop path: smallest-first fill, each variant capped at
    unit_cap units. Leftover beyond Σ(cap × multiplier) stacks onto the
    largest variant (over the cap — intentional so no pieces are lost).

    Why this exists:
      TikTok Shop limits buyers to ~20 units per SKU per order. Spreading
      stock across pack-size variants (1PCS, 10PCS, 50PCS, ...) widens
      the range of single-order quantities a buyer can place. The cap
      keeps any one variant from hoarding stock so every pack size
      carries something whenever total stock allows.

    Example with pieces=3500, cap=400, variants=[1, 10, 50, 200]:
      1PCS:  400 units (= 400 pcs);  remaining 3100
      10PCS: 310 units (= 3100 pcs); remaining 0
      Verify: 400*1 + 310*10 = 3500 ✓
    """
    allocations: list[list] = [[v, 0] for v in variants]
    if pieces == 0:
        return [(v, u) for v, u in allocations]

    remaining = pieces

    # Smallest-first fill, capped at unit_cap units per variant.
    for allocation in allocations:
        if remaining <= 0:
            break
        variant = allocation[0]
        units = min(unit_cap, remaining // variant["multiplier"])
        allocation[1] = units
        remaining -= units * variant["multiplier"]

    # Overflow: every variant hit the cap but stock still remains.
    # Stack on the largest variant (intentionally over unit_cap so no
    # pieces are dropped). Any residue below the largest multiplier
    # falls back to the smallest variant.
    if remaining > 0:
        largest_alloc = allocations[-1]
        largest_mult = largest_alloc[0]["multiplier"]
        extra = remaining // largest_mult
        if extra > 0:
            largest_alloc[1] += extra
            remaining -= extra * largest_mult

        if remaining > 0:
            smallest_alloc = allocations[0]
            smallest_mult = smallest_alloc[0]["multiplier"]
            if smallest_mult < largest_mult:
                extra_small = remaining // smallest_mult
                if extra_small > 0:
                    smallest_alloc[1] += extra_small
                    remaining -= extra_small * smallest_mult

    return [(v, u) for v, u in allocations]