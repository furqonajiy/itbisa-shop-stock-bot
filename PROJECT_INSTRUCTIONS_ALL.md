# ITBisa Shop — Project Instructions (all 5 repos)

> One Claude/ChatGPT Project covering 5 repos. Paste this SAME text into both the Claude Project and ChatGPT. ≤ 8000 chars (ChatGPT limit). For deep work in a repo, open that repo's `CLAUDE.md` / `PROJECT_INSTRUCTIONS.md`. Update only when explicitly asked.

## The 5 repos & how they connect
ITBisa runs the IT Bisa Shop on Shopee + TikTok Shop, automated via GitHub Actions. A Telegram bot is the control surface.

- **itbisa-shop-telegram-bot** — stateless Cloudflare Worker (JS/Wrangler). Routes `@ITBisaShopBot` commands → dispatches GitHub Actions via `workflow_dispatch`. Holds no state.
- **itbisa-shopee-order-bot** — Python. Fetch Shopee orders → ship to dropoff → send waybill labels to Telegram → dispatch `/stock_balance` once. Track unit `order_sn`.
- **itbisa-tiktokshop-order-bot** — Python. Fetch TikTok Shop orders → ship packages → send labels → dispatch `/stock_balance` once. Track unit `package_id`.
- **itbisa-shop-stock-bot** — Python. Set / get / balance stock across Shopee + TikTok Shop from one base SKU (or many). 50:50 split, per-platform allocation.
- **itbisa-shop-report-bot** — Python, **offline** Excel analysis tool (no API, no workflows, no tokens). Turns sales/stock exports into an analysis workbook.

**Flow:** Telegram → Worker → dispatches order bots / stock bot. Each order bot, at end of run, dispatches the stock bot's `balance.yml` once with the SKUs it touched. report-bot is standalone (run locally).

## Shared architecture & conventions
- GitHub Actions only — no VM, server, DB, queue, long-running process (report-bot is a local CLI).
- `main` = source code. `bot-state` branch = runtime state/token files only (never protect; never commit live tokens to `main`). telegram-bot & report-bot keep no `bot-state` data.
- Order bots & stock bot are `workflow_dispatch` only — **no cron**.
- Never hardcode secrets. Self-contained repos — shared helpers (e.g. `balance_dispatcher.py`) are duplicated on purpose, not factored out.
- Telegram/user-facing strings: Bahasa Indonesia. Never abbreviate "TikTok Shop" to "TikTok". Use "stock", not "inventory" (except real endpoints like `/inventory/update`).
- Minimal, targeted changes; preserve behavior unless explicitly in scope.

## Dev workflow standard (every repo)
- Branch `feature/<short-description>` off `main`. Doc/marker updates ride in the **same feature branch/PR as the code** — never a separate doc-only branch.
- Always PR into `main` and **merge with a merge commit (`--no-ff`)** — never squash/fast-forward. Merge title representative + ends with the PR number, e.g. `Update Project Instructions to the Latest State (#47)`.
- Commits/PRs authored **`C - Furqon Aji Yudhistira <furqonajiy@gmail.com>`** (never "Claude").
- Each repo root has a sync marker `YYYY-MM-DD_HHMM.txt` (WIB): rename it to the current WIB timestamp on every update to that repo.
- `PROJECT_INSTRUCTIONS.md` (per repo) and this umbrella are updated **only when explicitly asked**.

## Per-repo capsules (load-bearing invariants — full detail in each repo's CLAUDE.md)

**telegram-bot** — Accept only `POST /webhook/{WEBHOOK_SECRET}` (else 404); require `TELEGRAM_CHAT_ID`; process only `update.message`. Legacy Markdown; `escapeForTg` is identity; wrap user values in backticks; `isValidBaseSku` = `^[A-Z0-9._\-/]+$`. `wrangler.toml` plain vars are load-bearing (`STOCK_SET_WORKFLOW=set.yml`, `STOCK_GET_WORKFLOW=get.yml`, `STOCK_BALANCE_WORKFLOW=balance.yml`, `SHOPEE_REPO`, `TIKTOKSHOP_REPO`, `STOCK_REPO`). Never add `.github/workflows` here. Commands: `/resi_shopee`, `/resi_tiktokshop`, `/resi_all`, `/stock_set SKU JUMLAH …`, `/stock_get SKU …`, `/stock_balance SKU … [dry]`.

**shopee-order-bot** — Statuses READY_TO_SHIP / PROCESSED; dedup by `order_sn`. `_is_ready_to_ship` needs `package_list` in `response_optional_fields`; `get_package_detail` param is `package_number_list`. `_pick_balance_sku`: model_sku → item_sku (no item_name). Token file has **no** `refresh_token_expires_at` (Shopee). Heartbeat label plain "Shopee". Shop-level HMAC-SHA256 signing. Ship to dropoff (`dropoff: {}`).

**tiktokshop-order-bot** — Track unit `package_id` (one order → many packages). Statuses AWAITING_SHIPMENT / AWAITING_COLLECTION. Token file **has** `refresh_token_expires_at`. Label `doc_url` downloaded **without** auth (pre-signed). All Open API endpoints are `202309`; `orders` key omitted entirely when empty. Open API signing + `shop_cipher`. Heartbeat label plain "TikTok Shop".

**stock-bot** — Golden rule: never lose stock. 50:50 split (Shopee `ceil(total/2)`, TikTok `floor`). Allocation lives only in `stock_allocator.py`: Shopee equal-share (no cap); TikTok smallest-first capped at `TIKTOKSHOP_MAX_UNITS_PER_VARIANT = 400`, overflow onto the largest variant, plus the `TIKTOKSHOP_1PCS_RESERVE_BASE_SKUS` exception. `parse_sku()` uppercases base_sku. `set.yml` SKU mode runs the price-aware `stock_set_price.py`. Labels `🟧 Shopee` / `🟦 TikTok Shop`. Product search `202502`, stock writes `202309`. Token files only on `bot-state` (no `processed_orders.json`).

**report-bot** — Offline CLI. Inputs `*BisaStok*.xlsx` / `*BisaJual*.xlsx`; output = 12-sheet workbook. Sisa stok = current-workbook ledger reconciled to Google Sheets `BisaRekapBarang`. HPP = Ocistok-priority weighted average. Pricing decision: `markup = (harga_sekarang − hpp_pricing) / hpp_pricing`, where `harga_sekarang` = lowest non-CoD unit price on the SKU's most recent selling day and `hpp_pricing` = latest overseas (`Luar Negeri?=1`) lot price (else `hpp_wa`); **profit/margin use `hpp_wa` + `harga_jual_avg`**. SKU `UPPER().strip()`; no dedup (only drop-Migrasi). CLI: `--sales [YEAR]`, `--reorder`, `--ab-test`, `--all`.
