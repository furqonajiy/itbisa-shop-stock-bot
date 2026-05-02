# ITBisa Shop Stock Bot

Cross-platform stock setter for Shopee Indonesia and TikTok Shop Indonesia.

This repo replaces the stock-update logic that previously lived separately
inside the Shopee and TikTok Shop order bots. It is designed to run once from
GitHub Actions, update stock, persist refreshed token files to `bot-state`, send
Telegram output, then exit.

## Real flow

There is no server, VM, database, shared runtime, or cron in this repo.

The normal flow is:

```text
Telegram /stock_set SKU PIECES
  -> Cloudflare Worker workflow_dispatch
  -> GitHub Actions .github/workflows/run.yml
  -> scripts/stock_set.py --sku SKU --pieces PIECES
  -> src/main.py
  -> Shopee API + TikTok Shop API
  -> Telegram summary to the same chat
  -> refreshed token files committed to bot-state
```

Manual GitHub Actions dispatch is also supported:

- Fill both `sku` and `pieces` for single-SKU mode.
- Leave `sku` and `pieces` empty for Excel mode.
- Excel mode expects `excel_path` to already exist in the checked-out workspace.
  The dispatch form does not upload files.

`main` is source code only. Runtime token files belong on `bot-state`.

## What it does

You provide **one total physical stock count** for a base SKU.

Example:

```text
ITBISA-LED-5MM = 4000 pcs
```

The bot then:

1. Splits the total 50:50 between Shopee and TikTok Shop.
   - Odd total: Shopee receives the extra +1 piece.
2. Finds all pack-size variants of the base SKU on each platform.
   - Base SKU: `ITBISA-LED-5MM`
   - Pack variants: `20PCS-ITBISA-LED-5MM`, `100PCS-ITBISA-LED-5MM`, etc.
3. Allocates each platform share into absolute stock units per variant.
4. Pushes stock through each platform API.
5. Sends a Bahasa Indonesia summary to Telegram.
6. Saves rotated token files immediately during the run and commits them back to
   `bot-state` after a successful non-dry-run workflow step.

## Important stock allocation rules

### Platform split

The input is total warehouse stock in physical pieces.

```text
Shopee share      = ceil(total / 2)
TikTok Shop share = floor(total / 2)
```

Shopee receives the odd extra piece because Shopee is more likely to have a
representable 1-piece path.

### Shopee

Shopee variants can be separate products, so the bot does not apply the TikTok
Shop one-order reserve logic.

Shopee allocation is an unconstrained/equal-share allocation:

```text
platform pieces ÷ number of variants
```

Any remainder is absorbed by the smallest multiplier variant when it can be
represented.

### TikTok Shop

TikTok Shop variants live under one product. A buyer may be blocked by the max
quantity allowed per variant in one order. Because of that, putting too much
stock into `1PCS` can reduce the maximum quantity a buyer can purchase in a
single order.

The bot uses an **order-aware TikTok Shop allocation**:

1. Keep a small physical stock reserve on the smallest pack-size variant.
2. Push the remaining stock to the largest pack-size variant.
3. Use smaller variants only to absorb leftovers that cannot fit into the
   largest pack size.

Default reserve is a code constant in `src/config.py`, not an env var:

```python
TIKTOKSHOP_SMALL_PACK_RESERVE_PIECES = 200
```

This reserve is in **physical pieces**, not units per variant.

## Worked example

Input stock:

```text
Total warehouse stock = 4000 pcs
Shopee share          = 2000 pcs
TikTok Shop share     = 2000 pcs
TikTok Shop variants  = 1PCS and 100PCS
Reserve               = 200 pcs
```

Old TikTok Shop result:

```text
1000 x 1PCS   = 1000 pcs
10 x 100PCS   = 1000 pcs
Total         = 2000 pcs
```

Problem: if a buyer can only select limited units per variant in one order, the
extra `1PCS` stock does not help large orders.

Current TikTok Shop result:

```text
200 x 1PCS    = 200 pcs
18 x 100PCS   = 1800 pcs
Total         = 2000 pcs
```

This keeps `1PCS` available for small buyers while making large orders much more
possible through the `100PCS` variant.

## Run modes

### Single-SKU mode

```bash
python scripts/stock_set.py --sku ITBISA-LED-5MM --pieces 4000
python scripts/stock_set.py --sku ITBISA-LED-5MM --pieces 4000 --dry-run
```

This is the mode expected from the Telegram Worker command:

```text
/stock_set SKU PIECES
```

Use a **base SKU only**. Do not pass a pack-size variant such as
`20PCS-ITBISA-LED-5MM`.

### Excel mode

```bash
python scripts/stock_set.py stock.xlsx
python scripts/stock_set.py stock.xlsx --dry-run
```

Excel format:

| Column | Meaning |
| --- | --- |
| A | Base SKU |
| B | Total physical stock pieces |

Excel mode is for deliberate bulk runs. Variant SKUs like `20PCS-BASESKU` are
skipped with a warning because the bot automatically fans out from the base SKU
to all discovered platform variants.

## GitHub Actions

Workflow:

```text
.github/workflows/run.yml
```

Behavior:

- No cron.
- Deliberate `workflow_dispatch` only.
- Checkout `main` as source code.
- Overlay `data/` from `bot-state` when the branch exists.
- Run `scripts/stock_set.py`.
- Commit refreshed token files back to `bot-state` after non-dry-run runs.
- Concurrency group: `stock-set`.
- `cancel-in-progress: true`, because the latest stock instruction should win.

The workflow selects mode like this:

```text
sku + pieces present -> single-SKU mode
otherwise            -> Excel mode using excel_path
```

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

These values live in `src/config.py`, not in `.env` and not in GitHub Secrets:

```python
TIKTOKSHOP_SMALL_PACK_RESERVE_PIECES = 200
TIKTOKSHOP_MAX_UNITS_PER_VARIANT = 200  # legacy compatibility constant only
MAX_SKUS_PER_RUN = 500
```

Change them by editing `src/config.py` and committing the change to `main`.
Do not configure them as workflow secrets.

## Token/state files

Runtime state belongs on `bot-state`.

Expected mutable files:

```text
data/shopee_tokens.json
data/tiktokshop_tokens.json
```

This stock bot does **not** need `processed_orders.json`.

Do not commit live token files to `main`. Bootstrap scripts can create local
token files, and the workflow overlays/persists the runtime copies through
`bot-state`.

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

- Aborts before API writes when Excel row count exceeds `MAX_SKUS_PER_RUN`.
- Skips and reports a SKU that is missing on both platforms.
- Skips and reports a SKU that exists only on one platform.
- Reports platform API update failures in Telegram.
- Keeps Shopee and TikTok Shop API writes independent per SKU, so one platform
  failure does not prevent the other platform attempt for that SKU.

The bot sets absolute stock units per variant, not deltas.
