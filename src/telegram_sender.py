"""
telegram_sender.py
------------------
Sends a Bahasa Indonesia summary of the stock-set / stock-get / stock-balance
run to the operator's Telegram chat. Same chat as the order bots, but a
different message shape — this is a transactional report, not an order label.

Public functions:
  send_run_summary(report)                  — bulk Excel run
  send_single_sku_summary(report)           — single-SKU /stock_set run
  send_stock_get_summary(report)            — single-SKU /stock_get run (read-only)
  send_stock_balance_summary(report)        — single-SKU /stock_balance run
  send_stock_balance_multi_summary(report)  — multi-SKU /stock_balance run
  send_alert(text)                          — error path

The `report` dicts are produced by main.py and have a fully-defined
shape — see the docstrings below. Keep this module dumb (formatting
only); main.py does the orchestration.
"""

from __future__ import annotations

import re

import requests

from src import config

_TELEGRAM_API = "https://api.telegram.org"
_MAX_MESSAGE_CHARS = 4000  # Telegram caps at 4096; leave headroom

# Platform glyphs used in the multi-SKU balance summary. Swap these for any
# emoji (including custom Telegram emoji codepoints) that better matches the
# Shopee / TikTok Shop brand — they are the only two spots that change visually.
SHOPEE_EMOJI = "🟧S"
TIKTOKSHOP_EMOJI = "🎵T"

# Used by the multi-summary to strip leading "SKU `XXX` " from a reason
# string so the SKU isn't repeated twice in its block.
_SKU_PREFIX_RE = re.compile(r"^SKU `[^`]+`\s+")


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
    header = "📦 *Set Stock* — DRY RUN" if report["dry_run"] else "📦 *Set Stock* — Selesai"

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
        lines.append("*Tidak ditemukan di Shopee & TikTok Shop:*")
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
        lines.append("_Dry run — tidak ada write API yang dipanggil._")

    _send(_join(lines))


def send_single_sku_summary(report: dict) -> None:
    """
    Single-SKU run (from /stock_set SKU AMOUNT).

    report = {
      "mode":              "single",
      "base_sku":          str,
      "total_pieces":      int,
      "shopee_pieces":     int,
      "tiktokshop_pieces": int,
      "shopee_lines":      list[str],
      "tiktokshop_lines":  list[str],
      "shopee_status":     str,
      "tiktokshop_status": str,
      "dry_run":           bool,
    }
    """
    header = "📦 *Set Stock* — DRY RUN" if report["dry_run"] else "📦 *Set Stock* — Selesai"

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
    lines.append(
        f"*TikTok Shop* — {_fmt_int(report['tiktokshop_pieces'])} pcs — "
        f"{report['tiktokshop_status']}"
    )
    lines.extend(report["tiktokshop_lines"] or ["  _(tidak ada varian)_"])

    if report["dry_run"]:
        lines.append("")
        lines.append("_Dry run — tidak ada write API yang dipanggil._")

    _send(_join(lines))


def send_stock_get_summary(report: dict) -> None:
    """
    Single-SKU read-only run (from /stock_get SKU).

    report = {
      "base_sku":            str,
      "shopee_variants":     list[dict],   # variants with stock_units + weight_grams
      "tiktokshop_variants": list[dict],   # same shape
    }

    Format: per-variant rows showing each platform's units + weight,
    a per-variant cross-platform total, then the grand totals.
    """
    base_sku = report["base_sku"]
    shopee = report["shopee_variants"]
    tiktokshop = report["tiktokshop_variants"]

    # Merge variants from both platforms keyed by raw_sku. A variant may
    # exist on only one side — we still show it so the operator notices
    # the catalog mismatch.
    unified: dict[str, dict] = {}
    for v in shopee:
        unified.setdefault(v["raw_sku"], {
            "raw_sku": v["raw_sku"],
            "multiplier": v["multiplier"],
            "shopee": None,
            "tiktokshop": None,
        })["shopee"] = v
    for v in tiktokshop:
        unified.setdefault(v["raw_sku"], {
            "raw_sku": v["raw_sku"],
            "multiplier": v["multiplier"],
            "shopee": None,
            "tiktokshop": None,
        })["tiktokshop"] = v

    rows = sorted(unified.values(), key=lambda r: r["multiplier"])

    lines = [
        "📊 *Stock Get* — Selesai",
        "",
        f"SKU dasar: `{base_sku}`",
        f"Ditemukan: {len(rows)} varian "
        f"(Shopee {len(shopee)}, TikTok Shop {len(tiktokshop)})",
    ]

    shopee_total_pcs = 0
    tiktokshop_total_pcs = 0

    for row in rows:
        s = row["shopee"]
        t = row["tiktokshop"]
        mult = row["multiplier"]

        lines.append("")
        lines.append(f"`{row['raw_sku']}` (×{mult})")

        if s is not None:
            s_pcs = s["stock_units"] * mult
            shopee_total_pcs += s_pcs
            lines.append(
                f"  • Shopee:      {_fmt_int(s['stock_units'])} unit, "
                f"berat {_fmt_weight(s['weight_grams'])}"
            )
        else:
            lines.append("  • Shopee:      _(tidak ada)_")

        if t is not None:
            t_pcs = t["stock_units"] * mult
            tiktokshop_total_pcs += t_pcs
            lines.append(
                f"  • TikTok Shop: {_fmt_int(t['stock_units'])} unit, "
                f"berat {_fmt_weight(t['weight_grams'])}"
            )
        else:
            lines.append("  • TikTok Shop: _(tidak ada)_")

        # Per-variant cross-platform total.
        s_units = s["stock_units"] if s else 0
        t_units = t["stock_units"] if t else 0
        total_units = s_units + t_units
        lines.append(
            f"  • Total varian: {_fmt_int(total_units)} unit "
            f"(= {_fmt_int(total_units * mult)} pcs)"
        )

    lines.append("")
    lines.append("*Ringkasan:*")
    lines.append(f"  • Shopee total:      {_fmt_int(shopee_total_pcs)} pcs")
    lines.append(f"  • TikTok Shop total: {_fmt_int(tiktokshop_total_pcs)} pcs")
    lines.append(f"  • Total gabungan:    {_fmt_int(shopee_total_pcs + tiktokshop_total_pcs)} pcs")

    _send(_join(lines))


def send_stock_balance_summary(report: dict) -> None:
    """
    Single-SKU rebalance run (from /stock_balance SKU).

    report = {
      "base_sku":                  str,
      "total_pieces":              int,
      "shopee_before_pieces":      int,
      "tiktokshop_before_pieces":  int,
      "shopee_after_pieces":       int,
      "tiktokshop_after_pieces":   int,
      "shopee_lines":              list[str],
      "tiktokshop_lines":          list[str],
      "shopee_status":             str,
      "tiktokshop_status":         str,
      "dry_run":                   bool,
    }

    Highlights the before/after delta so the operator can immediately
    see what changed. Per-variant push lines mirror send_single_sku_summary.
    """
    header = (
        "🔄 *Balance Stock* — DRY RUN"
        if report["dry_run"]
        else "🔄 *Balance Stock* — Selesai"
    )

    shopee_delta = report["shopee_after_pieces"] - report["shopee_before_pieces"]
    tiktokshop_delta = report["tiktokshop_after_pieces"] - report["tiktokshop_before_pieces"]

    lines = [
        header,
        "",
        f"SKU: `{report['base_sku']}`",
        f"Total: {_fmt_int(report['total_pieces'])} pcs (dipertahankan)",
        "",
        "*Sebelum → Sesudah:*",
        f"  • Shopee:      {_fmt_int(report['shopee_before_pieces'])} → "
        f"{_fmt_int(report['shopee_after_pieces'])} pcs ({_signed(shopee_delta)})",
        f"  • TikTok Shop: {_fmt_int(report['tiktokshop_before_pieces'])} → "
        f"{_fmt_int(report['tiktokshop_after_pieces'])} pcs ({_signed(tiktokshop_delta)})",
        "",
        f"*Shopee* — {report['shopee_status']}",
    ]
    lines.extend(report["shopee_lines"] or ["  _(tidak ada varian)_"])
    lines.append("")
    lines.append(f"*TikTok Shop* — {report['tiktokshop_status']}")
    lines.extend(report["tiktokshop_lines"] or ["  _(tidak ada varian)_"])

    if report["dry_run"]:
        lines.append("")
        lines.append("_Dry run — tidak ada write API yang dipanggil._")

    _send(_join(lines))


def send_stock_balance_multi_summary(report: dict) -> None:
    """
    Multi-SKU rebalance run (from /stock_balance with 2+ SKU, or order-bot
    auto-dispatch after /resi_*).

    ONE compact message at end-of-run. Per SKU:
      <status>  `SKU`
      <shopee>  before → after
      <tiktok>  before → after

    Skipped/failed SKUs collapse to status + SKU + short reason (no
    platform lines, since there's nothing to show).

    report = {
      "results": list[dict],   # one per SKU
      "dry_run": bool,
    }

    Each result dict:
      {
        "base_sku":                  str,
        "status":                    "ok" | "dry_run" | "skipped" | "failed",
        "reason":                    str,           # for skipped/failed
        "shopee_before_pieces":      int,
        "tiktokshop_before_pieces":  int,
        "shopee_after_pieces":       int,
        "tiktokshop_after_pieces":   int,
        # ...other fields exist but are not used in this compact view
      }
    """
    results = report["results"]
    dry_run = bool(report.get("dry_run", False))
    total = len(results)

    ok_count = sum(1 for r in results if r["status"] in ("ok", "dry_run"))
    skip_count = sum(1 for r in results if r["status"] == "skipped")
    fail_count = sum(1 for r in results if r["status"] == "failed")

    suffix = " — DRY RUN" if dry_run else " — Selesai"
    header = f"🔄 *Balance Stock*{suffix} ({total} SKU)"

    lines = [header, ""]

    for r in results:
        sku = r["base_sku"]
        status = r["status"]
        if status in ("ok", "dry_run"):
            icon = "🔍" if status == "dry_run" else "✅"
            sh_b = _fmt_int(r["shopee_before_pieces"])
            sh_a = _fmt_int(r["shopee_after_pieces"])
            tt_b = _fmt_int(r["tiktokshop_before_pieces"])
            tt_a = _fmt_int(r["tiktokshop_after_pieces"])
            lines.append(f"{icon} `{sku}`")
            lines.append(f"{SHOPEE_EMOJI} {sh_b} → {sh_a}")
            lines.append(f"{TIKTOKSHOP_EMOJI} {tt_b} → {tt_a}")
        elif status == "skipped":
            short = _strip_sku_prefix(r["reason"])
            lines.append(f"⏭️ `{sku}`")
            lines.append(f"   {_truncate(short, 200)}")
        else:  # failed
            short = _strip_sku_prefix(r["reason"])
            lines.append(f"❌ `{sku}`")
            lines.append(f"   {_truncate(short, 200)}")
        lines.append("")  # blank line between SKU blocks

    # Drop trailing blank before the Ringkasan footer.
    if lines and lines[-1] == "":
        lines.pop()

    lines.append("")
    summary_parts: list[str] = []
    if ok_count:
        summary_parts.append(f"{ok_count} ✅")
    if skip_count:
        summary_parts.append(f"{skip_count} ⏭️")
    if fail_count:
        summary_parts.append(f"{fail_count} ❌")
    lines.append("*Ringkasan:* " + " | ".join(summary_parts))

    if dry_run:
        lines.append("")
        lines.append("_Dry run — tidak ada write API yang dipanggil._")

    _send(_join(lines))


def send_alert(text: str) -> None:
    """One-off error alert, for example refresh token expired or file not found."""
    _send(f"🚨 *Set Stock* — Error\n\n{text}")


# ============================================================
# Internals
# ============================================================

def _send(text: str) -> None:
    """POST sendMessage with Markdown parse mode. Errors are non-fatal."""
    if len(text) > _MAX_MESSAGE_CHARS:
        text = text[:_MAX_MESSAGE_CHARS - 50] + "\n\n_(pesan dipotong)_"

    url = f"{_TELEGRAM_API}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    body = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
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


def _fmt_weight(grams: int) -> str:
    """0 → '—' (data tidak tersedia), else 'NNN g' with thousands separator."""
    if not grams:
        return "—"
    return f"{_fmt_int(grams)} g"


def _signed(n: int) -> str:
    """Signed integer with Indonesian thousands separator; '±0' for zero."""
    if n > 0:
        return f"+{_fmt_int(n)}"
    if n < 0:
        return _fmt_int(n)
    return "±0"


def _strip_sku_prefix(reason: str) -> str:
    """
    Strip leading 'SKU `XXX` ' from a reason string when present, so the
    multi-summary doesn't repeat the SKU on the same line as its label.
    No-op for reasons that don't start with that pattern (e.g. partial-
    failure reasons that begin with 'Shopee ❌ gagal: ...').
    """
    return _SKU_PREFIX_RE.sub("", reason, count=1)