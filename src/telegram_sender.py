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

# Platform display labels. Used everywhere a platform name appears in a
# Telegram message — section headers, list rows, totals, balance summaries.
# Changing these here updates every command (/stock_set, /stock_get,
# /stock_balance) consistently.
SHOPEE_LABEL = "🟧Shopee"
TIKTOKSHOP_LABEL = "♪TikTok Shop"

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
      "skipped_missing":  list[str],
      "skipped_one_side": list[tuple[str, str]],
      "failed":           list[tuple[str, str]],
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
        lines.append(f"*Tidak ditemukan di {SHOPEE_LABEL} & {TIKTOKSHOP_LABEL}:*")
        for sku in report["skipped_missing"][:20]:
            lines.append(f"• `{sku}`")
        if len(report["skipped_missing"]) > 20:
            lines.append(f"...dan {len(report['skipped_missing']) - 20} lainnya")

    if report["skipped_one_side"]:
        lines.append("")
        lines.append("*Hanya ada di 1 platform (dilewati):*")
        for sku, platform in report["skipped_one_side"][:20]:
            platform_label = _label_for_platform(platform)
            lines.append(f"• `{sku}` (hanya di {platform_label})")
        if len(report["skipped_one_side"]) > 20:
            lines.append(f"...dan {len(report['skipped_one_side']) - 20} lainnya")

    if report["failed"]:
        lines.append("")
        lines.append("*Gagal (cek manual):*")
        for sku, err in report["failed"][:10]:
            lines.append(f"• `{sku}`: {_truncate(err, 120)}")
        if len(report["failed"]) > 10:
            lines.append(f"...dan {len(report['failed']) - 10} lainnya")

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
        f"*{SHOPEE_LABEL}* — {_fmt_int(report['shopee_pieces'])} pcs — {report['shopee_status']}",
    ]
    lines.extend(report["shopee_lines"] or ["_(tidak ada varian)_"])
    lines.append("")
    lines.append(
        f"*{TIKTOKSHOP_LABEL}* — {_fmt_int(report['tiktokshop_pieces'])} pcs — "
        f"{report['tiktokshop_status']}"
    )
    lines.extend(report["tiktokshop_lines"] or ["_(tidak ada varian)_"])

    if report["dry_run"]:
        lines.append("")
        lines.append("_Dry run — tidak ada write API yang dipanggil._")

    _send(_join(lines))


def send_stock_get_summary(report: dict) -> None:
    """
    Single-SKU read-only run (from /stock_get SKU).

    report = {
      "base_sku":            str,
      "shopee_variants":     list[dict],
      "tiktokshop_variants": list[dict],
    }
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
        f"({SHOPEE_LABEL} {len(shopee)}, {TIKTOKSHOP_LABEL} {len(tiktokshop)})",
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
                f"{SHOPEE_LABEL}: {_fmt_int(s['stock_units'])} unit, "
                f"berat {_fmt_weight(s['weight_grams'])}"
            )
        else:
            lines.append(f"{SHOPEE_LABEL}: _(tidak ada)_")

        if t is not None:
            t_pcs = t["stock_units"] * mult
            tiktokshop_total_pcs += t_pcs
            lines.append(
                f"{TIKTOKSHOP_LABEL}: {_fmt_int(t['stock_units'])} unit, "
                f"berat {_fmt_weight(t['weight_grams'])}"
            )
        else:
            lines.append(f"{TIKTOKSHOP_LABEL}: _(tidak ada)_")

        # Per-variant cross-platform total.
        s_units = s["stock_units"] if s else 0
        t_units = t["stock_units"] if t else 0
        total_units = s_units + t_units
        lines.append(
            f"Total varian: {_fmt_int(total_units)} unit "
            f"(= {_fmt_int(total_units * mult)} pcs)"
        )

    lines.append("")
    lines.append("*Ringkasan:*")
    lines.append(f"{SHOPEE_LABEL} total: {_fmt_int(shopee_total_pcs)} pcs")
    lines.append(f"{TIKTOKSHOP_LABEL} total: {_fmt_int(tiktokshop_total_pcs)} pcs")
    lines.append(f"Total gabungan: {_fmt_int(shopee_total_pcs + tiktokshop_total_pcs)} pcs")

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
        f"{SHOPEE_LABEL}: {_fmt_int(report['shopee_before_pieces'])} → "
        f"{_fmt_int(report['shopee_after_pieces'])} pcs ({_signed(shopee_delta)})",
        f"{TIKTOKSHOP_LABEL}: {_fmt_int(report['tiktokshop_before_pieces'])} → "
        f"{_fmt_int(report['tiktokshop_after_pieces'])} pcs ({_signed(tiktokshop_delta)})",
        "",
        f"*{SHOPEE_LABEL}* — {report['shopee_status']}",
    ]
    lines.extend(report["shopee_lines"] or ["_(tidak ada varian)_"])
    lines.append("")
    lines.append(f"*{TIKTOKSHOP_LABEL}* — {report['tiktokshop_status']}")
    lines.extend(report["tiktokshop_lines"] or ["_(tidak ada varian)_"])

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
      🟧Shopee  before → after
      ♪TikTok   before → after

    Skipped/failed SKUs collapse to status + SKU + short reason.

    report = {
      "results": list[dict],
      "dry_run": bool,
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
            lines.append(f"{SHOPEE_LABEL} {sh_b} → {sh_a}")
            lines.append(f"{TIKTOKSHOP_LABEL} {tt_b} → {tt_a}")
        elif status == "skipped":
            short = _strip_sku_prefix(r["reason"])
            lines.append(f"⏭️ `{sku}`")
            lines.append(_truncate(short, 200))
        else:  # failed
            short = _strip_sku_prefix(r["reason"])
            lines.append(f"❌ `{sku}`")
            lines.append(_truncate(short, 200))
        lines.append("")

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
    """One-off error alert. Replaces 'Shopee'/'TikTok Shop' inside `text`
    with the labelled versions so error messages from main.py stay
    consistent with the rest of the Telegram output."""
    decorated = _decorate_platforms(text)
    _send(f"🚨 *Set Stock* — Error\n\n{decorated}")


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
    """Strip leading 'SKU `XXX` ' from a reason string when present."""
    return _SKU_PREFIX_RE.sub("", reason, count=1)


def _label_for_platform(platform: str) -> str:
    """Maps the bare platform string from skipped_one_side tuples to the
    emoji-prefixed display label."""
    p = (platform or "").strip()
    if p == "Shopee":
        return SHOPEE_LABEL
    if p == "TikTok Shop":
        return TIKTOKSHOP_LABEL
    return p


def _decorate_platforms(text: str) -> str:
    """Apply emoji prefixes to bare 'Shopee' / 'TikTok Shop' tokens in
    arbitrary message text (used for send_alert). Skips strings that
    already contain the emoji to avoid double-prefixing. Word-boundary
    regex so 'Shopee.com' or 'TikTokShopX' aren't touched."""
    if "♪" not in text:
        text = re.sub(r"\bTikTok Shop\b", TIKTOKSHOP_LABEL, text)
    if "🟧" not in text:
        text = re.sub(r"\bShopee\b", SHOPEE_LABEL, text)
    return text