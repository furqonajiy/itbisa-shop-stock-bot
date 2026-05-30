"""
telegram_sender.py
------------------
Sends a Bahasa Indonesia summary of the stock-set / stock-get / stock-balance
run to the operator's Telegram chat. Same chat as the order bots, but a
different message shape — this is a transactional report, not an order label.

Public functions:
  send_run_summary(report)                  — bulk Excel run
  send_single_sku_summary(report)           — single-SKU /stock_set run
  send_stock_set_multi_summary(report)      — multi-SKU /stock_set run
  send_stock_get_summary(report)            — single-SKU /stock_get run (read-only)
  send_stock_balance_summary(report)        — single-SKU /stock_balance run
  send_stock_balance_multi_summary(report)  — multi-SKU /stock_balance run
  send_alert(text, mode)                    — error path

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
_RAW_VARIANT_LINE_RE = re.compile(
    r"^• `(?P<raw>[^`]+)`: (?P<units>[\d.]+) unit \(= (?P<pcs>[\d.]+) pcs\)(?P<suffix>.*)$"
)


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
    sku = report["base_sku"]

    lines = [
        header,
        "",
        f"✅ `{sku}`",
        f"Total: {_fmt_int(report['total_pieces'])} pcs",
        "",
        "📊 *Ringkas*",
        f"{SHOPEE_LABEL} {_fmt_int(report['shopee_pieces'])} pcs — {report['shopee_status']}",
        f"{TIKTOKSHOP_LABEL} {_fmt_int(report['tiktokshop_pieces'])} pcs — {report['tiktokshop_status']}",
        "",
        "📦 *Detail*",
        SHOPEE_LABEL,
    ]
    lines.extend(_compact_set_variant_lines(report["shopee_lines"], sku))
    lines.append("")
    lines.append(TIKTOKSHOP_LABEL)
    lines.extend(_compact_set_variant_lines(report["tiktokshop_lines"], sku))

    if report["dry_run"]:
        lines.append("")
        lines.append("_Dry run — tidak ada write API yang dipanggil._")

    _send(_join(lines))


def send_stock_set_multi_summary(report: dict) -> None:
    """
    Multi-SKU set run (from /stock_set SKU1 N1 SKU2 N2 ... with 2+ SKU).

    ONE compact message at end-of-run. Per SKU:
      <status>  `SKU` <total> pcs
      🟧Shopee  <shopee_pieces> pcs
      ♪TikTok Shop  <tiktokshop_pieces> pcs

    Skipped/failed SKUs collapse to status + SKU + short reason.

    report = {
      "results": list[dict],   # see _set_one_sku result shape in src/main.py
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
    header = f"📦 *Set Stock*{suffix} ({total} SKU)"

    lines = [header, ""]

    for r in results:
        sku = r["base_sku"]
        status = r["status"]
        if status in ("ok", "dry_run"):
            icon = "🔍" if status == "dry_run" else "✅"
            lines.append(f"{icon} `{sku}` {_fmt_int(r['total_pieces'])} pcs")
            lines.append(f"{SHOPEE_LABEL} {_fmt_int(r['shopee_pieces'])} pcs")
            lines.append(f"{TIKTOKSHOP_LABEL} {_fmt_int(r['tiktokshop_pieces'])} pcs")
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

    shopee_total_pcs = sum(v["stock_units"] * v["multiplier"] for v in shopee)
    tiktokshop_total_pcs = sum(v["stock_units"] * v["multiplier"] for v in tiktokshop)

    lines = [
        "📊 *Stock Get* — Selesai",
        "",
        f"✅ `{base_sku}`",
        f"Ditemukan: {len(rows)} varian ({SHOPEE_LABEL} {len(shopee)}, {TIKTOKSHOP_LABEL} {len(tiktokshop)})",
        "",
        "📊 *Ringkas*",
        f"{SHOPEE_LABEL} total: {_fmt_int(shopee_total_pcs)} pcs",
        f"{TIKTOKSHOP_LABEL} total: {_fmt_int(tiktokshop_total_pcs)} pcs",
        f"Total gabungan: {_fmt_int(shopee_total_pcs + tiktokshop_total_pcs)} pcs",
        "",
        "📦 *Detail*",
    ]

    for row in rows:
        s = row["shopee"]
        t = row["tiktokshop"]
        mult = row["multiplier"]
        pack_label = _pack_label(row["raw_sku"], base_sku)

        lines.append(f"• {pack_label} (×{_fmt_int(mult)})")
        if s is not None:
            lines.append(f"  {SHOPEE_LABEL}: {_stock_get_variant_line(s, mult)}")
        else:
            lines.append(f"  {SHOPEE_LABEL}: (tidak ada)")

        if t is not None:
            lines.append(f"  {TIKTOKSHOP_LABEL}: {_stock_get_variant_line(t, mult)}")
        else:
            lines.append(f"  {TIKTOKSHOP_LABEL}: (tidak ada)")

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


def send_alert(text: str, mode: str = "Set Stock") -> None:
    """One-off error alert. Header reflects the calling mode
    ("Set Stock" / "Get Stock" / "Balance Stock"); defaults to "Set Stock"
    so Excel-mode and Set-mode callers stay unchanged.

    Replaces 'Shopee'/'TikTok Shop' inside `text` with the labelled
    versions so error messages from main.py stay consistent with the rest
    of the Telegram output."""
    decorated = _decorate_platforms(text)
    _send(f"🚨 *{mode}* — Error\n\n{decorated}")


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


def _compact_set_variant_lines(lines: list[str], base_sku: str) -> list[str]:
    if not lines:
        return ["_(tidak ada varian)_"]
    return [_compact_set_variant_line(line, base_sku) for line in lines]


def _compact_set_variant_line(line: str, base_sku: str) -> str:
    match = _RAW_VARIANT_LINE_RE.match(line)
    if not match:
        return line

    raw_sku = match.group("raw")
    units = match.group("units")
    pcs = match.group("pcs")
    suffix = match.group("suffix").strip()
    suffix = f" {suffix}" if suffix else ""
    return f"• {_pack_label(raw_sku, base_sku)}: {_fmt_units(units)} unit = {_fmt_units(pcs)} pcs{suffix}"


def _stock_get_variant_line(variant: dict, multiplier: int) -> str:
    units = int(variant["stock_units"])
    pieces = units * multiplier
    return f"{_fmt_int(units)} unit = {_fmt_int(pieces)} pcs — {_fmt_weight(variant.get('weight_grams'))}"


def _pack_label(raw_sku: str, base_sku: str) -> str:
    if raw_sku == base_sku:
        return "1PCS"
    suffix = f"-{base_sku}"
    if raw_sku.endswith(suffix):
        return raw_sku[:-len(suffix)]
    return raw_sku


def _fmt_units(value: str) -> str:
    return _fmt_int(int(str(value).replace(".", "")))


def _fmt_int(n: int) -> str:
    """1234567 -> '1.234.567' (Indonesian thousands separator)."""
    return f"{n:,}".replace(",", ".")


def _fmt_weight(grams: int | None) -> str:
    """0/None → '—' (data tidak tersedia), else 'NNN g' with thousands separator."""
    if not grams:
        return "—"
    return f"{_fmt_int(int(grams))} g"


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
