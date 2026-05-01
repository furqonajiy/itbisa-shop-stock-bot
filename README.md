# ITBisa Shop Stock Bot

Cross-platform stock setter for Shopee Indonesia and TikTok Shop
Indonesia. Replaces the two separate `scripts/update_inventory.py`
files that previously lived in `itbisa-shopee-order-bot` and
`itbisa-tiktokshop-order-bot`.

## What it does

You give it ONE total stock count per SKU (in physical pieces), and it:

1. Splits the count 50:50 between Shopee and TikTok Shop. Odd totals
   give the +1 to Shopee.
2. On each platform, discovers all pack-size variants of that base
   SKU (e.g. `ITBISA-IC-NE555P-DIP8`, `25PCS-ITBISA-IC-NE555P-DIP8`,
   `500PCS-ITBISA-IC-NE555P-DIP8`).
3. Allocates the platform's share across those variants:
    - **Shopee**: equal-share split, smallest absorbs remainder. No
      per-variant cap (Shopee variants live as separate products).
    - **TikTok Shop**: smallest-multiplier variant first up to
      `TIKTOKSHOP_MAX_UNITS_PER_VARIANT` units (default **200**),
      then any leftover pieces shift to the next-smallest variant
      up to the cap, and so on.
4. Pushes the result via each platform's stock-update API.
5. Sends a single Bahasa Indonesia summary to Telegram.

### Why the per-variant cap on TikTok?

On TikTok Shop, every pack-size variant of a base SKU lives under
**one product**. Buyers see all variants on the same product page.
Capping each variant at 200 units means:

- The smallest pack always shows fresh, low-three-digit inventory on
  the storefront — exactly what buyers pick first.
- Surplus stock automatically flows to bigger pack sizes (where it
  reads as "wholesale" pricing) without manual rebalancing.
- The displayed stock numbers are warehouse-realistic, not silly
  4-digit values that customers find suspicious.

Shopee doesn't need this because pack-size variants are separate
products there — buyers landing on a `25PCS-...` product never see
the `1PCS` product unless they go look for it.

### Worked example: 10,000 × ITBISA-IC-NE555P-DIP8

```