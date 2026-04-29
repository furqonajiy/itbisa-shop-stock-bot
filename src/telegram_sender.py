"""
telegram_sender.py
------------------
Sends a Bahasa Indonesia summary of the inventory run to the operator's
Telegram chat. Same chat as the order bots, but a different message
shape — this is a transactional report, not an order label.

Public functions:
  send_run_summary(report)          — bulk Excel run
  send_single_sku_summary(report)   — single-SKU /update_inventory run
  send_alert(text)                  — error path (e.g., RT expired)

The `report` dicts are produced by main.py and have a fully-defined
shape — see the docstrings below. Keep this module dumb (formatting
only); main.py does the orchestration.
"""

from __future__ import annotations

import requests

from src import config


_TELEGRAM_API = "https://api.telegram.org"
_MAX_MESSAGE_CHARS = 4000  # Telegram caps at 4096; leave headroom


# ============================================================
# Public surface
# ============================================================

def send_run_summary(report: dict) -> None:
    """
    Bulk Excel run.

    report = {
      "mode":             "excel",
      "excel_path":       str,
      "total_skus":       int,
      "succeeded":        int,
      "skipped_missing":  list[str],   # SKU not on either platform
      "skipped_one_side": list[tuple[str, str]],  # (sku, platform_present)
      "failed":           list[tuple[str, str]],  # (sku, error_msg)
      "dry_run":          bool,
    }
    """
    header = "📦 *Update Inventory* — DRY RUN" if report["dry_run"] else "📦 *Update Inventory* — Selesai"

    lines = [
        header,
        "",
        f"📁 File: `{report['excel_path']}`",
        f"📊 Total SKU di Excel: {report['total_skus']}",
        f"✅ Berhasil: {report['succeeded']}",
        f"⏭️ Dilewati (tidak ditemukan): {len(report['skipped_missing'])}",
        f"⏭️ Dilewati (hanya 1 platform): {len(report['skipped_one_side'])}",
        f"❌ Gagal: {len(report['failed'])}",
    ]

    if report["skipped_missing"]:
        lines.append("")
        lines.append("*Tidak ditemukan di Shopee & TikTok:*")
        for sku in report["skipped_missing"][:20]:
            lines.append(f"  • `{sku}`")
        if len(report["skipped_missing"]) > 20:
            lines.append(f"  ...dan {len(report['skipped_missing']) - 20} lainnya")

    if report["skipped_one_side"]:
        lines.append("")
        lines.append("*Hanya ada di 1 platform (dilewati):*")
        for sku, platform in report["skipped_one_side"][:20]:
            lines.append(f"  • `{sku}` (hanya di {platform})")
        if len(report["skipped_one_side"]) > 20:
            lines.append(f"  ...dan {len(report['skipped_one_side']) - 20} lainnya")

    if report["failed"]:
        lines.append("")
        lines.append("*Gagal (cek manual):*")
        for sku, err in report["failed"][:10]:
            lines.append(f"  • `{sku}`: {_truncate(err, 120)}")
        if len(report["failed"]) > 10:
            lines.append(f"  ...dan {len(report['failed']) - 10} lainnya")

    if report["dry_run"]:
        lines.append("")
        lines.append("_Dry run — tidak ada API yang dipanggil._")

    _send(_join(lines))


def send_single_sku_summary(report: dict) -> None:
    """
    Single-SKU run (from /update_inventory SKU AMOUNT).

    report = {
      "mode":         "single",
      "base_sku":     str,
      "total_pieces": int,
      "shopee_pieces":  int,
      "tiktok_pieces":  int,
      "shopee_lines":   list[str],   # already-formatted "  • SKU: 5000 (= 5000 pcs)"
      "tiktok_lines":   list[str],
      "shopee_status":  str,         # "✅ berhasil" | "⏭️ dilewati: ..." | "❌ gagal: ..."
      "tiktok_status":  str,
      "dry_run":      bool,
    }
    """
    header = "📦 *Update Inventory* — DRY RUN" if report["dry_run"] else "📦 *Update Inventory* — Selesai"

    lines = [
        header,
        "",
        f"SKU: `{report['base_sku']}`",
        f"Total: {_fmt_int(report['total_pieces'])} pcs",
        "",
        f"*Shopee* — {_fmt_int(report['shopee_pieces'])} pcs — {report['shopee_status']}",
    ]
    lines.extend(report["shopee_lines"] or ["  _(tidak ada varian)_"])
    lines.append("")
    lines.append(f"*TikTok Shop* — {_fmt_int(report['tiktok_pieces'])} pcs — {report['tiktok_status']}")
    lines.extend(report["tiktok_lines"] or ["  _(tidak ada varian)_"])

    if report["dry_run"]:
        lines.append("")
        lines.append("_Dry run — tidak ada API yang dipanggil._")

    _send(_join(lines))


def send_alert(text: str) -> None:
    """One-off error alert (e.g., refresh token expired, file not found)."""
    _send(f"🚨 *Update Inventory* — Error\n\n{text}")


# ============================================================
# Internals
# ============================================================

def _send(text: str) -> None:
    """POST sendMessage with Markdown parse mode. Errors are non-fatal:
    a Telegram outage shouldn't make the inventory run fail."""
    if len(text) > _MAX_MESSAGE_CHARS:
        text = text[:_MAX_MESSAGE_CHARS - 50] + "\n\n_(pesan dipotong)_"

    url = f"{_TELEGRAM_API}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    body = {
        "chat_id":    config.TELEGRAM_CHAT_ID,
        "text":       text,
        "parse_mode": "Markdown",
    }
    try:
        response = requests.post(url, json=body, timeout=15)
        response.raise_for_status()
    except Exception as e:
        # Telegram failures should never crash the run.
        print(f"  [telegram] Failed to send summary: {e}")


def _join(lines: list[str]) -> str:
    return "\n".join(lines)


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def _fmt_int(n: int) -> str:
    """1234567 -> '1.234.567' (Indonesian thousands separator)."""
    return f"{n:,}".replace(",", ".")
