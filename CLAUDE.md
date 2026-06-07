# CLAUDE.md — itbisa-shop-stock-bot

Python bot: set, read, and rebalance Shopee + TikTok Shop stock from one base SKU (or many in one set/balance run). **No cron — `workflow_dispatch` only.** Triggered manually from the Actions tab, by the Telegram Worker, or by the order bots at end-of-run.

## Stack & files
- Python 3.11.
- Core: `src/main.py`, `src/config.py`, `src/stock_allocator.py` (allocation math), `src/shopee_client.py`, `src/tiktokshop_client.py`, `src/shopee_auth.py`, `src/tiktokshop_auth.py`, `src/telegram_sender.py`, `src/excel_reader.py`.
- Price-aware + summary helpers: `src/stock_set_price_rule.py`, `src/stock_balance_price_rule.py`, `src/stock_balance_preserve.py`, `src/stock_balance_delta_summary.py`, `src/stock_get_compact.py`, `src/shopee_detail_enrichment.py`.
- CLIs: `scripts/stock_set.py`, `scripts/stock_set_price.py`, `scripts/stock_get.py`, `scripts/stock_balance.py`, `scripts/stock_debug.py`.
- Workflows: `.github/workflows/set.yml`, `get.yml`, `balance.yml`.

## State / tokens
- Token files ONLY: `data/shopee_tokens.json`, `data/tiktokshop_tokens.json`. Committed to `bot-state` after every run (tokens can rotate even on read-only/dry-run reads).
- **Never create or require `data/processed_orders.json`** here.

## Golden rule
**Never lose stock.** Every piece is accounted for. TikTok Shop overflow goes to the largest pack-size variant with no cap — never discarded.

## Split rule (50:50) — `split_across_platforms`
Input is total physical warehouse stock. Shopee share = `ceil(total/2)`; TikTok Shop share = `floor(total/2)`; odd totals give Shopee +1. Same rule for `/stock_set` and `/stock_balance`.

## Allocation — lives in `src/stock_allocator.py` only
- Set absolute stock units per variant, never deltas. Platform clients fetch catalogs and write only — no allocation logic in clients.
- **Shopee:** variants may be separate products. Unconstrained/equal-share allocation across discovered pack-size variants; any remainder absorbed by the smallest-multiplier variant when representable. **Do NOT apply the TikTok Shop per-variant cap to Shopee.**
- **TikTok Shop:** variants are siblings under one product. Fill variants **smallest-first, capping each at `TIKTOKSHOP_MAX_UNITS_PER_VARIANT` units**; leftover stock stacks onto the largest-multiplier variant (intentionally over the cap so no pieces are dropped). Spreading across pack sizes widens the single-order quantities a buyer can place (TikTok Shop limits ~20 units/SKU/order). **Exception:** for base SKUs in `TIKTOKSHOP_1PCS_RESERVE_BASE_SKUS`, the 1PCS variant is reserved to 1 unit and the rest balanced across the others (`_allocate_tiktokshop_with_1pcs_reserve`).
- `parse_sku()` **uppercases the returned `base_sku`** (collapses Shopee/TikTok Shop case differences into one dict key, matching the Worker-uppercased operator input via `catalog.get(base_sku)`). Each variant keeps its original `raw_sku` for display.

## SKU rules
- Operator provides base SKU only. Pack-size variants: `<digits>PCS-<base_sku>` (e.g. `20PCS-ITBISA-LED-5MM`). Value-style variants (e.g. `ITBISA-RESISTOR-27K-1/4W`) live under one parent on Shopee; the line's `model_sku` carries the variant.
- `XPCS-` variant SKUs are rejected by `scripts/stock_set.py` and `scripts/stock_balance.py` (warning + skip), so the allocator never receives them as user input. The allocator parses `XPCS-` variants from platform catalogs itself.

## Constants — `src/config.py` (NEVER env vars or GitHub Secrets)
- `SHOPEE_API_BASE_URL = https://partner.shopeemobile.com`
- `TIKTOKSHOP_AUTH_BASE_URL = https://auth.tiktok-shops.com`
- `TIKTOKSHOP_OPEN_API_BASE_URL = https://open-api.tiktokglobalshop.com`
- `TIKTOKSHOP_MAX_UNITS_PER_VARIANT = 400` (active TikTok Shop per-variant unit cap)
- `MAX_SKUS_PER_RUN = 500`
- `DELAY_BETWEEN_CALLS_SECONDS = 1.0`

## main.py entry points
- `run_stock_set_multi(desired, dry_run)` where `desired` is `dict[base_sku, total_pieces]`. Walks both catalogs ONCE for the whole batch, loops per SKU. Enforces `MAX_SKUS_PER_RUN` against `desired`.
- `run_single_sku_mode(base_sku, total_pieces, dry_run)` → thin wrapper delegating to `run_stock_set_multi({base_sku: total_pieces}, …)`.
- `run_stock_balance_multi(base_skus, dry_run)`. Walks both catalogs ONCE, loops per SKU. `_walk_balance_catalogs()` is the shared single walk.
- `run_stock_balance_mode(base_sku, dry_run)` → thin wrapper delegating to `run_stock_balance_multi([base_sku], …)`.
- `_set_one_sku(...)` / `_balance_one_sku(...)`: per-SKU helpers, **no Telegram side effects** — return a result dict so the caller chooses detailed vs compact output.
  - set result: `{ base_sku, status, reason, total_pieces, shopee_pieces, tiktokshop_pieces, shopee_lines, tiktokshop_lines, shopee_status, tiktokshop_status }`
  - balance result: `{ base_sku, status, reason, total_pieces, shopee_before_pieces, tiktokshop_before_pieces, shopee_after_pieces, tiktokshop_after_pieces, shopee_lines, tiktokshop_lines, shopee_status, tiktokshop_status }`
  - `status` ∈ `ok | dry_run | skipped | failed`.
- Both reuse `_format_and_push_shopee` / `_format_and_push_tiktokshop` for writes — no duplicated allocation logic.
- `run_excel_mode(path, dry_run)` is independent (own inline loop + `send_run_summary`); leave it untouched by multi-SKU work.

## Per-SKU failure isolation (never abort the whole batch)
- **set:** SKU missing on both platforms → `skipped`; SKU only on one platform → `skipped`; platform write failure → `failed`. Continue with the rest.
- **balance:** SKU missing on either platform → `skipped`; total pieces = 0 → `skipped`; platform write failure → `failed`. Continue. The run aborts only if the catalog walk itself fails.
- Balance is idempotent — always writes when conditions are met, safe to call repeatedly (incl. order-bot auto-dispatch).

## CLI modes
- `stock_set.py`: single (`--sku S --pieces N`), multi (`--sku S1 S2 --pieces N1 N2`, `argparse nargs='+'` on both, equal-length validated), Excel (`stock_set.py stock.xlsx [--dry-run]`; cols A=base SKU, B=total pieces; file must already exist in the workspace).
- `stock_get.py --sku BASE_SKU`: read-only.
- `stock_balance.py --sku BASE_SKU [BASE_SKU …] [--dry-run]`: `nargs='+'`; bootstraps `sys.path` so `from src.main import …` resolves; dedupes, uppercases, rejects `XPCS-`, then calls `run_stock_balance_multi`.
- `stock_debug.py --sku BASE_SKU_OR_SUBSTRING`: operator/diagnostic only, read-only, NOT wired to the Worker. Exact + case-insensitive + substring scans, `repr()` of keys, sample of 10 keys per platform.
- `stock_set_price.py --sku … --pieces … [--dry-run]`: **price-aware** set runner (`nargs='+'`). This is what production `set.yml` runs for SKU mode (plain `stock_set.py` is Excel-mode only in the workflow).

## Price-aware layer (TikTok Shop low-price 1PCS variants)
- `stock_set_price_rule.py` — price-aware `/stock_set` runner for `--sku`/`--pieces` mode; sets stock then applies the low-price 1PCS rule.
- `stock_balance_price_rule.py` — `/stock_balance` orchestration for TikTok Shop low-price 1PCS variants.
- `stock_balance_preserve.py` — `/stock_balance` variant that preserves the existing grand total when the allocator cannot fully represent the input.
- `stock_balance_delta_summary.py` — balance runner with compact before→after delta Telegram formatting.
- `stock_get_compact.py` — compact `/stock_get` runner with marketplace detail price + weight enrichment.
- `shopee_detail_enrichment.py` — best-effort Shopee price enrichment for Telegram summaries (`enrich_shopee_prices`).

## TikTok Shop weight enrichment (/stock_get only)
- `202502` product/search omits `package_weight`, so `fetch_catalog` returns `weight_grams = 0` for every TikTok Shop variant.
- In `run_stock_get_mode`, after variant filtering, call `tiktokshop_client.fetch_product_detail(product_id)` (GET `/product/202309/products/{product_id}`) once per unique `product_id` for the requested SKU. Result is `{sku_id: weight_grams}`; only overwrite variants where `weight_grams == 0`.
- Best-effort: detail-call failure leaves `weight_grams = 0` (renders "—"), logged to the Actions log only, never raised, never sent to Telegram.
- **Not** called from `fetch_catalog` — `/stock_set` and `/stock_balance` don't display weight, so they don't pay the extra GET.
- Weight normalized to grams from KILOGRAM/GRAM/POUND; unknown/empty unit defaults to KILOGRAM.
- (Verbose schema-drift diagnostic prints may exist here — remove once weight enrichment is confirmed stable.)
- Shopee weight comes directly from `fetch_catalog` (no extra call).

## Clients
- **Shopee:** walks active NORMAL products via `get_item_list` + `get_item_base_info` + `get_model_list`; groups variants by parsed base SKU; reads `stock_units` + `weight_grams`. `update_stock` → `/api/v2/product/update_stock` (absolute set). Shop-level signing (`partner_id + path + timestamp + access_token + shop_id`, HMAC-SHA256, `partner_key`).
- **TikTok Shop:** walks active products via `/product/202502/products/search`; groups by parsed base SKU; reads `stock_units`. `update_stock_batch` → `/product/202309/products/{product_id}/inventory/update` (absolute set; endpoint says "inventory" but treat as absolute stock). Signed + `x-tts-access-token`; `shop_cipher` from `/authorization/202309/shops`.

## Telegram output — per mode (`src/telegram_sender.py`)
- Markdown (legacy). Single-space formatting. `_send` caps at `_MAX_MESSAGE_CHARS = 4000`.
- `/stock_set`: **1 SKU** → `send_single_sku_summary` (detailed). **2+ SKU** → `send_stock_set_multi_summary` (one compact message; 3-line blocks for ok/dry_run, 2-line for skipped/failed; Ringkasan footer). `_send_single_set_telegram` is the `len(results)==1` indirection.
- `/stock_get`: `send_stock_get_summary` (per-variant 🟧 Shopee / 🟦 TikTok Shop units + weight or `*(tidak ada)*`, per-variant totals, Ringkasan footer).
- `/stock_balance`: **1 SKU** → `send_stock_balance_summary` (detailed, before → after with signed delta). **2+ SKU** → `send_stock_balance_multi_summary` (compact). `_send_single_balance_telegram` is the `len(results)==1` indirection.
- `send_alert(text, mode="Set Stock")` → header `🚨 *{mode}* — Error`. Modes: Excel/multi-set/single-set-skipped ride "Set Stock"; get uses "Get Stock"; balance uses "Balance Stock".
- Helpers: `_label_for_platform`, `_decorate_platforms` (prefixes bare "Shopee"/"TikTok Shop" in arbitrary strings, guarded against double-prefix), `_strip_sku_prefix` (drops a leading ``SKU `XXX` `` from reasons in compact summaries).

## Workflows — required config
- `set.yml` (Set Stock): `workflow_dispatch` only, concurrency group `stock-set`, `cancel-in-progress: false` (queue, never cancel mid-write — a cancelled multi-SKU batch leaves partial writes). Inputs `sku`, `pieces`, `excel_path`, `dry_run`. SKU mode runs the **price-aware** runner `python scripts/stock_set_price.py --sku … --pieces …` (`$INPUT_SKU`/`$INPUT_PIECES` passed **unquoted** to feed `nargs='+'`); Excel mode runs `python scripts/stock_set.py "$INPUT_EXCEL_PATH"`.
- `get.yml` (Get Stock): `workflow_dispatch` only, concurrency group `stock-get`, `cancel-in-progress: true` (separate, so get never cancels set). Input `sku`.
- `balance.yml` (Balance Stock): `workflow_dispatch` only, concurrency group `stock-balance`, `cancel-in-progress: false` (`/resi_all` can fire balance for both platforms within seconds; cancelling mid-write leaves one platform updated). Inputs `sku`, `dry_run`. Run step passes `$SKU_ARG` **unquoted** into `python scripts/stock_balance.py --sku …`.
- All: checkout `main`; overlay `data/` from `bot-state` if present; Python 3.11; `actions/checkout@v5+`, `actions/setup-python@v6+`; `pip install -r requirements.txt`; commit token files back to `bot-state` after every run (read-only/dry-run included).

## Safety
- Excel or multi-SKU run exceeding `MAX_SKUS_PER_RUN` → abort before any write, alert via Telegram. (Telegram dispatches are already capped at 20 by the Worker.)
- Never silently update only one platform unless explicitly requested; report partial platform failures per SKU.

## Global architecture & conventions (shared across all ITBisa repos)
- GitHub Actions only. No VM, server, database, queue, or long-running process.
- `main` = source code. `bot-state` = runtime token files only. Never protect `bot-state`. Never commit live token files to `main`.
- Never hardcode secrets.
- Self-contained repo, no shared library — platform-label constants are duplicated across repos on purpose.
- Minimal, targeted changes only. No broad refactors; preserve existing behavior unless explicitly in scope.
- Telegram user-facing strings: Bahasa Indonesia. Never abbreviate "TikTok Shop" to "TikTok". Use "stock", not "inventory" (except real endpoint names such as `/inventory/update`).
- Platform labels (`src/telegram_sender.py`): `SHOPEE_LABEL = "🟧 Shopee"`, `TIKTOKSHOP_LABEL = "🟦 TikTok Shop"`. Changing a glyph changes every Telegram message in this repo.
- Runtime dispatch/checkout ref is `main`. `feature/improve` must be merged to `main` before production uses it.

## Development workflow (process standard)
- Branch from `main` using `feature/<short-description>` (e.g. `feature/document-dev-workflow`).
- Always open a PR into `main` and **merge with a merge commit (`--no-ff`)** — never squash, never fast-forward — so the feature branch stays an ancestor of `main`.
- Commits and PRs are authored as **`C - Furqon Aji Yudhistira <furqonajiy@gmail.com>`** (never "Claude").
- Keep changes minimal and targeted; update `CLAUDE.md` / `README.md` in the same PR whenever behavior or process changes.
- `PROJECT_INSTRUCTIONS.md` is the synced source for the Claude & ChatGPT project instructions (≤ 8000 chars, ChatGPT limit). Update it **only when explicitly asked**, not on every change.
- Sync marker: a file named `YYYY-MM-DD_HHMM.txt` (WIB) sits at the repo root. **On every update to this repo, rename it to the current WIB timestamp** — it signals whether the repo / Claude / ChatGPT instructions are in sync.

## Flag before changing
Stock allocation (Shopee equal-share / TikTok per-variant cap + `TIKTOKSHOP_1PCS_RESERVE_BASE_SKUS` exception), the price-aware `/stock_set` runner, the 50:50 split, `parse_sku()` uppercase normalization, token rotation, `bot-state`, workflow concurrency (incl. `stock-set` `cancel-in-progress: false` queuing semantics), `/stock_set` `/stock_get` `/stock_balance` inputs (multi-SKU format, SKU/JUMLAH pairs), `run_stock_set_multi` vs `run_single_sku_mode`, `run_stock_balance_multi` vs `run_stock_balance_mode`, `_set_one_sku` / `_balance_one_sku` result-dict shape, the 1-SKU-detailed vs 2+-SKU-compact Telegram strategy, `fetch_product_detail` weight enrichment, `202502` vs `202309` endpoint usage and the `package_weight` path, `send_alert(text, mode)` per-mode header, signing.