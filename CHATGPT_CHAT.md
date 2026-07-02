# itbisa-shop-stock-bot — ChatGPT Chat guide

Condensed `CLAUDE.md` (≤ 8000 chars); that file is authoritative.

## What it is
Python bot that sets, reads, and rebalances Shopee + TikTok Shop stock per base SKU (one or many per run), plus tiered pricing, variant rebuild, weight set, catalog audit. **No cron — `workflow_dispatch` only** (Actions tab, Telegram Worker, order bots). GitHub Actions only.
Primary commands: `/stok_set`, `/stok_get`, `/stok_balance`, `/stok_low`, `/varian_set`, `/berat_set`, `/harga_set`; legacy `/stock_*`, `/variant_set`, `/weight_set` stay accepted as aliases (Worker + batch `COMMAND_ALIASES`). Internal names keep English.

## Stack & files (Python 3.11)
- `src/`: `main.py`, `config.py`, `stock_allocator.py` (allocation math), `shopee_client.py`, `tiktokshop_client.py`, `{shopee,tiktokshop}_auth.py`, `telegram_sender.py`, `excel_reader.py`; helpers `stock_set_price_rule`, `stock_balance_{price_rule,preserve,delta_summary}`, `stock_get_compact`, `shopee_detail_enrichment`, `harga_set_price`, `variant_set_tiktok`, `weight_set_tiktok`, `catalog_audit`, `low_stock{,_throttle}`.
- `scripts/`: `stock_{set,set_price,get,balance,low,debug}`, `harga_set`, `variant_set`, `weight_set`, `catalog_audit`, `command_batch`, `cleanup_branches`.
- Workflows: `set/get/balance/low/harga/variant/weight/audit/batch.yml`; `ci.yml` (pytest on PRs, no secrets, never `bot-state`). Tests: pure logic (`pytest -q`, incl. `test_command_batch.py`); no network/API.

## State / tokens
Files: `data/{shopee,tiktokshop}_tokens.json` + `data/low_stock_throttle.json`, committed to `bot-state` every run (tokens rotate even on read-only/dry-run). **Never create/require `data/processed_orders.json`.** `main` = source; `bot-state` = runtime only — never protect it, never commit live tokens to `main`.

## Golden rule + split
**Never lose stock** — every piece accounted for; TikTok overflow stacks on the largest variant uncapped, never discarded. Split (`split_with_shopee_min_reserve`): reserve `ceil(SHOPEE_RESERVE_IDR / Shopee 1PCS price)` to Shopee first, then **70:30** Shopee:TikTok Shop (`SHOPEE_SPLIT_PERCENT`). `/stok_set` SKU mode = `/stok_balance`; unknown price or reserve 0 → no reserve. Excel mode 50:50.

## Allocation — `src/stock_allocator.py` only
Absolute units per variant, never deltas; clients only fetch + write. **Shopee:** variants may be separate products — equal-share, remainder to the smallest-multiplier; NO TikTok cap. **TikTok Shop:** siblings under one product — fill smallest-first, cap `TIKTOKSHOP_MAX_UNITS_PER_VARIANT` each, leftover stacks on the largest variant (nothing drops); `TIKTOKSHOP_1PCS_RESERVE_BASE_SKUS` reserves 1PCS to 1 unit. `parse_sku()` uppercases `base_sku`; each variant keeps `raw_sku`. Operator gives base SKU only (packs `NPCS-<base>`); `XPCS-` rejected by CLIs.

## Constants — `src/config.py` (never env/Secrets)
Base URLs; `TIKTOKSHOP_MAX_UNITS_PER_VARIANT = 50`; `MAX_SKUS_PER_RUN = 500`; `DELAY_BETWEEN_CALLS_SECONDS = 1.0`; `SHOPEE_RESERVE_IDR = 200000`; `SHOPEE_SPLIT_PERCENT = 70`; `SHOPEE_MIN_BUY_IDR = 20000`; `LOW_STOCK_THRESHOLD = 50`; `LOW_STOCK_MIN_INTERVAL_HOURS = 24`.

## main.py entry points
`run_stock_set_multi({base_sku: pieces}, dry_run)`: one catalog walk, per-SKU loop, enforces `MAX_SKUS_PER_RUN` (`run_single_sku_mode` wraps). `run_stock_balance_multi` same (`_walk_balance_catalogs`; `run_stock_balance_mode` wraps). `_set_one_sku`/`_balance_one_sku`: no Telegram side effects, return result dicts (`status ∈ ok|dry_run|skipped|failed`).

## Per-SKU failure isolation (never abort)
**set:** missing on both → `skipped`; one platform → full total there (no split); write failure → `failed`. **balance:** missing on either / total 0 → `skipped`; write failure → `failed` (abort only if the walk fails). Continue; balance idempotent.

## CLI / command modes
- `stock_set.py`: single/multi (`--sku … --pieces …`, equal length), Excel (`stock_set.py stock.xlsx`; A=SKU, B=pieces). `stock_set_price.py` = price-aware SKU runner (production `set.yml`). `stock_get.py` read-only; `stock_balance.py --sku … [--dry-run]` dedupes/uppercases/rejects `XPCS-`. `stock_debug.py` diagnostic, not wired.
- `/stok_low` (`low.yml`): base SKUs with combined stock < 50 pcs; throttle 1×/24h.
- `/harga_set` (`harga.yml`, `harga_set_price.py`): `(JUMLAH HARGA)` tier pairs. TikTok pack variant = tier × pack size, charm-rounded UP to 9s (TikTok-only); Shopee 1PCS = base price + Harga Grosir, but the API can't write wholesale so `set_wholesale` reports "set manual".
- `/varian_set` (`variant.yml`, TikTok only, default dry_run): rebuild `Packing` variation to an exact set + `ITBISA-BUBBLE-WRAP` (stock 0) via **Edit Product PUT** 202309 (`category_version: "v2"`; shop-global `value_id`s own→donor→fresh; new variants may lag minutes).
- `/berat_set` (`weight.yml`, TikTok only, default dry_run): per-piece weight via the same Edit Product PUT; `sku_weight = per_pcs × multiplier`, INTEGER GRAM rounded up, floor 1 g; preserves variation/stock/price.
- `audit.yml` (read-only, `catalog_audit.py`): Excel (3 sheets) of un-standardized SKUs (Shopee ≥3 Harga Grosir layers; low-price 1PCS needs packs; min-buy targets) → artifact. `batch.yml` (`command_batch.py`): runs `/commands` (one/line) sequentially via subprocess to the CLIs; `COMMAND_ALIASES` maps legacy names. **Settle gate:** after non-dry `/varian_set`/`/berat_set` with a later same-SKU line, poll TikTok Shop (20 s, ≤6 min) until packs appear and search sku_ids == detail (full-replace edit from a stale detail wipes pending variants; stale sku_ids get dead stock writes); timeout aborts + Telegram alert. Neither wired.

## Clients
Weight: `202502` search omits `package_weight` → 0; `/stok_get` enriches via `fetch_product_detail` 202309 (best-effort). Shopee: from `fetch_catalog`.
- **Shopee:** `get_item_list`+`get_item_base_info`+`get_model_list`; `update_stock`/`update_price`/`set_wholesale` (verifies live `wholesales`; no working write endpoint)/`get_wholesale` → `/api/v2/product/*`. HMAC-SHA256 (`partner_id+path+timestamp+access_token+shop_id`).
- **TikTok Shop:** `/product/202502/products/search`; `update_stock_batch`→`202309 …/inventory/update`; `update_price_batch`→`202309 …/prices/update`; `fetch_product_detail_raw`/`fetch_categories` (v2)/`edit_product` (**PUT** 202309, POST 405s). Signed + `x-tts-access-token`; `shop_cipher` from `…/shops`.

## Telegram output (`src/telegram_sender.py`)
Legacy Markdown, single-space; `_send` caps 4000 chars. Labels `🟧 Shopee` / `🟦 TikTok Shop`. `/stok_set` & `/stok_balance`: 1 SKU → detailed (balance: before→after delta), 2+ → compact. `/stok_get` per-variant units+weight (+ Shopee Harga Grosir). `send_alert(text, mode)`. Bahasa Indonesia; never "TikTok"; use "stock".

## Workflows & safety
All `workflow_dispatch` only. Write paths (`stock-set`/`-balance`/`-harga`/`-variant`/`-weight`/`-batch`) `cancel-in-progress: false` (never cancel mid-write); read-only (`stock-get`/`-low`/`catalog-audit`) `true` + `timeout-minutes`. All checkout `main`, overlay `data/` from `bot-state`, Python 3.11, commit tokens back every run. Run > `MAX_SKUS_PER_RUN` → abort before any write, alert Telegram; report partial failures per SKU.

## Process standard
Author commits/PRs as `C - Furqon Aji Yudhistira <furqonajiy@gmail.com>`. **No AI references** anywhere — no Co-Authored-By, "Generated by", session links. Branch `feature/<desc>`; PR into `main`; merge commit (`--no-ff`); title ends `(#PR)`; docs + marker ride the same PR; CLI in PowerShell.

## Flag before changing
Allocation (Shopee equal-share / TikTok cap + 1PCS reserve), price-aware runners, Shopee reserve + 70:30 split, `parse_sku()` uppercasing, token rotation, `bot-state`, workflow concurrency, multi-SKU formats, command aliases (`COMMAND_ALIASES`), batch settle gate, result-dict shapes, harga/variant/weight, `202502`/`202309`, signing.
