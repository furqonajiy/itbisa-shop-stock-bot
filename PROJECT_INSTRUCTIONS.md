# Project Instructions — itbisa-shop-stock-bot

> Synced source for **Claude** and **ChatGPT** project instructions — paste this same text into both. Keep ≤ 8000 characters (ChatGPT limit). Update only on request.

## What this is
Python bot: set, read, and rebalance Shopee + TikTok Shop stock from one base SKU (or many in one run). **No cron — `workflow_dispatch` only.** Triggered manually, by the Telegram Worker, or by the order bots at end-of-run.

## Stack & files
- Python 3.11. Core: `src/main.py`, `src/config.py`, `src/stock_allocator.py` (allocation math), `src/shopee_client.py`, `src/tiktokshop_client.py`, `src/shopee_auth.py`, `src/tiktokshop_auth.py`, `src/telegram_sender.py`, `src/excel_reader.py`.
- Price-aware + summary helpers (see "Price-aware layer" below).
- CLIs: `scripts/stock_set.py`, `stock_set_price.py`, `stock_get.py`, `stock_balance.py`, `stock_debug.py`. Workflows: `.github/workflows/set.yml`, `get.yml`, `balance.yml`.

## State / tokens
- Token files ONLY: `data/shopee_tokens.json`, `data/tiktokshop_tokens.json`. Committed to `bot-state` after every run (tokens rotate even on read-only runs). **Never create/require `data/processed_orders.json` here.**

## Golden rule
**Never lose stock.** Every piece is accounted for. TikTok Shop overflow goes to the largest pack-size variant with no cap — never discarded.

## Split rule (50:50) — `split_across_platforms`
Input is total physical warehouse stock. Shopee share = `ceil(total/2)`; TikTok Shop = `floor(total/2)`; odd totals give Shopee +1. Same for `/stock_set` and `/stock_balance`.

## Allocation — lives in `src/stock_allocator.py` only
- Set absolute stock units per variant, never deltas. Clients fetch catalogs and write only — no allocation logic in clients.
- **Shopee:** variants may be separate products. Unconstrained/equal-share across discovered pack-size variants; remainder absorbed by the smallest-multiplier variant when representable. **Do NOT apply the TikTok Shop per-variant cap to Shopee.**
- **TikTok Shop:** variants are siblings under one product. Fill **smallest-first, capping each at `TIKTOKSHOP_MAX_UNITS_PER_VARIANT` units**; leftover stacks onto the largest-multiplier variant (intentionally over the cap so no pieces drop). Spreads across pack sizes (TikTok Shop limits ~20 units/SKU/order). **Exception:** for base SKUs in `TIKTOKSHOP_1PCS_RESERVE_BASE_SKUS`, the 1PCS variant is reserved to 1 unit and the rest balanced across the others (`_allocate_tiktokshop_with_1pcs_reserve`).
- `parse_sku()` **uppercases the returned `base_sku`** (collapses Shopee/TikTok case differences into one key). Each variant keeps its original `raw_sku` for display.

## SKU rules
Operator provides base SKU only. Pack-size variants: `<digits>PCS-<base_sku>` (e.g. `20PCS-ITBISA-LED-5MM`). Value-style variants live under one Shopee parent (`model_sku` carries the variant). `XPCS-` SKUs are rejected by the CLI (warning + skip); the allocator parses `XPCS-` variants from platform catalogs itself.

## Constants — `src/config.py` (NEVER env vars / Secrets)
- `SHOPEE_API_BASE_URL = https://partner.shopeemobile.com`
- `TIKTOKSHOP_AUTH_BASE_URL = https://auth.tiktok-shops.com`
- `TIKTOKSHOP_OPEN_API_BASE_URL = https://open-api.tiktokglobalshop.com`
- `TIKTOKSHOP_MAX_UNITS_PER_VARIANT = 400` (active per-variant unit cap)
- `MAX_SKUS_PER_RUN = 500`; `DELAY_BETWEEN_CALLS_SECONDS = 1.0`

## main.py entry points
- `run_stock_set_multi(desired, dry_run)` (`desired` = `dict[base_sku, total_pieces]`) and `run_stock_balance_multi(base_skus, dry_run)`: walk both catalogs ONCE, loop per SKU. `run_single_sku_mode` / `run_stock_balance_mode` are thin wrappers. `run_stock_set_multi` enforces `MAX_SKUS_PER_RUN`.
- `_set_one_sku` / `_balance_one_sku`: per-SKU helpers, **no Telegram side effects** — return a result dict, `status ∈ ok | dry_run | skipped | failed`. Reuse `_format_and_push_shopee` / `_format_and_push_tiktokshop`.
- `run_excel_mode(path, dry_run)` is independent — leave untouched by multi-SKU work.

## Per-SKU failure isolation (never abort the whole batch)
- **set:** missing on both / only one platform → `skipped`; write failure → `failed`. Continue.
- **balance:** missing on either platform / total pieces 0 → `skipped`; write failure → `failed`. Continue. Run aborts only if the catalog walk fails. Balance is idempotent.

## CLI modes
- `stock_set.py`: single (`--sku S --pieces N`), multi (`nargs='+'`, equal-length), Excel (`stock_set.py stock.xlsx`; A=base SKU, B=pieces).
- `stock_get.py --sku BASE_SKU` (read-only). `stock_balance.py --sku S [S …] [--dry-run]` (dedupes, uppercases, rejects `XPCS-`). `stock_debug.py` (diagnostic only, read-only, not wired to the Worker).
- `stock_set_price.py --sku … --pieces … [--dry-run]`: **price-aware** set runner — what production `set.yml` runs for SKU mode.

## Price-aware layer (TikTok Shop low-price 1PCS variants)
`stock_set_price_rule.py` / `stock_balance_price_rule.py` = set/balance orchestration for the low-price 1PCS rule. `stock_balance_preserve.py` preserves the grand total when the allocator can't fully represent input. `stock_balance_delta_summary.py` = compact before→after deltas. `stock_get_compact.py` / `shopee_detail_enrichment.py` = price/weight enrichment for Telegram.

## TikTok Shop weight enrichment (/stock_get only)
`202502` search omits `package_weight` (→ `weight_grams = 0`). `run_stock_get_mode` calls `fetch_product_detail(product_id)` (GET `/product/202309/products/{product_id}`) once per `product_id`, overwriting only `weight_grams == 0`. Best-effort (failure → 0). NOT called from `fetch_catalog`. Shopee weight comes from `fetch_catalog`.

## Clients
- **Shopee:** `get_item_list` + `get_item_base_info` + `get_model_list`; `update_stock` → `/api/v2/product/update_stock` (absolute). Shop-level HMAC-SHA256 signing.
- **TikTok Shop:** `/product/202502/products/search`; `update_stock_batch` → `/product/202309/products/{product_id}/inventory/update` (absolute). Signed + `x-tts-access-token`; `shop_cipher` from `/authorization/202309/shops`.

## Telegram (`src/telegram_sender.py`)
Markdown (legacy), `_send` caps at 4000 chars. Labels: `SHOPEE_LABEL = "🟧 Shopee"`, `TIKTOKSHOP_LABEL = "🟦 TikTok Shop"`. `/stock_set` & `/stock_balance`: 1 SKU → detailed (balance shows before→after signed delta), 2+ → compact. `/stock_get`: per-variant units + weight. `send_alert(text, mode)` → `🚨 *{mode}* — Error`.

## Workflows
All `workflow_dispatch` only. `set.yml` (`stock-set`, `cancel-in-progress: false`): SKU mode runs `stock_set_price.py`, Excel mode `stock_set.py`. `get.yml` (`stock-get`, `cancel-in-progress: true`). `balance.yml` (`stock-balance`, `cancel-in-progress: false`). All: checkout `main`, overlay `data/` from `bot-state`, Python 3.11, commit token files to `bot-state` after every run.

## Conventions
GitHub Actions only. `main` = source; `bot-state` = token files only (never protect). Never hardcode secrets. Self-contained, no shared library. Minimal targeted changes. Telegram strings Bahasa Indonesia; never abbreviate "TikTok Shop"; use "stock" not "inventory" (except endpoints like `/inventory/update`). Runtime ref `main`.

## Development workflow (process standard)
- Branch from `main` using `feature/<short-description>`. Always open a PR into `main` and **merge with a merge commit (`--no-ff`)** — never squash, never fast-forward. Commits/PRs authored as **`C - Furqon Aji Yudhistira <furqonajiy@gmail.com>`** (never "Claude").

## Flag before changing
Allocation (Shopee equal-share / TikTok per-variant cap + 1PCS-reserve exception), the price-aware `/stock_set` runner, the 50:50 split, `parse_sku()` uppercase, token rotation, `bot-state`, workflow concurrency, `/stock_set` `/stock_get` `/stock_balance` inputs, the multi-vs-single entry points, `_set_one_sku`/`_balance_one_sku` result shape, weight enrichment, `202502` vs `202309` usage, signing.
