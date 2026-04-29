# ITBisa Inventory Bot

Cross-platform inventory updater for Shopee Indonesia and TikTok Shop
Indonesia. Replaces the two separate `scripts/update_inventory.py`
files that previously lived in `itbisa-shopee-order-bot` and
`itbisa-tiktokshop-order-bot`.

## What it does

You give it ONE total stock count per SKU (in physical pieces), and it:

1. Splits the count 50:50 between Shopee and TikTok Shop. Odd totals
   give the +1 to Shopee.
2. On each platform, discovers all pack-size variants of that base
   SKU (e.g. `ITBISA-IC-NE555P-DIP8`, `25PCS-ITBISA-IC-NE555P-DIP8`,
   `500PCS-ITBISA-IC-NE555P-DIP8`).
3. Allocates the platform's share across those variants so the
   resulting unit counts multiply back to (or as close as possible to)
   the input piece count.
4. Pushes the result via each platform's inventory-update API.
5. Sends a single Bahasa Indonesia summary to Telegram.

### Worked example: 10,000 × ITBISA-IC-NE555P-DIP8

```
Excel:  ITBISA-IC-NE555P-DIP8 = 10000

Cross-platform 50:50:
  Shopee = 5000 pcs
  TikTok = 5000 pcs

Shopee (separate products per pack-size):
  ITBISA-IC-NE555P-DIP8       (×1)   ← 5000 units
  25PCS-ITBISA-IC-NE555P-DIP8 (×25)  ← (separate Excel row, separate run)

TikTok Shop (all variants under one product):
  Suppose variants are {1pc, 20pc, 500pc}, all on product P.
  share = 5000 // 3 = 1666 pcs/variant
    1pc:   1666 // 1   = 1666 units
    20pc:  1666 // 20  =   83 units (= 1660 pcs)
    500pc: 1666 // 500 =    3 units (= 1500 pcs)
  represented = 4826
  remainder = 174 → 174 extra units on smallest variant (1pc)
  Final 1pc = 1840
  Verify: 1840 + 1660 + 1500 = 5000 pcs ✓
  → ONE batched PUT carrying all three SKUs.
```

> **Important about Shopee pack-size variants.** On Shopee, pack-size
> variants live as **separate products** (separate `item_id`s), not as
> models under one item. The bot discovers them by parsing the leaf
> SKU, so `25PCS-ITBISA-IC-NE555P-DIP8` is automatically grouped with
> `ITBISA-IC-NE555P-DIP8` even though they are different Shopee
> products. **You only enter the base SKU in Excel.**

## Project structure

```text
itbisa-inventory-bot/
├── .github/workflows/
│   └── run.yml                          # workflow_dispatch only, no cron
├── data/                                # bot-state (token files only)
│   ├── shopee_tokens.json
│   └── tiktokshop_tokens.json
├── scripts/
│   ├── bootstrap_shopee_tokens.py       # one-time setup
│   ├── bootstrap_tiktokshop_tokens.py   # one-time setup
│   └── update_inventory.py              # CLI entry point
├── src/
│   ├── __init__.py
│   ├── main.py                          # orchestrator (Excel + single-SKU)
│   ├── config.py
│   ├── excel_reader.py
│   ├── inventory_allocator.py           # 50:50 split + pack-size math (pure)
│   ├── shopee_auth.py
│   ├── shopee_client.py
│   ├── telegram_sender.py
│   ├── tiktokshop_auth.py
│   └── tiktokshop_client.py
├── inventory.xlsx                       # operator's stock counts (gitignored)
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

## Excel format

One sheet, two columns. Header row text is ignored; column ORDER matters.

| SKU                          | Stock |
|------------------------------|-------|
| ITBISA-IC-NE555P-DIP8        | 10000 |
| ITBISA-LED-SUPERBRIGHT-5MM   | 3000  |
| ITBISA-BUBBLE-WRAP           | 0     |

Rules:
- Always provide the **base** SKU. The bot fans out to every pack-size
  variant on both platforms automatically.
- Rows starting with `<digits>PCS-` are **rejected** with a warning.
  The base SKU drives the rebalance.
- Empty rows: skipped silently.
- Non-integer or negative stock: skipped with a row-level warning.
- Duplicate SKU rows: last value wins, with a warning.

## Two ways to run

### Mode A — Excel (bulk)

Local:

```bash
python scripts/update_inventory.py inventory.xlsx
python scripts/update_inventory.py inventory.xlsx --dry-run
```

GitHub Actions: open the **Update Inventory** workflow, click **Run
workflow**, and (optionally) override `excel_path` and `dry_run`.

### Mode B — Single SKU

Local:

```bash
python scripts/update_inventory.py --sku ITBISA-IC-NE555P-DIP8 --pieces 10000
python scripts/update_inventory.py --sku ITBISA-IC-NE555P-DIP8 --pieces 10000 --dry-run
```

Telegram (after the Worker is wired — see "Telegram integration" below):

```
/update_inventory ITBISA-IC-NE555P-DIP8 10000
```

## Initial setup

### 1. Clone and install

```bash
conda create -n itbisa_inventory_bot python=3.11
conda activate itbisa_inventory_bot
cd C:\path\to\itbisa-inventory-bot
python -m pip install -r requirements.txt
```

### 2. Configure secrets

Copy `.env.example` to `.env` and fill in. Use the **same** Shopee
partner key and TikTok app secret as the order bots — but the token
files generated below are independent.

### 3. Bootstrap tokens (independent from the order bots)

The inventory bot maintains its own token chain on its own bot-state
branch. You authorize the shop once for this repo:

```bash
# Shopee: get AUTH_CODE from Shopee Open Platform Console → Authorize
python scripts/bootstrap_shopee_tokens.py <AUTH_CODE>

# TikTok Shop: get AUTH_CODE from Partner Center → app authorization
python scripts/bootstrap_tiktokshop_tokens.py <AUTH_CODE>
```

This writes `data/shopee_tokens.json` and `data/tiktokshop_tokens.json`.

### 4. Push to GitHub

```bash
git add .
git commit -m "Initial commit"
git push origin main
```

Then commit the `data/` files to a `bot-state` branch (the workflow
auto-creates it on first run if you skip this step).

### 5. Configure GitHub Secrets

Add the same secrets as in `.env.example`, plus `MAX_SKUS_PER_RUN`
(optional, default 500).

## State management (the `bot-state` branch)

This repo uses two branches, exactly like the order bots:

- `main` — source code.
- `bot-state` — the two `data/*_tokens.json` files only. No
  `processed_orders.json` (the inventory bot is not order-aware).

The workflow checks out `main`, overlays `data/` from `bot-state` if
the branch exists, runs the script, then commits any rotated tokens
back to `bot-state`. Token rotation only happens when the access
token was about to expire during the run, so most runs commit nothing.

`--dry-run` runs intentionally skip the bot-state commit.

### What you should NOT do

- Do not enable branch protection on `bot-state`. The bot writes there.
- Do not delete `bot-state`. The bot will recreate it but you will
  need to re-bootstrap the tokens.
- Do not manually edit token files on `bot-state` unless you are
  recovering from a failed refresh.

## Telegram integration

The Telegram bot Worker (`itbisa-shop-telegram-bot`) needs a new
`/update_inventory` command that calls `workflow_dispatch` on this
repo with `sku` and `pieces` inputs. See **Worker side** below for
the patch.

A user types in Telegram:

```
/update_inventory ITBISA-IC-NE555P-DIP8 10000
```

The Worker fires `workflow_dispatch` on this repo. The workflow runs
the script in single-SKU mode and sends a detailed allocation report
to the same Telegram chat:

```
📦 Update Inventory — Selesai

SKU: ITBISA-IC-NE555P-DIP8
Total: 10.000 pcs

Shopee — 5.000 pcs — ✅ berhasil
  • ITBISA-IC-NE555P-DIP8: 5000 unit (= 5000 pcs)

TikTok Shop — 5.000 pcs — ✅ berhasil
  • ITBISA-IC-NE555P-DIP8: 1840 unit (= 1840 pcs)
  • 20PCS-ITBISA-IC-NE555P-DIP8: 83 unit (= 1660 pcs)
  • 500PCS-ITBISA-IC-NE555P-DIP8: 3 unit (= 1500 pcs)
```

## Cost

Free forever. GitHub Actions free-tier minutes only; no scheduled cron;
each manual run is well under one minute of compute.

## Troubleshooting

**`Shopee token file not found`** — Run `bootstrap_shopee_tokens.py`
locally, commit `data/shopee_tokens.json` to the bot-state branch.

**`Otorisasi Shopee kadaluarsa`** in Telegram — Refresh token expired
(rare, ~30 days inactivity). Re-authorize in Shopee Open Platform
Console, re-run `bootstrap_shopee_tokens.py`, push to bot-state.

**SKU on Shopee but not TikTok (or vice versa)** — Bot skips with a
warning. Either publish the SKU on the missing platform, or update
that platform manually outside this bot.

**Allocation says `N pcs unrepresentable`** — The smallest variant on
that product is bigger than 1pc, and the leftover after rounding is
smaller than that smallest pack. Either accept the small loss, or
publish a 1pc variant.

## Important note

The `inventory_allocator.py` module is pure-math with no I/O. It is
the one piece of logic shared between platforms. If you ever need to
change the allocation algorithm (e.g. weighted split instead of 50:50),
that's the only file to touch — both clients call into it identically.
