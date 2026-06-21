"""
stock_low.py
------------
CLI entry for /stock_low — list every base SKU whose combined on-hand stock
(Shopee + TikTok Shop) is below config.LOW_STOCK_THRESHOLD (default 50 pcs).

Read-only: no write APIs are called. The only state changes are token rotation
and the throttle timestamp, both committed to bot-state by the workflow. The
report is throttled to once per config.LOW_STOCK_MIN_INTERVAL_HOURS (24h).

Triggered by:
  • /stock_low from the Telegram bot Worker
  • Manual workflow_dispatch on .github/workflows/low.yml
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    # Deferred import: config.py validates env vars at import time.
    from src.low_stock import run_stock_low_mode

    return run_stock_low_mode()


if __name__ == "__main__":
    sys.exit(main())
