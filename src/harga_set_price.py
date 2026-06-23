"""
harga_set_price.py
------------------
/harga_set runner — set tiered ("Harga Grosir"-style) prices.

**TikTok Shop only for now.** Shopee "Harga Grosir" support is added in a later
change; this module prices the TikTok Shop pack-size variants and reports that
Shopee was not touched.

Tier model
----------
A tier list is `[(start_qty, unit_price_idr), ...]`, ascending by `start_qty`,
covering `1..∞`. For a TikTok Shop pack-size variant with multiplier `M`, the
unit price is the tier whose `start_qty` is the largest value `<= M`; the
variant's listing price is `unit_price * M` (the variant is sold as a pack of
`M` pieces).

Example tiers `1=749, 50=739, 100=699`:
  - 1PCS    (M=1)    -> tier 1   -> Rp749    listing 749
  - 10PCS   (M=10)   -> tier 1   -> Rp749    listing 7.490
  - 50PCS   (M=50)   -> tier 50  -> Rp739    listing 36.950
  - 100PCS  (M=100)  -> tier 100 -> Rp699    listing 69.900
  - 1000PCS (M=1000) -> tier 100 -> Rp699    listing 699.000

A variant whose multiplier is below the lowest tier `start_qty` cannot be banded
and is skipped (reported), per the best-effort rule.
"""

from __future__ import annotations

from src import telegram_sender, tiktokshop_client


def parse_tiers(tokens: list[str]) -> list[tuple[int, int]]:
    """Parse `[q1, p1, q2, p2, ...]` into sorted `(start_qty, unit_price)` tiers.

    Pure. Raises ValueError (Bahasa Indonesia message) on malformed input:
    odd token count, non-integer values, qty < 1, negative price, or a
    duplicate start quantity.
    """
    if not tokens or len(tokens) % 2 != 0:
        raise ValueError("Tier harus berpasangan: JUMLAH HARGA [JUMLAH HARGA ...].")

    tiers: list[tuple[int, int]] = []
    for i in range(0, len(tokens), 2):
        qty_raw, price_raw = tokens[i], tokens[i + 1]
        try:
            qty = int(str(qty_raw))
            price = int(str(price_raw))
        except (TypeError, ValueError):
            raise ValueError(
                f"JUMLAH dan HARGA harus bilangan bulat: `{qty_raw} {price_raw}`."
            )
        if qty < 1:
            raise ValueError(f"JUMLAH minimal 1, dapat: {qty}.")
        if price < 0:
            raise ValueError(f"HARGA tidak boleh negatif, dapat: {price}.")
        tiers.append((qty, price))

    tiers.sort(key=lambda t: t[0])
    starts = [t[0] for t in tiers]
    if len(set(starts)) != len(starts):
        raise ValueError("JUMLAH awal tiap tier tidak boleh sama (duplikat).")
    return tiers


def unit_price_for_quantity(tiers: list[tuple[int, int]], qty: int) -> int | None:
    """Unit price for `qty`: the tier with the largest `start_qty <= qty`.

    Returns None when `qty` is below the lowest tier start. `tiers` must be
    sorted ascending by start_qty (as returned by `parse_tiers`). Pure.
    """
    chosen: int | None = None
    for start, price in tiers:
        if start <= qty:
            chosen = price
        else:
            break
    return chosen


def _format_tiers(tiers: list[tuple[int, int]]) -> str:
    return ", ".join(f"{qty}=Rp{price:,}".replace(",", ".") for qty, price in tiers)


def run_harga_set(base_sku: str, tier_tokens: list[str], dry_run: bool) -> int:
    """Set tiered prices for one base SKU on TikTok Shop.

    Returns a process exit code: 0 ok/dry-run, 1 not found / write failure,
    2 invalid tier input.
    """
    print("=" * 70)
    print(f"ITBisa Shop Stock Bot — Harga mode {'(DRY RUN)' if dry_run else ''}")
    print("=" * 70)
    print(f"SKU: {base_sku}")

    try:
        tiers = parse_tiers(tier_tokens)
    except ValueError as e:
        print(f"✗ {e}")
        telegram_sender.send_alert(f"Format tier tidak valid: {e}", mode="Harga")
        return 2

    print(f"Tier: {_format_tiers(tiers)}")
    print()

    try:
        print("Walking TikTok Shop catalog...")
        tiktokshop_catalog = tiktokshop_client.fetch_catalog()
    except Exception as e:  # noqa: BLE001 - surface any walk/auth failure to Telegram
        msg = f"Gagal membaca katalog TikTok Shop: {e}"
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg, mode="Harga")
        return 1

    variants = tiktokshop_catalog.get(base_sku)
    if not variants:
        msg = f"SKU `{base_sku}` tidak ditemukan di TikTok Shop."
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg, mode="Harga")
        return 1

    priced: list[dict] = []
    skipped: list[dict] = []
    for v in variants:
        mult = int(v["multiplier"])
        unit_price = unit_price_for_quantity(tiers, mult)
        if unit_price is None:
            skipped.append(v)
            print(
                f"  ⏭️ {v['raw_sku']} (×{mult}): pack size di bawah tier terendah; dilewati."
            )
            continue
        variant_price = unit_price * mult
        priced.append({
            "raw_sku": v["raw_sku"],
            "multiplier": mult,
            "unit_price": unit_price,
            "variant_price": variant_price,
            "product_id": v["product_id"],
            "sku_id": v["sku_id"],
        })
        print(
            f"  • {v['raw_sku']} (×{mult}): Rp{unit_price:,}/pcs → "
            f"Rp{variant_price:,}".replace(",", ".")
        )

    if not priced:
        msg = (
            f"SKU `{base_sku}`: tidak ada varian TikTok Shop yang bisa dihargai "
            f"dengan tier tsb (semua pack size di bawah tier terendah)."
        )
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg, mode="Harga")
        return 1

    status = "🔍 dry-run" if dry_run else _push_prices(priced)

    telegram_sender.send_harga_set_summary({
        "base_sku": base_sku,
        "tiers": tiers,
        "priced": priced,
        "skipped": skipped,
        "status": status,
        "dry_run": dry_run,
    })

    return 0 if ("❌" not in status) else 1


def _push_prices(priced: list[dict]) -> str:
    """Group priced variants by product_id and push via update_price_batch.

    Returns a status string (`✅ berhasil` or `❌ gagal: ...`).
    """
    by_product: dict[str, list[tuple[str, int]]] = {}
    for p in priced:
        by_product.setdefault(p["product_id"], []).append(
            (p["sku_id"], p["variant_price"])
        )

    for product_id, sku_prices in by_product.items():
        try:
            tiktokshop_client.update_price_batch(product_id, sku_prices)
        except Exception as e:  # noqa: BLE001 - report platform write failure
            return f"❌ gagal: product {product_id}: {e}"
    return "✅ berhasil"
