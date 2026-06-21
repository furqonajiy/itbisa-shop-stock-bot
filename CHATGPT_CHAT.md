# itbisa-shop-stock-bot — ChatGPT Chat guide

Condensed `CLAUDE.md` for ChatGPT Chat (≤ 8000 chars). The repo's `CLAUDE.md` is the full source of truth.

## What it is
Python bot that sets, reads, and rebalances Shopee + TikTok Shop stock from one base SKU (or many per run). **No cron — `workflow_dispatch` only**, triggered from the Actions tab, by the Telegram Worker, or by the order bots at end-of-run. GitHub Actions only: no server, DB, or long-running process.

## Stack & files (Python 3.11)
- Core: `src/main.py`, `src/config.py`, `src/stock_allocator.py` (allocation math), `src/shopee_client.py`, `src/tiktokshop_client.py`, `src/shopee_auth.py`, `src/tiktokshop_auth.py`, `src/telegram_sender.py`, `src/excel_reader.py`.
- Price-aware + summary helpers: `src/stock_set_price_rule.py`, `src/stock_balance_price_rule.py`, `src/stock_balance_preserve.py`, `src/stock_balance_delta_summary.py`, `src/stock_get_compact.py`, `src/shopee_detail_enrichment.py`.
- CLIs: `scripts/stock_set.py`, `scripts/stock_set_price.py`, `scripts/stock_get.py`, `scripts/stock_balance.py`, `scripts/stock_debug.py`.
- Workflows: `.github/workflows/set.yml`, `get.yml`, `balance.yml`.

## State / tokens
Token files ONLY: `data/shopee_tokens.json`, `data/tiktokshop_tokens.json`, committed to `bot-state` after every run (tokens rotate even on read-only/dry-run). **Never create or require `data/processed_orders.json`** here. `main` = source; `bot-state` = runtime token files only — never protect it, never commit live tokens to `main`.

## Golden rule
**Never lose stock.** Every piece is accounted for. TikTok Shop overflow goes to the largest pack-size variant uncapped — never discarded.

## Split rule (50:50) — `split_across_platforms`
Input is total physical warehouse stock. Shopee = `ceil(total/2)`, TikTok Shop = `floor(total/2)` (odd totals give Shopee +1). Same for `/stock_set` and `/stock_balance`.

## Allocation — lives in `src/stock_allocator.py` only
- Set absolute units per variant, never deltas. Clients fetch catalogs and write only — no allocation logic in clients.
- **Shopee:** variants may be separate products. Unconstrained/equal-share across discovered pack-size variants; remainder absorbed by the smallest-multiplier variant when representable. Do NOT apply the TikTok Shop per-variant cap to Shopee.
- **TikTok Shop:** variants are siblings under one product. Fill smallest-first, capping each at `TIKTOKSHOP_MAX_UNITS_PER_VARIANT`; leftover stacks onto the largest-multiplier variant (intentionally over cap so nothing drops). Exception: base SKUs in `TIKTOKSHOP_1PCS_RESERVE_BASE_SKUS` reserve the 1PCS variant to 1 unit and balance the rest.
- `parse_sku()` uppercases `base_sku` (one dict key across platforms); each variant keeps its `raw_sku` for display.

## SKU rules
Operator provides base SKU only. Pack-size variants: `<digits>PCS-<base_sku>` (e.g. `20PCS-ITBISA-LED-5MM`). Value-style variants live under one Shopee parent; the line's `model_sku` carries the variant. `XPCS-` variant SKUs are rejected by the CLIs (warn + skip); the allocator parses `XPCS-` from platform catalogs itself.

## Constants — `src/config.py` (never env vars or Secrets)
`SHOPEE_API_BASE_URL = https://partner.shopeemobile.com`; `TIKTOKSHOP_AUTH_BASE_URL = https://auth.tiktok-shops.com`; `TIKTOKSHOP_OPEN_API_BASE_URL = https://open-api.tiktokglobalshop.com`; `TIKTOKSHOP_MAX_UNITS_PER_VARIANT = 400`; `MAX_SKUS_PER_RUN = 500`; `DELAY_BETWEEN_CALLS_SECONDS = 1.0`.

## main.py entry points
- `run_stock_set_multi(desired={base_sku: pieces}, dry_run)` — walks both catalogs ONCE, loops per SKU, enforces `MAX_SKUS_PER_RUN`. `run_single_sku_mode(...)` is the wrapper.
- `run_stock_balance_multi(base_skus, dry_run)` — single shared walk (`_walk_balance_catalogs()`); `run_stock_balance_mode(...)` is the wrapper.
- `_set_one_sku` / `_balance_one_sku`: per-SKU helpers, no Telegram side effects — return a result dict (`status ∈ ok | dry_run | skipped | failed`).
- `run_excel_mode(path, dry_run)` is independent; leave it untouched by multi-SKU work.

## Per-SKU failure isolation (never abort the whole batch)
**set:** missing on both / only one platform → `skipped`; write failure → `failed`; continue. **balance:** missing on either platform / total 0 → `skipped`; write failure → `failed`; continue (abort only if the catalog walk fails). Balance is idempotent — safe to call repeatedly (incl. order-bot auto-dispatch).

## CLI modes
- `stock_set.py`: single (`--sku S --pieces N`), multi (`--sku S1 S2 --pieces N1 N2`, equal-length), Excel (`stock_set.py stock.xlsx`; A=base SKU, B=total pieces).
- `stock_get.py --sku BASE_SKU`: read-only. `stock_balance.py --sku … [--dry-run]`: dedupes, uppercases, rejects `XPCS-`.
- `stock_debug.py`: operator/diagnostic only, read-only, not wired to the Worker. `stock_set_price.py`: **price-aware** set runner — what production `set.yml` runs for SKU mode.
- `stock_low.py` (`/stock_low`, `low.yml`): read-only — base SKUs with combined stock < 50 pcs; throttled 1×/24h (`low_stock_throttle`).

## Price-aware layer (TikTok Shop low-price 1PCS variants)
`stock_set_price_rule.py`, `stock_balance_price_rule.py`, `stock_balance_preserve.py` (preserve total), `stock_balance_delta_summary.py` (deltas), `stock_get_compact.py`, `shopee_detail_enrichment.py`.

## TikTok Shop weight enrichment (/stock_get only)
`202502` search omits `package_weight` → catalog weight 0. `run_stock_get_mode` calls `fetch_product_detail` (GET `/product/202309/products/{id}`) once per product_id, overwriting only where 0. Best-effort; Shopee weight from `fetch_catalog`.

## Clients
- **Shopee:** `get_item_list` + `get_item_base_info` + `get_model_list`; `update_stock` → `/api/v2/product/update_stock` (absolute). Shop-level signing `partner_id+path+timestamp+access_token+shop_id`, HMAC-SHA256 with `partner_key`.
- **TikTok Shop:** `/product/202502/products/search`; `update_stock_batch` → `/product/202309/products/{product_id}/inventory/update` (absolute). Signed + `x-tts-access-token`; `shop_cipher` from `/authorization/202309/shops`.

## Telegram output (`src/telegram_sender.py`)
Legacy Markdown, single-space; `_send` caps at 4000 chars. `/stock_set` & `/stock_balance`: 1 SKU → detailed (balance shows before→after delta), 2+ → compact. `/stock_get`: per-variant 🟧 Shopee / 🟦 TikTok Shop units + weight or `*(tidak ada)*`. Labels `SHOPEE_LABEL = "🟧 Shopee"`, `TIKTOKSHOP_LABEL = "🟦 TikTok Shop"`. Strings in Bahasa Indonesia; never abbreviate "TikTok Shop"; use "stock" not "inventory" (except real endpoints).

## Workflows
`set.yml` / `get.yml` / `balance.yml`: `workflow_dispatch` only. Concurrency: `stock-set` & `stock-balance` `cancel-in-progress: false` (never cancel mid-write); `stock-get` `true`. SKU set mode runs price-aware `scripts/stock_set_price.py`. All checkout `main`, overlay `data/` from `bot-state`, Python 3.11, commit token files back to `bot-state` after every run.

## Safety
Run exceeding `MAX_SKUS_PER_RUN` → abort before any write, alert Telegram. Never silently update only one platform unless requested; report partial failures per SKU.

## Workflow & identity (process standard)
- Author commits/PRs as `C - Furqon Aji Yudhistira <furqonajiy@gmail.com>`. **No AI references** anywhere (branches, messages, PR text, code, comments) — no Co-Authored-By, no "Generated by", no session links.
- Branch `feature/<desc>` off `main`; PR into `main`; merge with a merge commit (`--no-ff`); merge title ends with `(#PR)`. Docs + marker ride in the same PR. Maintainer is on Windows — give CLI commands in PowerShell.

## Flag before changing
Allocation (Shopee equal-share / TikTok cap + 1PCS reserve), price-aware runners, 50:50 split, `parse_sku()` uppercasing, token rotation, `bot-state`, workflow concurrency, multi-SKU input formats, the result-dict shapes, `fetch_product_detail` weight enrichment, `202502` vs `202309` usage, signing.
