# ITBisa Shop Stock Bot

Cross-platform stock tooling for Shopee Indonesia and TikTok Shop Indonesia.

This repo is used by the Telegram stock commands and GitHub Actions workflows to:

- read stock (`/stock_get`)
- set stock to a requested total (`/stock_set`)
- rebalance existing stock while preserving the current total (`/stock_balance`)

The bot runs as short-lived GitHub Actions jobs. There is no server, VM, database,
shared runtime, or cron in this repo.

Runtime token files live on the `bot-state` branch. Source code lives on `main`.

## Commands and workflows

### `/stock_get SKU`

Read-only stock inspection for one base SKU.

```text
Telegram /stock_get SKU
  -> Cloudflare Worker workflow_dispatch
  -> GitHub Actions .github/workflows/get.yml
  -> scripts/stock_get.py --sku SKU
  -> src/stock_get_compact.py
  -> Shopee API + TikTok Shop API (read-only)
  -> Telegram summary
  -> refreshed token files committed to bot-state
```

Use a base SKU only. Do not pass pack-size variants like
`20PCS-ITBISA-IC-NE555P-DIP8`.

### `/stock_set SKU PIECES`

Sets one or more base SKUs to a requested total physical stock count.

```text
Telegram /stock_set SKU PIECES
  -> Cloudflare Worker workflow_dispatch
  -> GitHub Actions .github/workflows/set.yml
  -> scripts/stock_set_price.py --sku SKU --pieces PIECES
  -> src/stock_set_price_rule.py
  -> Shopee API + TikTok Shop API
  -> Telegram summary
  -> refreshed token files committed to bot-state
```

Multi-SKU mode is supported by passing space-separated parallel lists:

```text
sku="SKU1 SKU2 SKU3"
pieces="100 200 300"
```

The workflow intentionally keeps `$INPUT_SKU` and `$INPUT_PIECES` unquoted when
calling `scripts/stock_set_price.py` so shell word-splitting feeds argparse's
multi-value parsing.

### `/stock_set` Excel mode

Excel mode is still supported and intentionally uses the older Excel path:

```text
.github/workflows/set.yml
  -> scripts/stock_set.py "$INPUT_EXCEL_PATH"
  -> src.main.run_excel_mode
```

Excel mode is intentionally **not** price-aware and does **not** apply the
TikTok Shop low-price 1PCS rule unless explicitly changed later.

Excel file format:

| Column | Meaning                     |
|--------|-----------------------------|
| A      | Base SKU                    |
| B      | Total physical stock pieces |

### `/stock_balance SKU`

Rebalances the existing combined stock between Shopee and TikTok Shop while
preserving the current total.

```text
Telegram /stock_balance SKU
  -> Cloudflare Worker workflow_dispatch
  -> GitHub Actions .github/workflows/balance.yml
  -> scripts/stock_balance.py --sku SKU
  -> src/stock_balance_delta_summary.py
  -> src/stock_balance_price_rule.py
  -> Shopee API + TikTok Shop API
  -> Telegram summary
  -> refreshed token files committed to bot-state
```

Multiple base SKUs may be passed as a space-separated list.

## SKU conventions

Base SKUs generally follow:

```text
ITBISA-[CATEGORY]-[DESCRIPTION]
```

Pack-size variants prepend the pack size:

```text
ITBISA-IC-NE555P-DIP8
10PCS-ITBISA-IC-NE555P-DIP8
50PCS-ITBISA-IC-NE555P-DIP8
1000PCS-ITBISA-IC-NE555P-DIP8
```

Commands should receive the **base SKU**. The bot discovers pack-size variants
from each marketplace catalog.

## Pieces vs units

The bot distinguishes physical pieces from marketplace units:

```text
pieces = physical stock count
units  = variant quantity pushed to marketplace
variant physical stock = units * multiplier
```

Example:

```text
20PCS-ITBISA-IC-ULN2803APG-DIP18
3 units = 60 pcs
```

## Stock allocation rules

### Platform split

For `/stock_set SKU TOTAL`, the requested total is split 50:50:

```text
Shopee target      = ceil(total / 2)
TikTok Shop target = floor(total / 2)
```

Shopee receives the odd remainder.

For `/stock_balance SKU`, the bot first reads the current Shopee + TikTok Shop
stock, preserves that combined total, then applies the same target split.

### Shopee

Shopee can receive leftover stock that TikTok Shop cannot represent due to
pack-size constraints.

Shopee does not use the TikTok Shop low-price 1PCS cap.

### TikTok Shop pack-size allocation

TikTok Shop variants live under one product and are allocated by pack size.

The bot allocates TikTok Shop target pieces into available pack-size variants.
If TikTok Shop cannot represent its target exactly, the unrepresentable leftover
is assigned to Shopee so the total stock is preserved.

Do not describe this as lost stock. It is stock that is unrepresentable by the
available TikTok Shop pack sizes and is therefore moved to Shopee.

## Price-aware TikTok Shop low-price 1PCS rule

The TikTok Shop low-price rule is price-based, not hardcoded SKU-based.

It applies to:

- `/stock_balance`
- `/stock_set` SKU mode through `scripts/stock_set_price.py`

It does **not** apply to Excel mode.

Rule:

```text
If TikTok Shop 1PCS variant price < Rp5.000:
  cap TikTok Shop 1PCS stock to max 1 unit
  allocate the remaining TikTok target to other pack-size variants
  assign any TikTok unrepresentable leftover to Shopee
```

Example:

```text
/stock_set ITBISA-IC-ULN2803APG-DIP18 18

Initial target split:
Shopee      = 9 pcs
TikTok Shop = 9 pcs

TikTok Shop variants:
1PCS, 20PCS, 1000PCS

TikTok Shop 1PCS price = Rp2.199, below Rp5.000

TikTok Shop allocation:
1PCS    = 1 unit = 1 pc
20PCS   = 0 unit = 0 pcs
1000PCS = 0 unit = 0 pcs

TikTok Shop represented = 1 pc
TikTok unrepresentable leftover = 8 pcs
Final Shopee = 17 pcs
Final TikTok Shop = 1 pc
Total preserved = 18 pcs
```

## Product detail metadata

### TikTok Shop

TikTok Shop product detail uses API version `202309`:

```text
GET /product/202309/products/{product_id}
```

The current detail fields used by the bot are:

```text
data.skus[].sku_weight
data.skus[].price.sale_price
```

For each SKU:

- weight is read from `sku_weight`
- price is read from `price.sale_price`
- SKU `package_weight` is only a fallback
- product-level `package_weight` is only a fallback when SKU weight is missing

The bot does not derive TikTok Shop weight from pack size or multiplier.
If API/catalog weight is missing, Telegram shows `—`.

### Shopee

Shopee catalog already provides weight in kilograms, which the bot converts to
grams.

Shopee price is enriched best-effort for Telegram summaries through read-only
Shopee detail calls. If Shopee does not expose a parseable price field for a
variant, the row is still shown and the price suffix is omitted.

## Telegram summary format

All single-SKU stock summaries use the same platform labels:

```text
🟧 Shopee
🟦 TikTok Shop
```

Detail rows use the same shape when metadata is available:

```text
• PACK: UNITS unit = PIECES pcs — WEIGHT — PRICE
```

Examples:

```text
• 1PCS: 17 unit = 17 pcs — 2 g — Rp2.199
• 20PCS: 0 unit = 0 pcs — 40 g — Rp43.999
```

If weight is missing:

```text
• 1PCS: 17 unit = 17 pcs — — — Rp2.199
```

If price is missing, the price suffix is omitted:

```text
• 1PCS: 17 unit = 17 pcs — 2 g
```

### `/stock_get` example

```text
📊 Stock Get — Selesai

✅ ITBISA-IC-ULN2803APG-DIP18
Ditemukan: 3 varian (🟧 Shopee 1, 🟦 TikTok Shop 3)

📊 Ringkas
🟧 Shopee total: 17 pcs
🟦 TikTok Shop total: 1 pcs
Total gabungan: 18 pcs

📦 Detail
🟧 Shopee
• 1PCS: 17 unit = 17 pcs — 2 g — Rp2.199

🟦 TikTok Shop
• 1PCS: 1 unit = 1 pcs — 2 g — Rp2.199
• 20PCS: 0 unit = 0 pcs — 40 g — Rp43.999
• 1000PCS: 0 unit = 0 pcs — 2.001 g — Rp2.100.000
```

### `/stock_set` example

```text
📦 Set Stock — Selesai

✅ ITBISA-IC-ULN2803APG-DIP18
Total: 18 pcs

📊 Ringkas
🟧 Shopee 17 pcs — ✅ berhasil
🟦 TikTok Shop 1 pcs — ✅ berhasil

📦 Detail
🟧 Shopee
• 1PCS: 17 unit = 17 pcs — 2 g — Rp2.199

🟦 TikTok Shop
• 1PCS: 1 unit = 1 pcs — 2 g — Rp2.199
• 20PCS: 0 unit = 0 pcs — 40 g — Rp43.999
• 1000PCS: 0 unit = 0 pcs — 2.001 g — Rp2.100.000
```

### `/stock_balance` example

```text
🔄 Balance Stock — Selesai

✅ ITBISA-IC-ULN2803APG-DIP18
Total tetap: 18 pcs

📊 Ringkas
🟧 Shopee 17 → 17
🟦 TikTok Shop 1 → 1

📦 Detail
🟧 Shopee
• 1PCS: 17 unit = 17 pcs — 2 g — Rp2.199

🟦 TikTok Shop
• 1PCS: 1 unit = 1 pcs — 2 g — Rp2.199
• 20PCS: 0 unit = 0 pcs — 40 g — Rp43.999
• 1000PCS: 0 unit = 0 pcs — 2.001 g — Rp2.100.000
```

## Run modes

### Stock get

```bash
python scripts/stock_get.py --sku ITBISA-IC-ULN2803APG-DIP18
```

### Stock set SKU mode

```bash
python scripts/stock_set_price.py --sku ITBISA-IC-ULN2803APG-DIP18 --pieces 18
python scripts/stock_set_price.py --sku ITBISA-IC-ULN2803APG-DIP18 --pieces 18 --dry-run
```

Multi-SKU:

```bash
python scripts/stock_set_price.py --sku SKU1 SKU2 --pieces 100 200
```

### Stock set Excel mode

```bash
python scripts/stock_set.py stock.xlsx
python scripts/stock_set.py stock.xlsx --dry-run
```

### Stock balance

```bash
python scripts/stock_balance.py --sku ITBISA-IC-ULN2803APG-DIP18
python scripts/stock_balance.py --sku ITBISA-IC-ULN2803APG-DIP18 --dry-run
```

Multi-SKU:

```bash
python scripts/stock_balance.py --sku SKU1 SKU2 SKU3
```

## GitHub Actions

Current workflows:

```text
.github/workflows/get.yml      # /stock_get, read-only
.github/workflows/set.yml      # /stock_set, write
.github/workflows/balance.yml  # /stock_balance, write
```

All workflows:

- checkout `main`
- overlay `data/` from `bot-state` when available
- run Python 3.11
- install `requirements.txt`
- run the relevant script
- commit refreshed token files back to `bot-state`

Token rotation can happen even during read-only or dry-run catalog reads, so
workflows persist token files after every run.

Concurrency:

```text
stock-get      cancel-in-progress: true
stock-set      cancel-in-progress: false
stock-balance  cancel-in-progress: false
```

`stock-set` and `stock-balance` do not cancel in-progress runs because cancelling
mid-write could leave a partially applied batch.

## Required secrets

Shopee:

```text
SHOPEE_PARTNER_ID
SHOPEE_PARTNER_KEY
SHOPEE_SHOP_ID
```

TikTok Shop:

```text
TIKTOKSHOP_APP_KEY
TIKTOKSHOP_APP_SECRET
TIKTOKSHOP_SHOP_ID
```

Telegram:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

## Behaviour constants

These values live in `src/config.py`, not in GitHub Secrets:

```python
TIKTOKSHOP_MAX_UNITS_PER_VARIANT = 400   # per-variant unit cap (TikTok Shop allocator)
DELAY_BETWEEN_CALLS_SECONDS = 1.0
MAX_SKUS_PER_RUN = 500
```

Change them by editing `src/config.py` and committing the change to `main`.

## Token/state files

Runtime state belongs on `bot-state`.

Expected mutable files:

```text
data/shopee_tokens.json
data/tiktokshop_tokens.json
```

This stock bot does **not** need `processed_orders.json`.

Do not commit live token files to `main`. Bootstrap scripts can create local
token files, and workflows overlay/persist runtime copies through `bot-state`.

## API hosts

API base URLs live in `src/config.py`:

```text
SHOPEE_API_BASE_URL
TIKTOKSHOP_AUTH_BASE_URL
TIKTOKSHOP_OPEN_API_BASE_URL
```

Do not hardcode API hosts inside client modules.

## Safety behaviour

The bot:

- aborts before API writes when Excel row count exceeds `MAX_SKUS_PER_RUN`
- skips and reports a SKU that is missing on both platforms
- skips and reports `/stock_set` or `/stock_balance` when the SKU exists only on one platform
- allows `/stock_get` to report a SKU that exists only on one platform
- reports platform API update failures in Telegram
- keeps Shopee and TikTok Shop API writes independent per SKU, so one platform failure does not prevent the other platform attempt for that SKU
- sets absolute stock units per variant, not deltas
