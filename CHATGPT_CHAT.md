# itbisa-shop-stock-bot — ChatGPT Chat guide

Condensed `CLAUDE.md` (≤ 8000 chars); `CLAUDE.md` is the source of truth.

## What it is
Python bot that sets, reads, and rebalances Shopee + TikTok Shop stock from one base SKU (or many per run), plus tiered pricing, variant rebuild, weight set, and a catalog audit. **No cron — `workflow_dispatch` only** (Actions tab, Telegram Worker, or order bots at end-of-run). GitHub Actions only.

## Stack & files (Python 3.11)
- `src/`: `main.py`, `config.py`, `stock_allocator.py` (allocation math), `shopee_client.py`, `tiktokshop_client.py`, `{shopee,tiktokshop}_auth.py`, `telegram_sender.py`, `excel_reader.py`; helpers `stock_set_price_rule.py`, `stock_balance_{price_rule,preserve,delta_summary}.py`, `stock_get_compact.py`, `shopee_detail_enrichment.py`, `harga_set_price.py`, `variant_set_tiktok.py` (+weight helpers), `weight_set_tiktok.py`, `catalog_audit.py`, `low_stock{,_throttle}.py`.
- `scripts/`: `stock_{set,set_price,get,balance,low,debug}`, `harga_set`, `variant_set`, `weight_set`, `catalog_audit`, `command_batch`, `cleanup_branches`.
- Workflows: `set/get/balance/low/harga/variant/weight/audit/batch.yml`; `ci.yml` (pytest on PRs, no secrets, never `bot-state`). Tests: pure logic only (`pytest -q`); no network/API tested.

## State / tokens
Files: `data/{shopee,tiktokshop}_tokens.json` + `data/low_stock_throttle.json`, committed to `bot-state` every run (tokens rotate even on read-only/dry-run). **Never create or require `data/processed_orders.json`.** `main` = source; `bot-state` = runtime files only — never protect it, never commit live tokens to `main`.

## Golden rule + split
**Never lose stock** — every piece accounted for; TikTok overflow stacks on the largest pack-size variant uncapped, never discarded. Split (`split_with_shopee_min_reserve`): reserve `ceil(SHOPEE_RESERVE_IDR / Shopee 1PCS price)` to Shopee first (Rp200.000 → ~200 units), then split the remainder **70:30** Shopee:TikTok Shop (`SHOPEE_SPLIT_PERCENT`). `/stock_set` SKU mode = `/stock_balance`; unknown price / reserve 0 → no reserve. Excel mode plain 50:50.

## Allocation — `src/stock_allocator.py` only
Absolute units per variant, never deltas; clients fetch + write only. **Shopee:** variants may be separate products — equal-share across pack-size variants, remainder to the smallest-multiplier; do NOT apply the TikTok cap. **TikTok Shop:** siblings under one product — fill smallest-first, cap each at `TIKTOKSHOP_MAX_UNITS_PER_VARIANT`, leftover stacks on the largest variant (over cap, nothing drops); exception `TIKTOKSHOP_1PCS_RESERVE_BASE_SKUS` reserves 1PCS to 1 unit, balances the rest. `parse_sku()` uppercases `base_sku` (one dict key), each variant keeps `raw_sku`. Operator gives base SKU only (pack variants `<digits>PCS-<base_sku>`); `XPCS-` rejected by CLIs, the allocator parses it from catalogs.

## Constants — `src/config.py` (never env/Secrets)
Shopee/TikTok base URLs; `TIKTOKSHOP_MAX_UNITS_PER_VARIANT = 50`; `MAX_SKUS_PER_RUN = 500`; `DELAY_BETWEEN_CALLS_SECONDS = 1.0`; `SHOPEE_RESERVE_IDR = 200000`; `SHOPEE_SPLIT_PERCENT = 70`; `SHOPEE_MIN_BUY_IDR = 20000`; `LOW_STOCK_THRESHOLD = 50`; `LOW_STOCK_MIN_INTERVAL_HOURS = 24`.

## main.py entry points
`run_stock_set_multi({base_sku: pieces}, dry_run)` walks both catalogs ONCE, loops per SKU, enforces `MAX_SKUS_PER_RUN` (`run_single_sku_mode` wraps it). `run_stock_balance_multi(base_skus, dry_run)` shares one walk (`_walk_balance_catalogs()`; `run_stock_balance_mode` wraps it). `_set_one_sku`/`_balance_one_sku`: per-SKU helpers, no Telegram side effects, return a result dict (`status ∈ ok|dry_run|skipped|failed`).

## Per-SKU failure isolation (never abort the batch)
**set:** missing on both → `skipped`; on one platform only → full total there (no split); write failure → `failed`. **balance:** missing on either / total 0 → `skipped`; write failure → `failed` (abort only if the walk fails). Continue on each; balance idempotent.

## CLI / command modes
- `stock_set.py`: single/multi (`--sku … --pieces …`, equal length), Excel (`stock_set.py stock.xlsx`; A=SKU, B=pieces). `stock_set_price.py` = price-aware SKU runner (production `set.yml`). `stock_get.py` read-only; `stock_balance.py --sku … [--dry-run]` dedupes/uppercases/rejects `XPCS-`. `stock_debug.py` diagnostic-only, not wired.
- `/stock_low` (`low.yml`): base SKUs with combined stock < 50 pcs; throttled 1×/24h.
- `/harga_set` (`harga.yml`, `harga_set_price.py`): `(JUMLAH HARGA)` tier pairs. TikTok pack variant = tier × pack size, charm-rounded UP to 9s (TikTok-only); Shopee 1PCS = base price + Harga Grosir, but the Open API can't write wholesale so `set_wholesale` reports "set manual".
- `/variant_set` (`variant.yml`, TikTok only, default dry_run): rebuild `Packing` variation to an exact set + always-present `ITBISA-BUBBLE-WRAP` (stock 0) via **Edit Product PUT** 202309 (needs `category_version: "v2"`; resolves shop-global `value_id`s own→donor→fresh; new variants may lag minutes).
- `/weight_set` (`weight.yml`, TikTok only, default dry_run): per-piece weight via the same Edit Product PUT; `sku_weight = per_pcs × multiplier`, INTEGER GRAM rounded up, floored 1 g; preserves variation/stock/price.
- `audit.yml` (read-only, `catalog_audit.py`): Excel report (3 sheets) of un-standardized base SKUs (Shopee ≥3 Harga Grosir layers; TikTok low-price 1PCS needs pack variants; min-buy targets) → `catalog-audit` artifact. `batch.yml` (`command_batch.py`): runs `/commands` (one/line) sequentially via subprocess to the CLIs. Neither wired.

## Clients
Weight: `202502` search omits `package_weight` → 0; `/stock_get` enriches via `fetch_product_detail` 202309 (best-effort). Shopee weight from `fetch_catalog`.
- **Shopee:** `get_item_list`+`get_item_base_info`+`get_model_list`; `update_stock`/`update_price`/`set_wholesale` (verifies live `wholesales`, no working write endpoint)/`get_wholesale` → `/api/v2/product/*`. Shop-level HMAC-SHA256 (`partner_id+path+timestamp+access_token+shop_id`).
- **TikTok Shop:** `/product/202502/products/search`; `update_stock_batch`→`202309 …/inventory/update`; `update_price_batch`→`202309 …/prices/update`; `fetch_product_detail_raw`/`fetch_categories` (v2)/`edit_product` (**PUT** 202309, POST 405s). Signed + `x-tts-access-token`; `shop_cipher` from `…/shops`.

## Telegram output (`src/telegram_sender.py`)
Legacy Markdown, single-space; `_send` caps at 4000 chars. Labels `🟧 Shopee` / `🟦 TikTok Shop`. `/stock_set` & `/stock_balance`: 1 SKU → detailed (balance shows before→after delta), 2+ → compact. `/stock_get` per-variant units+weight (Shopee detail also shows Harga Grosir). Per-mode helpers + `send_alert(text, mode)`. Bahasa Indonesia; never "TikTok"; use "stock".

## Workflows & safety
All `workflow_dispatch` only. Write paths (`stock-set`/`-balance`/`-harga`/`-variant`/`-weight`/`-batch`) `cancel-in-progress: false` (never cancel mid-write); read-only (`stock-get`/`-low`/`catalog-audit`) `true` + `timeout-minutes`. SKU set mode runs `stock_set_price.py`. All checkout `main`, overlay `data/` from `bot-state`, Python 3.11, commit token files to `bot-state` every run. Run > `MAX_SKUS_PER_RUN` → abort before any write, alert Telegram; report partial failures per SKU.

## Process standard
Author commits/PRs as `C - Furqon Aji Yudhistira <furqonajiy@gmail.com>`. **No AI references** anywhere (branches, messages, PR text, code, comments) — no Co-Authored-By, "Generated by", session links. Branch `feature/<desc>` off `main`; PR into `main`; merge commit (`--no-ff`); title ends `(#PR)`; docs + marker in the same PR; CLI in PowerShell.

## Flag before changing
Allocation (Shopee equal-share / TikTok cap + 1PCS reserve), price-aware runners, Shopee reserve + 70:30 split, `parse_sku()` uppercasing, token rotation, `bot-state`, workflow concurrency, multi-SKU formats, result-dict shapes, harga/variant/weight, `202502`/`202309`, signing.
