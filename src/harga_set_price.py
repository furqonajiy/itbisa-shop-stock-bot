"""
harga_set_price.py
------------------
/harga_set runner — set tiered ("Harga Grosir"-style) prices on **both**
Shopee and TikTok Shop for one exact base SKU.

Tier model
----------
A tier list is `[(start_qty, unit_price_idr), ...]`, ascending by `start_qty`,
covering `1..∞`. For a given quantity `q`, the unit price is the tier whose
`start_qty` is the largest value `<= q`.

Example tiers `1=749, 50=739, 100=699` → 1–49=Rp749, 50–99=Rp739, 100+=Rp699.

TikTok Shop — per pack-size variant
------------------------------------
Each pack-size variant (multiplier `M`) is priced by the tier `M` bands into;
listing price = `unit_price × M` (1PCS→749, 50PCS→739×50, 1000PCS→699×1000) via
`tiktokshop_client.update_price_batch`. Variants below the lowest tier are
skipped + reported (best-effort).

Shopee — Harga Grosir on the 1PCS listing
-----------------------------------------
Shopee expresses bulk pricing as "Harga Grosir" (wholesale tiers) on a single
listing. The `multiplier == 1` Shopee listing gets:
  - base/normal price = the tier covering quantity 1 (e.g. 749), and
  - wholesale tiers for the bands starting at ≥ 2 (50–99=739, 100+=699), via
    `shopee_client.update_price` + `shopee_client.set_wholesale`.
Any Shopee pack-size products (multiplier > 1) are skipped + reported.
(Shopee wholesale endpoints are best-effort pending live verification.)
"""

from __future__ import annotations

import time

from src import config, shopee_auth, shopee_client, telegram_sender, tiktokshop_client

# Upper bound for Shopee's open-ended top wholesale band (e.g. "100+").
_WHOLESALE_TOP_MAX = 999999


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


def compute_shopee_pricing(
        tiers: list[tuple[int, int]],
) -> tuple[int, list[tuple[int, int, int]]]:
    """Map tiers to a Shopee 1PCS listing: `(base_price, wholesale_tiers)`.

    - `base_price` = unit price for quantity 1 (falls back to the lowest tier's
      price if no tier starts at 1).
    - `wholesale_tiers` = `[(min_count, max_count, unit_price)]` for tiers
      starting at ≥ 2, contiguous, the last band open to `_WHOLESALE_TOP_MAX`.

    Pure. e.g. `[(1,749),(50,739),(100,699)]` →
    `(749, [(50,99,739),(100,999999,699)])`.
    """
    base_price = unit_price_for_quantity(tiers, 1)
    if base_price is None:
        base_price = tiers[0][1]

    bulk = [t for t in tiers if t[0] >= 2]
    wholesale: list[tuple[int, int, int]] = []
    for i, (start, price) in enumerate(bulk):
        max_count = (bulk[i + 1][0] - 1) if i + 1 < len(bulk) else _WHOLESALE_TOP_MAX
        wholesale.append((start, max_count, price))
    return base_price, wholesale


def _format_tiers(tiers: list[tuple[int, int]]) -> str:
    return ", ".join(f"{qty}=Rp{price:,}".replace(",", ".") for qty, price in tiers)


def run_harga_set(base_sku: str, tier_tokens: list[str], dry_run: bool) -> int:
    """Set tiered prices for one base SKU on Shopee + TikTok Shop.

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
        print("Walking Shopee catalog...")
        shopee_catalog = shopee_client.fetch_catalog()
    except shopee_auth.RefreshTokenExpiredError as e:
        msg = (
            f"🔐 Otorisasi Shopee kadaluarsa. Mohon otorisasi ulang aplikasi "
            f"di Shopee Open Platform Console, lalu update file "
            f"data/shopee_tokens.json di branch bot-state. ({e})"
        )
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg, mode="Harga")
        return 1
    except Exception as e:  # noqa: BLE001 - surface any walk/auth failure to Telegram
        msg = f"Gagal membaca katalog: {e}"
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg, mode="Harga")
        return 1

    on_tiktok = base_sku in tiktokshop_catalog
    on_shopee = base_sku in shopee_catalog
    if not on_tiktok and not on_shopee:
        msg = f"SKU `{base_sku}` tidak ditemukan di Shopee maupun TikTok Shop."
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg, mode="Harga")
        return 1

    tiktok = (
        _run_tiktok_harga(tiers, tiktokshop_catalog[base_sku], dry_run)
        if on_tiktok else None
    )
    shopee = (
        _run_shopee_harga(tiers, shopee_catalog[base_sku], dry_run)
        if on_shopee else None
    )

    telegram_sender.send_harga_set_summary({
        "base_sku": base_sku,
        "tiers": tiers,
        "dry_run": dry_run,
        "tiktok": tiktok,
        "shopee": shopee,
    })

    statuses = [r["status"] for r in (tiktok, shopee) if r]
    return 0 if all("❌" not in s for s in statuses) else 1


# ----------------------------------------------------------------------
# TikTok Shop
# ----------------------------------------------------------------------
def _run_tiktok_harga(
        tiers: list[tuple[int, int]],
        variants: list[dict],
        dry_run: bool,
) -> dict:
    print("-- TikTok Shop --")
    priced: list[dict] = []
    skipped: list[dict] = []
    for v in variants:
        mult = int(v["multiplier"])
        unit_price = unit_price_for_quantity(tiers, mult)
        if unit_price is None:
            skipped.append(v)
            print(f"  ⏭️ {v['raw_sku']} (×{mult}): di bawah tier terendah; dilewati.")
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
        return {"priced": [], "skipped": skipped, "status": "⏭️ tidak ada varian cocok"}

    status = "🔍 dry-run" if dry_run else _push_tiktok_prices(priced)
    return {"priced": priced, "skipped": skipped, "status": status}


def _push_tiktok_prices(priced: list[dict]) -> str:
    """Group priced variants by product_id and push via update_price_batch."""
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


# ----------------------------------------------------------------------
# Shopee (Harga Grosir on the 1PCS listing)
# ----------------------------------------------------------------------
def _run_shopee_harga(
        tiers: list[tuple[int, int]],
        variants: list[dict],
        dry_run: bool,
) -> dict:
    print("-- Shopee (Harga Grosir) --")
    base_price, wholesale_tiers = compute_shopee_pricing(tiers)

    ones = [v for v in variants if int(v["multiplier"]) == 1]
    packs = [v for v in variants if int(v["multiplier"]) != 1]

    for v in packs:
        print(f"  ⏭️ {v['raw_sku']} (×{v['multiplier']}): produk pack-size Shopee; dilewati (Harga Grosir hanya di listing 1PCS).")

    print(f"  Harga dasar: Rp{base_price:,}".replace(",", "."))
    for mn, mx, price in wholesale_tiers:
        hi = "∞" if mx >= _WHOLESALE_TOP_MAX else str(mx)
        print(f"  Grosir {mn}–{hi}: Rp{price:,}".replace(",", "."))

    if not ones:
        return {
            "base_price": base_price,
            "wholesale_tiers": wholesale_tiers,
            "ones": [],
            "skipped_packs": packs,
            "status": "⏭️ tidak ada listing 1PCS di Shopee",
        }

    status = "🔍 dry-run" if dry_run else _push_shopee_prices(ones, base_price, wholesale_tiers)
    return {
        "base_price": base_price,
        "wholesale_tiers": wholesale_tiers,
        "ones": [{"raw_sku": v["raw_sku"]} for v in ones],
        "skipped_packs": packs,
        "status": status,
    }


def _push_shopee_prices(
        ones: list[dict],
        base_price: int,
        wholesale_tiers: list[tuple[int, int, int]],
) -> str:
    """Set base price + Harga Grosir wholesale tiers on each 1PCS listing."""
    for v in ones:
        try:
            shopee_client.update_price(v["item_id"], v["model_id"], base_price)
            shopee_client.set_wholesale(v["item_id"], wholesale_tiers)
        except Exception as e:  # noqa: BLE001 - report platform write failure
            return f"❌ gagal: {v['raw_sku']}: {e}"
        time.sleep(config.DELAY_BETWEEN_CALLS_SECONDS)
    return "✅ berhasil"
