# itbisa-shop-stock-bot — ChatGPT Chat guide

Condensed `CLAUDE.md` for ChatGPT Chat (≤ 8000 chars). The repo's `CLAUDE.md` is the full source of truth.

## What it is
Python bot that sets, reads, and rebalances Shopee + TikTok Shop stock from one base SKU (or many per run). **No cron — `workflow_dispatch` only**, triggered from the Actions tab, by the Telegram Worker, or by the order bots at end-of-run. GitHub Actions only: no server/DB/long-running process.

## Stack & files (Python 3.11)
- Core: `src/main.py`, `src/config.py`, `src/stock_allocator.py` (allocation math), `src/shopee_client.py`, `src/tiktokshop_client.py`, `src/shopee_auth.py`, `src/tiktokshop_auth.py`, `src/telegram_sender.py`, `src/excel_reader.py`.
- Price-aware + summary helpers: `src/stock_set_price_rule.py`, `src/stock_balance_price_rule.py`, `src/stock_balance_preserve.py`, `src/stock_balance_delta_summary.py`, `src/stock_get_compact.py`, `src/shopee_detail_enrichment.py`.
- CLIs: `scripts/stock_set.py`, `scripts/stock_set_price.py`, `scripts/stock_get.py`, `scripts/stock_balance.py`, `scripts/stock_debug.py`.
- Workflows: `.github/workflows/set.yml`, `get.yml`, `balance.yml`.

## State / tokens
Token files ONLY: `data/shopee_tokens.json`, `data/tiktokshop_tokens.json`, committed to `bot-state` after every run (tokens rotate even on read-only/dry-run). **Never create or require `data/processed_orders.json`** here. `main` = source; `bot-state` = runtime token files only — never protect it, never commit live tokens to `main`.

## Golden rule
**Never lose stock.** Every piece is accounted for. TikTok Shop overflow goes to the largest pack-size variant uncapped — never discarded.

## Split rule — `split_with_shopee_min_reserve`
Reserve `ceil(SHOPEE_RESERVE_IDR / Shopee 1PCS price)` to Shopee first (Rp200.000 → ~200 units), then split the remainder **70:30** Shopee:TikTok Shop (`SHOPEE_SPLIT_PERCENT`). `/stock_set` = `/stock_balance`; unknown price → no reserve. Excel mode plain 50:50.

## Allocation — lives in `src/stock_allocator.py` only
- Set absolute units per variant, never deltas. Clients fetch catalogs and write only — no allocation logic in clients.
- **Shopee:** variants may be separate products. Unconstrained/equal-share across discovered pack-size variants; remainder absorbed by the smallest-multiplier variant when representable. Do NOT apply the TikTok Shop per-variant cap to Shopee.
- **TikTok Shop:** variants are siblings under one product. Fill smallest-first, capping each at `TIKTOKSHOP_MAX_UNITS_PER_VARIANT`; leftover stacks onto the largest-multiplier variant (intentionally over cap so nothing drops). Exception: base SKUs in `TIKTOKSHOP_1PCS_RESERVE_BASE_SKUS` reserve the 1PCS variant to 1 unit and balance the rest.
- `parse_sku()` uppercases `base_sku` (one dict key across platforms); each variant keeps its `raw_sku` for display.

## SKU rules
Operator provides base SKU only. Pack-size variants: `<digits>PCS-<base_sku>` (e.g. `20PCS-ITBISA-LED-5MM`). `XPCS-` variant SKUs are rejected by the CLIs (warn + skip); the allocator parses `XPCS-` from platform catalogs itself.

## Constants — `src/config.py` (never env vars or Secrets)
Shopee/TikTok base URLs; `TIKTOKSHOP_MAX_UNITS_PER_VARIANT = 400`; `MAX_SKUS_PER_RUN = 500`; `DELAY_BETWEEN_CALLS_SECONDS = 1.0`.

## main.py entry points
- `run_stock_set_multi(desired={base_sku: pieces}, dry_run)` — walks both catalogs ONCE, loops per SKU, enforces `MAX_SKUS_PER_RUN`. `run_single_sku_mode(...)` is the wrapper.
- `run_stock_balance_multi(base_skus, dry_run)` — single shared walk (`_walk_balance_catalogs()`); `run_stock_balance_mode(...)` is the wrapper.
- `_set_one_sku` / `_balance_one_sku`: per-SKU helpers, no Telegram side effects — return a result dict (`status ∈ ok | dry_run | skipped | failed`).
- `run_excel_mode(path, dry_run)` is independent; leave it untouched by multi-SKU work.

## Per-SKU failure isolation (never abort the whole batch)
**set:** missing on both → `skipped`; on only one platform → full total to that platform (no split); write failure → `failed`; continue. **balance:** missing on either platform / total 0 → `skipped`; write failure → `failed`; continue (abort only if the walk fails). Balance is idempotent (safe to call repeatedly, incl. order-bot auto-dispatch).

## CLI modes
- `stock_set.py`: single (`--sku S --pieces N`), multi (`--sku S1 S2 --pieces N1 N2`, equal-length), Excel (`stock_set.py stock.xlsx`; A=base SKU, B=total pieces).
- `stock_get.py --sku BASE_SKU`: read-only. `stock_balance.py --sku … [--dry-run]`: dedupes, uppercases, rejects `XPCS-`.
- `stock_debug.py`: operator/diagnostic only, read-only, not wired to the Worker. `stock_set_price.py`: **price-aware** set runner (production `set.yml` SKU mode).
- `stock_low.py` (`/stock_low`, `low.yml`): read-only — base SKUs with combined stock < 50 pcs; throttled 1×/24h (`low_stock_throttle`).
- `harga_set.py` (`/harga_set`, `harga.yml`): tiered pricing (`harga_set_price.py`). `(JUMLAH HARGA)` pairs; each TikTok Shop pack-size variant priced by the tier its multiplier bands into × pack size (`update_price_batch`, 202309). TikTok Shop only; Shopee Harga Grosir later.

## Price-aware layer (TikTok Shop low-price 1PCS variants)
Modules: `*_price_rule`, `stock_balance_preserve`, `stock_balance_delta_summary`, `stock_get_compact`, `shopee_detail_enrichment` (see Stack list).

## TikTok Shop weight enrichment (/stock_get only)
`202502` search omits `package_weight` → catalog weight 0. `run_stock_get_mode` calls `fetch_product_detail` (202309) per product_id, overwriting only where 0. Best-effort; Shopee weight from `fetch_catalog`.

## Clients
- **Shopee:** `get_item_list` + `get_item_base_info` + `get_model_list`; `update_stock` → `/api/v2/product/update_stock` (absolute). Shop-level signing `partner_id+path+timestamp+access_token+shop_id`, HMAC-SHA256 with `partner_key`.
- **TikTok Shop:** `/product/202502/products/search`; `update_stock_batch` → `/product/202309/products/{product_id}/inventory/update` (absolute). Signed + `x-tts-access-token`; `shop_cipher` from `/authorization/202309/shops`.

## Telegram output (`src/telegram_sender.py`)
Legacy Markdown, single-space; `_send` caps at 4000 chars. `/stock_set` & `/stock_balance`: 1 SKU → detailed (balance shows before→after delta), 2+ → compact. `/stock_get` per-variant 🟧/🟦 units+weight. `/harga_set` → `send_harga_set_summary` (per-variant TikTok price). Labels `SHOPEE_LABEL`/`TIKTOKSHOP_LABEL` = 🟧/🟦. Bahasa Indonesia; never abbreviate "TikTok Shop"; "stock" not "inventory".

## Workflows
`set.yml` / `get.yml` / `balance.yml` / `low.yml` / `harga.yml`: `workflow_dispatch` only. Write paths (`stock-set`, `stock-balance`, `stock-harga`) `cancel-in-progress: false` (never cancel mid-write); `stock-get` `true`. SKU set mode runs price-aware `scripts/stock_set_price.py`. All checkout `main`, overlay `data/` from `bot-state`, Python 3.11, commit token files back to `bot-state` after every run.

## Safety
Run exceeding `MAX_SKUS_PER_RUN` → abort before any write, alert Telegram. Report partial platform failures per SKU.

## Workflow & identity (process standard)
- Author commits/PRs as `C - Furqon Aji Yudhistira <furqonajiy@gmail.com>`. **No AI references** anywhere (branches, messages, PR text, code, comments) — no Co-Authored-By, no "Generated by", no session links.
- Branch `feature/<desc>` off `main`; PR into `main`; merge with a merge commit (`--no-ff`); merge title ends with `(#PR)`. Docs + marker ride in the same PR. Maintainer is on Windows — give CLI commands in PowerShell.

## Flag before changing
Allocation (Shopee equal-share / TikTok cap + 1PCS reserve), price-aware runners, Shopee reserve + 70:30 split, `parse_sku()` uppercasing, token rotation, `bot-state`, workflow concurrency, multi-SKU formats, result-dict shapes, `fetch_product_detail` weight enrichment, `202502` vs `202309`, signing.
