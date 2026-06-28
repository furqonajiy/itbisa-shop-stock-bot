"""Tiered price runner for /harga_set."""

from __future__ import annotations

import time

from src import config, shopee_auth, shopee_client, telegram_sender, tiktokshop_client
from src.stock_allocator import shopee_min_buy_units

_WHOLESALE_TOP_MAX = 999999
_TIKTOKSHOP_TIER_SCALE_NUMERATOR = 2
_TIKTOKSHOP_TIER_SCALE_DENOMINATOR = 5


def parse_tiers(tokens: list[str]) -> list[tuple[int, int]]:
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
    chosen: int | None = None
    for start, price in tiers:
        if start <= qty:
            chosen = price
        else:
            break
    return chosen


def tiktokshop_tier_start_qty(shopee_tier_start_qty: int) -> int:
    numerator = int(shopee_tier_start_qty) * _TIKTOKSHOP_TIER_SCALE_NUMERATOR
    scaled = (numerator + _TIKTOKSHOP_TIER_SCALE_DENOMINATOR - 1) // _TIKTOKSHOP_TIER_SCALE_DENOMINATOR
    return max(scaled, 1)


def unit_price_for_tiktokshop_pack(tiers: list[tuple[int, int]], multiplier: int) -> int | None:
    scaled_tiers = [(tiktokshop_tier_start_qty(start), price) for start, price in tiers]
    return unit_price_for_quantity(scaled_tiers, multiplier)


def charm_round_up_to_nines(price: int) -> int:
    price = int(price)
    if price < 100:
        return price
    unit = 10 ** min(len(str(price)) - 2, 3)
    return (price // unit) * unit + (unit - 1)


def compute_shopee_pricing(
        tiers: list[tuple[int, int]],
) -> tuple[int, list[tuple[int, int, int]]]:
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
        msg = f"Otorisasi Shopee kadaluarsa. Perlu otorisasi ulang. ({e})"
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg, mode="Harga")
        return 1
    except Exception as e:
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
        unit_price = unit_price_for_tiktokshop_pack(tiers, mult)
        if unit_price is None:
            skipped.append(v)
            print(f"  ⏭️ {v['raw_sku']} (×{mult}): di bawah tier terendah; dilewati.")
            continue
        raw_price = unit_price * mult
        variant_price = charm_round_up_to_nines(raw_price)
        priced.append({
            "raw_sku": v["raw_sku"],
            "multiplier": mult,
            "unit_price": unit_price,
            "variant_price": variant_price,
            "product_id": v["product_id"],
            "sku_id": v["sku_id"],
        })
        charm_note = "" if variant_price == raw_price else f" (dari Rp{raw_price:,})".replace(",", ".")
        print(
            f"  • {v['raw_sku']} (×{mult}): Rp{unit_price:,}/pcs → "
            f"Rp{variant_price:,}{charm_note}".replace(",", ".")
        )

    if not priced:
        return {"priced": [], "skipped": skipped, "status": "⏭️ tidak ada varian cocok"}

    status = "🔍 dry-run" if dry_run else _push_tiktok_prices(priced)
    return {"priced": priced, "skipped": skipped, "status": status}


def _push_tiktok_prices(priced: list[dict]) -> str:
    by_product: dict[str, list[tuple[str, int]]] = {}
    for p in priced:
        by_product.setdefault(p["product_id"], []).append(
            (p["sku_id"], p["variant_price"])
        )

    for product_id, sku_prices in by_product.items():
        try:
            tiktokshop_client.update_price_batch(product_id, sku_prices)
        except Exception as e:
            return f"❌ gagal: product {product_id}: {e}"
    return "✅ berhasil"


def _run_shopee_harga(
        tiers: list[tuple[int, int]],
        variants: list[dict],
        dry_run: bool,
) -> dict:
    print("-- Shopee (Harga Grosir) --")
    base_price, wholesale_tiers = compute_shopee_pricing(tiers)
    min_buy_units = shopee_min_buy_units(base_price, config.SHOPEE_MIN_BUY_IDR)

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
            "wholesale_applied": None,
            "min_buy_units": min_buy_units,
        }

    if dry_run:
        status, wholesale_applied = "🔍 dry-run", None
    else:
        status, wholesale_applied = _push_shopee_prices(ones, base_price, wholesale_tiers)
    return {
        "base_price": base_price,
        "wholesale_tiers": wholesale_tiers,
        "ones": [{"raw_sku": v["raw_sku"]} for v in ones],
        "skipped_packs": packs,
        "status": status,
        "wholesale_applied": wholesale_applied,
        "min_buy_units": min_buy_units,
    }


def _push_shopee_prices(
        ones: list[dict],
        base_price: int,
        wholesale_tiers: list[tuple[int, int, int]],
) -> tuple[str, bool | None]:
    wholesale_applied: bool | None = True if wholesale_tiers else None
    for v in ones:
        try:
            shopee_client.update_price(v["item_id"], v["model_id"], base_price)
        except Exception as e:
            return f"❌ gagal set harga dasar: {v['raw_sku']}: {e}", False
        if wholesale_tiers:
            try:
                shopee_client.set_wholesale(v["item_id"], wholesale_tiers)
            except Exception as e:
                wholesale_applied = False
                print(f"  [shopee] Harga Grosir tidak diterapkan ({v['raw_sku']}): {e}")
        time.sleep(config.DELAY_BETWEEN_CALLS_SECONDS)
    if wholesale_tiers and not wholesale_applied:
        return "✅ harga dasar di-set — ⚠️ Harga Grosir set manual di Seller Center", False
    return "✅ berhasil", wholesale_applied
