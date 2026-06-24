# CLAUDE.md — itbisa-shop-stock-bot

> **Single source of truth for this repo.** Read automatically by Claude Code and pasted into the Claude Chat project. `AGENTS.md` (ChatGPT Codex) points here; `CHATGPT_CHAT.md` is the ≤ 8000-char condensed copy for ChatGPT Chat. Keep all three at the repo root.

Python bot: set, read, and rebalance Shopee + TikTok Shop stock from one base SKU (or many in one set/balance run). **No cron — `workflow_dispatch` only.** Triggered manually from the Actions tab, by the Telegram Worker, or by the order bots at end-of-run.

## Stack & files
- Python 3.11.
- Core: `src/main.py`, `src/config.py`, `src/stock_allocator.py` (allocation math), `src/shopee_client.py`, `src/tiktokshop_client.py`, `src/shopee_auth.py`, `src/tiktokshop_auth.py`, `src/telegram_sender.py`, `src/excel_reader.py`.
- Price-aware + summary helpers: `src/stock_set_price_rule.py`, `src/stock_balance_price_rule.py`, `src/stock_balance_preserve.py`, `src/stock_balance_delta_summary.py`, `src/stock_get_compact.py`, `src/shopee_detail_enrichment.py`, `src/harga_set_price.py` (tiered pricing), `src/variant_set_tiktok.py` (TikTok pack-size variant rebuild), `src/weight_set_tiktok.py` (TikTok per-piece weight set, reuses variant_set's Edit Product + category helpers).
- CLIs: `scripts/stock_set.py`, `scripts/stock_set_price.py`, `scripts/stock_get.py`, `scripts/stock_balance.py`, `scripts/stock_low.py`, `scripts/harga_set.py`, `scripts/variant_set.py`, `scripts/weight_set.py`, `scripts/stock_debug.py`.
- Workflows: `.github/workflows/set.yml`, `get.yml`, `balance.yml`, `low.yml`, `harga.yml`, `variant.yml`, `weight.yml` (execution, `workflow_dispatch`; all pip-cached); `ci.yml` (quality gate — runs `pytest` on PRs that touch code/tests/deps, pip-cached, cancels superseded runs; no secrets, never touches `bot-state`). Write paths (`set`/`balance`/`harga`/`variant`/`weight`) carry no kill-timeout by design (`cancel-in-progress: false` — never cancel mid-write); `get`/`low` (read-only) and `ci` have a runaway-safe `timeout-minutes`.
- Low-stock report + helpers: `src/low_stock.py` (`find_low_stock`, `run_stock_low_mode`), `src/low_stock_throttle.py` (24h throttle).
- Tests: `tests/` (pytest). Covers the pure logic only — `stock_allocator.py` (50:50 split, Shopee equal-share, TikTok Shop cap + overflow, 1PCS reserve, Shopee minimum-purchase reserve `shopee_min_reserve_units` / `split_with_shopee_min_reserve`, `parse_sku`, `verify_allocation`) and `low_stock` (`find_low_stock`, throttle `window_open`). Dev deps in `requirements-dev.txt`. Run `pytest -q`. No network/API calls are unit-tested (use `--dry-run` for that).

## State / tokens
- Token files ONLY: `data/shopee_tokens.json`, `data/tiktokshop_tokens.json`. Committed to `bot-state` after every run (tokens can rotate even on read-only/dry-run reads).
- **Never create or require `data/processed_orders.json`** here.

## Golden rule
**Never lose stock.** Every piece is accounted for. TikTok Shop overflow goes to the largest pack-size variant with no cap — never discarded.

## Split rule — `split_across_platforms(total, shopee_percent)`
Splits total physical warehouse stock between platforms by `shopee_percent` (Shopee's share; TikTok Shop gets the rest; Shopee absorbs the rounding remainder). Production `/stock_set` + `/stock_balance` pass `config.SHOPEE_SPLIT_PERCENT` (**70 → 70:30 Shopee:TikTok Shop**). The pure function defaults to 50 (used by the legacy Excel/`main.py` paths, which stay 50:50). Integer math: `tiktokshop = total*(100-pct)//100; shopee = total - tiktokshop`.

## Low-stock report (`/stock_low`) — `src/low_stock.py`
Read-only report of every base SKU whose **combined** on-hand stock (Shopee + TikTok Shop, in pieces) is **strictly below** `LOW_STOCK_THRESHOLD` (default 50). `find_low_stock(shopee_catalog, tiktokshop_catalog, threshold)` is the pure scan (union of both catalogs; combined pieces = `Σ stock_units × multiplier`; sorted ascending by base SKU). `run_stock_low_mode` walks both catalogs via `_walk_balance_catalogs`, then `telegram_sender.send_low_stock_summary` sends the list (chunked across messages — `_send_chunked` — since it can be long). **Throttled to once per `LOW_STOCK_MIN_INTERVAL_HOURS` (24h)** via `src/low_stock_throttle.py` (`data/low_stock_throttle.json` on `bot-state`): a trigger inside the window skips the scan and replies "already generated" (`send_low_stock_skipped`). The Telegram Worker is stateless, so the 1×/day cap lives here — repeat triggers still spin a runner but skip the scan. The timestamp is recorded only after a successful scan (a failed scan can retry within the window). Runs via `scripts/stock_low.py` (no args; threshold from config).

## Shopee stock reserve (`/stock_balance` + `/stock_set`) — `split_with_shopee_min_reserve`
Before splitting, the bot reserves `reserve = min(ceil(SHOPEE_RESERVE_IDR / shopee_unit_price), total)` units to Shopee **first** (e.g. Rp 200.000 ÷ Rp 1.000 = 200 units), then splits the remainder by `SHOPEE_SPLIT_PERCENT` (70:30). The Shopee unit price is the `multiplier == 1` (1PCS) variant's `price_idr`, obtained best-effort via `enrich_shopee_prices` (called before the split, wrapped in try/except). **Fallbacks:** unknown price / `SHOPEE_RESERVE_IDR = 0` → no reserve (plain 70:30); `total < reserve` → all to Shopee, 0 to TikTok Shop. Shopee listings are single 1PCS products, so reserving to Shopee's total lands the units on the 1PCS variant. Pure math in `shopee_min_reserve_units` / `split_with_shopee_min_reserve` (`stock_allocator.py`); wired identically into **both** the balance runner (`stock_balance_price_rule._balance_one_sku`) and the price-aware set runner (`stock_set_price_rule._set_one_sku`) so `/stock_set` and `/stock_balance` share the same split logic. (Excel-mode `/stock_set` via `run_excel_mode` still uses a plain 50:50 split, no reserve.)

## Tiered pricing (`/harga_set`) — `src/harga_set_price.py` (Shopee + TikTok Shop)
Set tiered ("Harga Grosir"-style) prices for one **exact** base SKU on both platforms. Input is `(JUMLAH HARGA)` pairs = a unit price per quantity band, e.g. `1 749 50 739 100 699` → 1–49=Rp749, 50–99=Rp739, 100+=Rp699. `parse_tiers` validates + sorts; `unit_price_for_quantity(tiers, qty)` bands by the largest `start_qty ≤ qty` (below the lowest start → `None`). **TikTok Shop:** each pack-size variant is priced by the tier its multiplier `M` bands into, listing price = `unit_price × M` (1PCS→749, 50PCS→739×50, 1000PCS→699×1000) via `tiktokshop_client.update_price_batch`; variants below the lowest tier are skipped + reported. **Shopee:** `compute_shopee_pricing(tiers)` → the `multiplier == 1` listing gets base price = tier covering qty 1 (`update_price`) + Harga Grosir wholesale tiers `(min,max,unit_price)` for bands ≥ 2 (`set_wholesale`, contiguous, top band open to `999999`); Shopee pack-size products (mult > 1) are skipped + reported. Both best-effort per variant. Runs through `scripts/harga_set.py` (`--sku`, `--tiers q1 p1 …`, `--dry-run`) on `harga.yml`. **The Shopee wholesale endpoints (`update_wholesale`/`add_wholesale`/`delete_wholesale`, field `wholesale_list`) are best-effort and pending live verification** — the official docs are login-gated; use `--dry-run` first. Pure tier + Shopee-mapping logic is unit-tested (`tests/test_harga_set.py`); API writes exercised via `--dry-run`.

## Variant creation (`/variant_set`) — `src/variant_set_tiktok.py` (TikTok Shop only)
Rebuild a TikTok Shop product's pack-size variation to an exact set via the **Edit Product (202309)** API (`tiktokshop_client.edit_product`, full-replace, **`PUT`** `/product/202309/products/{id}` — POST is Create and 405s). `/variant_set <BASE_SKU> 1 20 50 500 1000` → the `Packing` variation ends up with exactly those pack sizes **plus an always-present `ITBISA-BUBBLE-WRAP` value (stock 0, price 100)**; other values (e.g. 5PCS/100PCS) are dropped. `build_edit_payload` (pure, unit-tested) reuses the product's title/description/main_images/dimensions/attributes from `fetch_product_detail_raw`, keeps existing values' `value_id`/price, and creates new ones at **stock 0** with price/weight scaled from the 1PCS variant (refined later by `/harga_set` + `/weight_set`). Variation is text-only → the product **main image** is the default (no per-variant image upload). seller_sku: 1PCS = bare base SKU, packs = `<M>PCS-<base>`. **Stock-safe flow:** operator saves the combined total → `/variant_set` (creates at 0) → `/stock_set <base> <total>` re-applies + splits. `variant.yml` defaults `dry_run=true` (logs the Edit Product payload, no write). **Verified live** on `ITBISA-IC-PC817-DIP4`.
- **Category (V2 required):** Edit Product mandates a `category_id` (omitting → error 36009004) and rejects the legacy **V1** id unless the request **declares V2** — so the payload always sends `category_version: "v2"`. The id is resolved as: the detail's `recommended_categories` leaf if present, else (in the runner) the V2 leaf whose `local_name` matches the product's current V1 leaf name, looked up via `tiktokshop_client.fetch_categories()` (`GET /product/202309/categories?category_version=v2`; the V2 tree keeps the same leaf names/ids as V1 for migrated categories). Recommend-by-title is unreliable for niche components and is not used.

## Weight set (`/weight_set`) — `src/weight_set_tiktok.py` (TikTok Shop only)
Set the **per-piece** weight across a product's existing pack-size variants via the same **Edit Product (202309) PUT** as `/variant_set`. `/weight_set <BASE_SKU> <refPcs> <grams>` (e.g. `… 1000 1700` = 1000PCS weighs 1700 g) → per-piece = `grams / refPcs` (1.7 g/pcs); each variant's `package_weight` = `per_pcs × its multiplier` (1PCS→1.7 g, 20PCS→34 g, …). `build_weight_edit_payload` (pure, unit-tested) **preserves the existing variation set, stock (inventory), and prices** — only weights change; `ITBISA-BUBBLE-WRAP` keeps its own weight (not a pack of the product). Category handled exactly as `/variant_set` (`category_version: "v2"` + recommended-or-name-matched V2 leaf, reusing the variant_set helpers + `fetch_categories`). Runs through `scripts/weight_set.py` (`--sku`, `--ref-pcs`, `--grams`, `--dry-run`) on `weight.yml` (defaults `dry_run=true`). Telegram: `send_weight_set_summary` (⚖️ Set Weight, per-variant kg lines).

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
- `SHOPEE_RESERVE_IDR = 200000` (IDR value of stock reserved to Shopee first in `/stock_balance` + `/stock_set`; 0 disables)
- `SHOPEE_SPLIT_PERCENT = 70` (Shopee's share of the post-reserve remainder → 70:30)
- `LOW_STOCK_THRESHOLD = 50` (`/stock_low` flags combined on-hand pieces below this)
- `LOW_STOCK_MIN_INTERVAL_HOURS = 24` (`/stock_low` report throttle window)

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
- **set:** SKU missing on **both** platforms → `skipped`; **SKU on only one platform → the full requested total is set on that platform (no split)** via `_set_shopee_only` / `_set_tiktokshop_only`; platform write failure → `failed`. Continue with the rest.
- **balance:** SKU missing on either platform → `skipped` (single-platform stock is already 100% on its one platform — nothing to redistribute, and skipping avoids a redundant write); total pieces = 0 → `skipped`; platform write failure → `failed`. Continue. The run aborts only if the catalog walk itself fails.
- Balance is idempotent — always writes when conditions are met, safe to call repeatedly (incl. order-bot auto-dispatch).

## CLI modes
- `stock_set.py`: single (`--sku S --pieces N`), multi (`--sku S1 S2 --pieces N1 N2`, `argparse nargs='+'` on both, equal-length validated), Excel (`stock_set.py stock.xlsx [--dry-run]`; cols A=base SKU, B=total pieces; file must already exist in the workspace).
- `stock_get.py --sku BASE_SKU`: read-only.
- `stock_balance.py --sku BASE_SKU [BASE_SKU …] [--dry-run]`: `nargs='+'`; bootstraps `sys.path` so `from src.main import …` resolves; dedupes, uppercases, rejects `XPCS-`, then calls `run_stock_balance_multi`.
- `stock_debug.py --sku BASE_SKU_OR_SUBSTRING`: operator/diagnostic only, read-only, NOT wired to the Worker. Exact + case-insensitive + substring scans, `repr()` of keys, sample of 10 keys per platform.
- `stock_set_price.py --sku … --pieces … [--dry-run]`: **price-aware** set runner (`nargs='+'`). This is what production `set.yml` runs for SKU mode (plain `stock_set.py` is Excel-mode only in the workflow).

## Price-aware layer (TikTok Shop low-price 1PCS variants)
- `stock_set_price_rule.py` — price-aware `/stock_set` runner for `--sku`/`--pieces` mode; same split logic as `/stock_balance` (Shopee minimum-purchase reserve → 50:50) then applies the low-price 1PCS rule + leftover-to-Shopee.
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
- **Shopee:** walks active NORMAL products via `get_item_list` + `get_item_base_info` + `get_model_list`; groups variants by parsed base SKU; reads `stock_units` + `weight_grams`. `update_stock` → `/api/v2/product/update_stock` (absolute set); `update_price` → `/api/v2/product/update_price` (base price); `set_wholesale` → `update_wholesale`/`add_wholesale`/`delete_wholesale` (Harga Grosir tiers, used by `/harga_set` — best-effort, pending live verification); `get_wholesale` → `/api/v2/product/get_wholesale` (reads Harga Grosir tiers for `/stock_get`, best-effort → `[]`; logs the raw response + parses `wholesale_list`/`wholesales` and `min_count`/`min` etc. for diagnosis). Shop-level signing (`partner_id + path + timestamp + access_token + shop_id`, HMAC-SHA256, `partner_key`).
- **TikTok Shop:** walks active products via `/product/202502/products/search`; groups by parsed base SKU; reads `stock_units`. `update_stock_batch` → `/product/202309/products/{product_id}/inventory/update` (absolute set; endpoint says "inventory" but treat as absolute stock). `update_price_batch` → `/product/202309/products/{product_id}/prices/update` (absolute price set; per-SKU `price: {amount: "<int>", currency: "IDR"}`; used by `/harga_set`). `fetch_product_detail_raw` → GET `/product/202309/products/{id}` (full data); `fetch_categories` → GET `/product/202309/categories?category_version=v2` (V2 category tree for `/variant_set` name-mapping, best-effort → `[]`); `edit_product` → **PUT** `/product/202309/products/{id}` (Edit Product full-replace, used by `/variant_set`; PUT not POST — POST on this path is Create Product and returns HTTP 405; payload declares `category_version: "v2"`). Signed + `x-tts-access-token`; `shop_cipher` from `/authorization/202309/shops`.

## Telegram output — per mode (`src/telegram_sender.py`)
- Markdown (legacy). Single-space formatting. `_send` caps at `_MAX_MESSAGE_CHARS = 4000`.
- `/stock_set`: **1 SKU** → `send_single_sku_summary` (detailed). **2+ SKU** → `send_stock_set_multi_summary` (one compact message; 3-line blocks for ok/dry_run, 2-line for skipped/failed; Ringkasan footer). `_send_single_set_telegram` is the `len(results)==1` indirection.
- `/stock_get`: `send_stock_get_summary` (per-variant 🟧 Shopee / 🟦 TikTok Shop units + weight or `*(tidak ada)*`, per-variant totals, Ringkasan footer). Single-SKU detail also shows each Shopee variant's **Harga Grosir** tiers (enriched via `shopee_client.get_wholesale`, best-effort; `_wholesale_line` in `telegram_sender`).
- `/harga_set`: `send_harga_set_summary` — result-focused, no tier echo (the command already shows the tiers). 🟦 TikTok Shop one line per variant `{M}PCS = Rp{listing} (Rp{unit}/pcs)`; 🟧 Shopee quantity bands `- 1: Rp{base}` then `- {min}–{max}: Rp{price}` (top band `–∞`); per-platform skipped note; `_(tidak ada)_` for an absent platform.
- `/stock_balance`: **1 SKU** → `send_stock_balance_summary` (detailed, before → after with signed delta). **2+ SKU** → `send_stock_balance_multi_summary` (compact). `_send_single_balance_telegram` is the `len(results)==1` indirection.
- `send_alert(text, mode="Set Stock")` → header `🚨 *{mode}* — Error`. Modes: Excel/multi-set/single-set-skipped ride "Set Stock"; get uses "Get Stock"; balance uses "Balance Stock".
- Helpers: `_label_for_platform`, `_decorate_platforms` (prefixes bare "Shopee"/"TikTok Shop" in arbitrary strings, guarded against double-prefix), `_strip_sku_prefix` (drops a leading ``SKU `XXX` `` from reasons in compact summaries).

## Workflows — required config
- `set.yml` (Set Stock): `workflow_dispatch` only, concurrency group `stock-set`, `cancel-in-progress: false` (queue, never cancel mid-write — a cancelled multi-SKU batch leaves partial writes). Inputs `sku`, `pieces`, `excel_path`, `dry_run`. SKU mode runs the **price-aware** runner `python scripts/stock_set_price.py --sku … --pieces …` (`$INPUT_SKU`/`$INPUT_PIECES` passed **unquoted** to feed `nargs='+'`); Excel mode runs `python scripts/stock_set.py "$INPUT_EXCEL_PATH"`.
- `get.yml` (Get Stock): `workflow_dispatch` only, concurrency group `stock-get`, `cancel-in-progress: true` (separate, so get never cancels set). Input `sku`.
- `balance.yml` (Balance Stock): `workflow_dispatch` only, concurrency group `stock-balance`, `cancel-in-progress: false` (`/resi_all` can fire balance for both platforms within seconds; cancelling mid-write leaves one platform updated). Inputs `sku`, `dry_run`. Run step passes `$SKU_ARG` **unquoted** into `python scripts/stock_balance.py --sku …`.
- `harga.yml` (Set Price): `workflow_dispatch` only, concurrency group `stock-harga`, `cancel-in-progress: false` (price write — never cancel mid-write). Inputs `sku`, `tiers`, `dry_run`. Run step passes `$SKU_ARG` quoted and `$TIERS_ARG` **unquoted** into `python scripts/harga_set.py --sku … --tiers …`. Needs all platform secrets (config validates them at import) even though only TikTok Shop is written today.
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
- The merge commit title must be **representative and end with the PR number**, e.g. `Update Project Instructions to the Latest State (#47)` — never the default `Merge pull request …`.
- Commits and PRs are authored as **`C - Furqon Aji Yudhistira <furqonajiy@gmail.com>`** — never an AI/assistant identity.
- **No AI/assistant references anywhere** — not in branch names, commit messages, PR titles/bodies, code, or comments. No mention of any AI assistant by name, no `Co-Authored-By` trailer, no "Generated with/by" line, no model ids, no session links. History must read as human-authored. (If the PR tool auto-appends such a footer, edit the PR body to remove it and re-check.)
- CLI commands handed to the maintainer are given in **PowerShell** syntax (Windows).
- Keep changes minimal and targeted; update `CLAUDE.md` / `README.md` in the same PR whenever behavior or process changes.
- **AI-instruction files (repo root, auto-discovered):** `CLAUDE.md` is the single source of truth — read by Claude Code and pasted into the Claude Chat project (no tight size cap). `AGENTS.md` is a thin pointer to `CLAUDE.md` for ChatGPT Codex, carrying the author-identity / no-AI-refs / feature→PR→merge / PowerShell rules inline. `CHATGPT_CHAT.md` is a ≤ 8000-char condensed copy of this file for ChatGPT Chat (its project-instruction limit). Update these **only when explicitly asked**, and keep `CHATGPT_CHAT.md` in step with `CLAUDE.md`.
- Sync marker: a file named `YYYY-MM-DD_HHMM.txt` (WIB) sits at the repo root. **On every update to this repo, rename it to the current WIB timestamp** — it signals whether the repo and the AI-instruction files are in sync.
- Doc/marker updates (`CLAUDE.md`, `AGENTS.md`, `CHATGPT_CHAT.md`, the sync marker) ride in the **same feature branch and PR as the related code change** — never a separate doc-only branch (avoids noise).

## Flag before changing
Stock allocation (Shopee equal-share / TikTok per-variant cap + `TIKTOKSHOP_1PCS_RESERVE_BASE_SKUS` exception), the price-aware `/stock_set` runner, the configurable Shopee:TikTok Shop split (`SHOPEE_SPLIT_PERCENT`, default 70:30 in production), the Shopee stock reserve (`SHOPEE_RESERVE_IDR`, `split_with_shopee_min_reserve`, `_shopee_unit_price` + the best-effort `enrich_shopee_prices` call), `parse_sku()` uppercase normalization, token rotation, `bot-state`, workflow concurrency (incl. `stock-set` `cancel-in-progress: false` queuing semantics), `/stock_set` `/stock_get` `/stock_balance` inputs (multi-SKU format, SKU/JUMLAH pairs), `run_stock_set_multi` vs `run_single_sku_mode`, `run_stock_balance_multi` vs `run_stock_balance_mode`, `_set_one_sku` / `_balance_one_sku` result-dict shape, the 1-SKU-detailed vs 2+-SKU-compact Telegram strategy, the `/stock_low` combined-stock threshold + 24h throttle (`low_stock_throttle`), `fetch_product_detail` weight enrichment, `202502` vs `202309` endpoint usage and the `package_weight` path, `send_alert(text, mode)` per-mode header, signing.