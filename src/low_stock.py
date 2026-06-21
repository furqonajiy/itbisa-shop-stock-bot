"""
low_stock.py
------------
/stock_low — report every base SKU whose combined on-hand stock (Shopee +
TikTok Shop, in pieces) is below a threshold (config.LOW_STOCK_THRESHOLD,
default 50). Read-only; the only state change is token rotation (committed to
bot-state by the workflow) plus the throttle timestamp.

Throttled to once per config.LOW_STOCK_MIN_INTERVAL_HOURS (default 24h): a
trigger inside the window skips the catalog scan and replies that the report
was already generated.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src import config, low_stock_throttle, shopee_auth, telegram_sender


def _total_pieces(variants: list[dict]) -> int:
    """Combined pieces across a base SKU's variants (units × pack multiplier)."""
    return sum(v["stock_units"] * v["multiplier"] for v in variants)


def find_low_stock(
        shopee_catalog: dict,
        tiktokshop_catalog: dict,
        threshold: int,
) -> list[dict]:
    """Pure scan: every base SKU (union of both catalogs) whose combined on-hand
    pieces are strictly below `threshold`, sorted ascending by total then SKU.

    Each item: {base_sku, total, shopee, tiktokshop}. `total` is the combined
    on-hand the bot would split 50:50; that is the reorder-relevant quantity.
    """
    items: list[dict] = []
    for base_sku in set(shopee_catalog) | set(tiktokshop_catalog):
        shopee = _total_pieces(shopee_catalog.get(base_sku, []))
        tiktokshop = _total_pieces(tiktokshop_catalog.get(base_sku, []))
        total = shopee + tiktokshop
        if total < threshold:
            items.append({
                "base_sku": base_sku,
                "total": total,
                "shopee": shopee,
                "tiktokshop": tiktokshop,
            })
    items.sort(key=lambda x: (x["total"], x["base_sku"]))
    return items


def run_stock_low_mode(threshold: int | None = None) -> int:
    """Generate the low-stock report, throttled to once per window."""
    if threshold is None:
        threshold = config.LOW_STOCK_THRESHOLD

    print("=" * 70)
    print(f"ITBisa Shop Stock Bot — Low-stock report (< {threshold} pcs)")
    print("=" * 70)

    state = low_stock_throttle.load()
    if not low_stock_throttle.window_open(state):
        print(
            f"Throttled: last report at {state.get('last_run_at')}; "
            f"within {config.LOW_STOCK_MIN_INTERVAL_HOURS}h window. Skipping scan."
        )
        telegram_sender.send_low_stock_skipped(state.get("last_run_at"))
        return 0

    # Deferred import: src.main pulls the platform clients + Excel reader; keep
    # find_low_stock / the throttle importable without that heavy chain.
    from src.main import _walk_balance_catalogs

    try:
        shopee_catalog, tiktokshop_catalog = _walk_balance_catalogs()
    except shopee_auth.RefreshTokenExpiredError as e:
        msg = (
            f"🔐 Otorisasi Shopee kadaluarsa. Mohon otorisasi ulang aplikasi "
            f"di Shopee Open Platform Console, lalu update file "
            f"data/shopee_tokens.json di branch bot-state. ({e})"
        )
        print(f"✗ {msg}")
        telegram_sender.send_alert(msg, mode="Low Stock")
        return 1

    items = find_low_stock(shopee_catalog, tiktokshop_catalog, threshold)
    print(f"Found {len(items)} base SKU(s) below {threshold} pcs.")

    telegram_sender.send_low_stock_summary(items, threshold)

    # Record the run only after a successful scan + send, so a failed scan can
    # be retried within the window.
    low_stock_throttle.save({"last_run_at": datetime.now(timezone.utc).isoformat()})
    return 0
