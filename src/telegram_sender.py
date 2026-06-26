"""
telegram_sender.py
------------------
Sends a Bahasa Indonesia summary of the stock-set / stock-get / stock-balance
run to the operator's Telegram chat. Same chat as the order bots, but a
different message shape — this is a transactional report, not an order label.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import requests

from src import config

_WIB = timezone(timedelta(hours=7))

_TELEGRAM_API = "https://api.telegram.org"
_MAX_MESSAGE_CHARS = 4000  # Telegram caps at 4096; leave headroom

SHOPEE_LABEL = "🟧 Shopee"
TIKTOKSHOP_LABEL = "🟦 TikTok Shop"

_SKU_PREFIX_RE = re.compile(r"^SKU `[^`]+`\s+")
_RAW_VARIANT_LINE_RE = re.compile(
    r"^• `(?P<raw>[^`]+)`: (?P<units>[\d.]+) unit \(= (?P<pcs>[\d.]+) pcs\)(?P<suffix>.*)$"
)


# ============================================================
# Public surface
# ============================================================

def send_run_summary(report: dict) -> None:
    """Bulk Excel run."""
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
    """Single-SKU run (from /stock_set SKU AMOUNT)."""
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
    lines.extend(_variant_detail_lines(
        report.get("shopee_detail_variants"),
        report.get("shopee_lines") or [],
        sku,
    ))
    lines.append("")
    lines.append(TIKTOKSHOP_LABEL)
    lines.extend(_variant_detail_lines(
        report.get("tiktokshop_detail_variants"),
        report.get("tiktokshop_lines") or [],
        sku,
    ))

    if report["dry_run"]:
        lines.append("")
        lines.append("_Dry run — tidak ada write API yang dipanggil._")

    _send(_join(lines))


def send_stock_set_multi_summary(report: dict) -> None:
    """Multi-SKU set run (from /stock_set SKU1 N1 SKU2 N2 ... with 2+ SKU)."""
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
        else:
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
    """Single-SKU read-only run (from /stock_get SKU)."""
    base_sku = report["base_sku"]
    shopee = sorted(report["shopee_variants"], key=lambda v: v["multiplier"])
    tiktokshop = sorted(report["tiktokshop_variants"], key=lambda v: v["multiplier"])

    shopee_total_pcs = sum(v["stock_units"] * v["multiplier"] for v in shopee)
    tiktokshop_total_pcs = sum(v["stock_units"] * v["multiplier"] for v in tiktokshop)

    lines = [
        "📊 *Stock Get* — Selesai",
        "",
        f"✅ `{base_sku}`",
        "",
        "📊 *Ringkas*",
        f"{SHOPEE_LABEL} total: {_fmt_int(shopee_total_pcs)} pcs",
        f"{TIKTOKSHOP_LABEL} total: {_fmt_int(tiktokshop_total_pcs)} pcs",
        f"Total gabungan: {_fmt_int(shopee_total_pcs + tiktokshop_total_pcs)} pcs",
        "",
        "📦 *Detail*",
        SHOPEE_LABEL,
    ]
    lines.extend(_stock_get_variant_lines(shopee, base_sku))
    lines.append("")
    lines.append(TIKTOKSHOP_LABEL)
    lines.extend(_stock_get_variant_lines(tiktokshop, base_sku))

    _send(_join(lines))


def send_stock_get_multi_summary(report: dict) -> None:
    """Multi-SKU read-only run formatter."""
    results = report["results"]
    total = len(results)

    ok_count = sum(1 for r in results if r["status"] == "ok")
    fail_count = sum(1 for r in results if r["status"] == "failed")

    lines = [f"📊 *Stock Get* — Selesai ({total} SKU)", ""]

    for r in results:
        sku = r["base_sku"]
        status = r["status"]
        if status == "ok":
            lines.append(f"✅ `{sku}`")
            lines.append(f"{SHOPEE_LABEL}: {_fmt_int(r['shopee_pieces'])} pcs")
            lines.append(f"{TIKTOKSHOP_LABEL}: {_fmt_int(r['tiktokshop_pieces'])} pcs")
            lines.append(f"Total: {_fmt_int(r['total_pieces'])} pcs")
        else:
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
    if fail_count:
        summary_parts.append(f"{fail_count} ❌")
    lines.append("*Ringkasan:* " + " | ".join(summary_parts))

    _send(_join(lines))


def send_stock_balance_summary(report: dict) -> None:
    """Fallback single-SKU rebalance run formatter."""
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
        f"🧮 Total: {_fmt_int(report['total_pieces'])} pcs (dipertahankan)",
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
    """Multi-SKU rebalance run formatter."""
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
            lines.append(
                f"🧮 Total: "
                f"{_fmt_int(int(r['shopee_after_pieces']) + int(r['tiktokshop_after_pieces']))} pcs"
            )
        elif status == "skipped":
            short = _strip_sku_prefix(r["reason"])
            lines.append(f"⏭️ `{sku}`")
            lines.append(_truncate(short, 200))
        else:
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


def send_low_stock_summary(items: list[dict], threshold: int) -> None:
    """Low-stock report (/stock_low): every base SKU below the threshold.

    `items` is the output of low_stock.find_low_stock (sorted ascending by
    total). The list can be long, so the message is split across multiple
    Telegram sends on line boundaries.
    """
    if not items:
        _send(
            f"📉 *Stock Rendah* (< {_fmt_int(threshold)} pcs)\n\n"
            f"✅ Tidak ada SKU di bawah {_fmt_int(threshold)} pcs."
        )
        return

    lines = [
        f"📉 *Stock Rendah* (< {_fmt_int(threshold)} pcs) — {len(items)} SKU",
        "",
    ]
    for it in items:
        lines.append(
            f"• `{it['base_sku']}`: {_fmt_int(it['total'])} pcs "
            f"(🟧 {_fmt_int(it['shopee'])} / 🟦 {_fmt_int(it['tiktokshop'])})"
        )
    lines.append("")
    lines.append(f"*Ringkasan:* {len(items)} SKU di bawah {_fmt_int(threshold)} pcs.")

    _send_chunked(lines)


def send_low_stock_skipped(last_run_iso: str | None) -> None:
    """Reply when /stock_low is triggered again within the throttle window."""
    when = _fmt_wib(last_run_iso)
    when_note = f" (terakhir {when} WIB)" if when else ""
    _send(
        f"📉 *Stock Rendah*\n\n"
        f"Laporan sudah dibuat{when_note}. Maks. 1× per "
        f"{config.LOW_STOCK_MIN_INTERVAL_HOURS} jam — coba lagi nanti."
    )


def send_harga_set_summary(report: dict) -> None:
    """Single-SKU tiered price set (from /harga_set) across Shopee + TikTok Shop."""
    dry_run = bool(report.get("dry_run"))
    base_sku = report["base_sku"]
    suffix = " — DRY RUN" if dry_run else " — Selesai"

    lines = [
        f"💰 *Set Harga*{suffix}",
        "",
        f"✅ `{base_sku}`",
        "",
    ]

    # TikTok Shop section — one line per pack-size variant.
    tiktok = report.get("tiktok")
    if tiktok is None:
        lines.append(f"{TIKTOKSHOP_LABEL} — _(tidak ada)_")
    else:
        lines.append(f"{TIKTOKSHOP_LABEL} — {tiktok['status']}")
        for p in tiktok.get("priced") or []:
            lines.append(
                f"• {p['multiplier']}PCS = Rp{_fmt_int(p['variant_price'])} "
                f"(Rp{_fmt_int(p['unit_price'])}/pcs)"
            )
        skipped = tiktok.get("skipped") or []
        if skipped:
            lines.append(f"⏭️ {len(skipped)} varian dilewati (di bawah tier terendah)")

    lines.append("")

    # Shopee section — base + Harga Grosir as quantity bands.
    shopee = report.get("shopee")
    if shopee is None:
        lines.append(f"{SHOPEE_LABEL} — _(tidak ada)_")
    else:
        lines.append(f"{SHOPEE_LABEL} — {shopee['status']}")
        lines.append(f"• 1 = Rp{_fmt_int(shopee['base_price'])}")
        for mn, mx, price in shopee.get("wholesale_tiers") or []:
            hi = "∞" if mx >= 999999 else str(mx)
            lines.append(f"• {mn}–{hi} = Rp{_fmt_int(price)}")
        packs = shopee.get("skipped_packs") or []
        if packs:
            lines.append(f"⏭️ {len(packs)} produk pack-size dilewati")

    if dry_run:
        lines.append("")
        lines.append("_Dry run — tidak ada write API yang dipanggil._")

    _send(_join(lines))


def send_variant_set_summary(report: dict) -> None:
    """Single-SKU TikTok variant rebuild (from /variant_set)."""
    dry_run = bool(report.get("dry_run"))
    suffix = " — DRY RUN" if dry_run else " — Selesai"
    lines = [
        f"🧩 *Set Variant*{suffix}",
        "",
        f"✅ `{report['base_sku']}`",
        f"{TIKTOKSHOP_LABEL} — {report.get('status', '')}",
    ]
    for vn in report.get("value_names") or []:
        lines.append(f"• {vn}")
    if not dry_run and "✅" in report.get("status", ""):
        lines.append("")
        # No italic here: legacy Markdown can't nest the `/stock_set` code span
        # inside an _italic_ span (the message would fail to parse and fall back
        # to plain text). Keep the code span; drop the italic.
        lines.append("Stok di-set 0 — jalankan `/stock_set` untuk mengisi ulang total.")
    if dry_run:
        lines.append("")
        lines.append("_Dry run — tidak ada write API yang dipanggil._")
    _send(_join(lines))


def send_weight_set_summary(report: dict) -> None:
    """Single-SKU TikTok per-piece weight set (from /weight_set)."""
    dry_run = bool(report.get("dry_run"))
    suffix = " — DRY RUN" if dry_run else " — Selesai"
    per_pcs = report.get("per_pcs_g")
    lines = [
        f"⚖️ *Set Weight*{suffix}",
        "",
        f"✅ `{report['base_sku']}`",
        f"{TIKTOKSHOP_LABEL} — {report.get('status', '')}",
    ]
    if isinstance(per_pcs, (int, float)):
        lines.append(f"Berat per pcs: {per_pcs:g} g")
    lines.extend(report.get("weight_lines") or [])
    if dry_run:
        lines.append("")
        lines.append("_Dry run — tidak ada write API yang dipanggil._")
    _send(_join(lines))


def send_alert(text: str, mode: str = "Set Stock") -> None:
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
        # A 400 here is almost always a legacy-Markdown parse error (an
        # unbalanced `_`/`*`/`` ` `` slipped in from a raw API error string).
        # Retry once as plain text so the message — often an error report we
        # don't want to lose — still reaches the operator.
        print(f"  [telegram] Markdown send failed ({e}); retrying as plain text")
        try:
            plain = dict(body)
            plain.pop("parse_mode", None)
            requests.post(url, json=plain, timeout=15).raise_for_status()
        except Exception as e2:
            print(f"  [telegram] Failed to send summary: {e2}")


def _join(lines: list[str]) -> str:
    return "\n".join(lines)


def _send_chunked(lines: list[str]) -> None:
    """Send lines across multiple messages, each within _MAX_MESSAGE_CHARS,
    splitting only on line boundaries so no Markdown span is cut mid-line."""
    buf: list[str] = []
    size = 0
    for line in lines:
        add = len(line) + 1
        if buf and size + add > _MAX_MESSAGE_CHARS:
            _send("\n".join(buf))
            buf, size = [], 0
        buf.append(line)
        size += add
    if buf:
        _send("\n".join(buf))


def _fmt_wib(iso_utc: str | None) -> str:
    """Format a UTC ISO timestamp as 'YYYY-MM-DD HH:MM' in WIB, or '' if unparseable."""
    if not iso_utc:
        return ""
    try:
        return datetime.fromisoformat(iso_utc).astimezone(_WIB).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return ""


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def _variant_detail_lines(
        detail_variants: list[dict] | None,
        fallback_lines: list[str],
        base_sku: str,
) -> list[str]:
    if detail_variants:
        return [_detail_variant_line(variant, base_sku) for variant in detail_variants]
    if not fallback_lines:
        return ["_(tidak ada varian)_"]
    return [_compact_set_variant_line(line, base_sku) for line in fallback_lines]


def _detail_variant_line(variant: dict, base_sku: str) -> str:
    units = int(variant["units"])
    pieces = int(variant["pieces"])
    return _summary_variant_line(
        raw_sku=variant["raw_sku"],
        base_sku=base_sku,
        units=units,
        pieces=pieces,
        weight_grams=variant.get("weight_grams"),
        price_idr=variant.get("price_idr"),
    )


def _compact_set_variant_line(line: str, base_sku: str) -> str:
    match = _RAW_VARIANT_LINE_RE.match(line)
    if not match:
        return line

    raw_sku = match.group("raw")
    units = int(str(match.group("units")).replace(".", ""))
    pcs = int(str(match.group("pcs")).replace(".", ""))
    suffix = match.group("suffix").strip()
    return _summary_variant_line(
        raw_sku=raw_sku,
        base_sku=base_sku,
        units=units,
        pieces=pcs,
        weight_grams=None,
        raw_suffix=suffix,
    )


def _stock_get_variant_lines(variants: list[dict], base_sku: str) -> list[str]:
    if not variants:
        return ["_(tidak ada varian)_"]
    out: list[str] = []
    for variant in variants:
        out.append(_stock_get_variant_line(variant, base_sku))
        # Shopee variants carry their "Harga Grosir" tiers (TikTok variants don't).
        grosir = _wholesale_line(variant.get("wholesale_tiers"))
        if grosir:
            out.append(grosir)
    return out


def _wholesale_line(wholesale_tiers) -> str:
    """One indented "Harga Grosir" line, or "" when there are no tiers."""
    if not wholesale_tiers:
        return ""
    parts = []
    for mn, mx, price in wholesale_tiers:
        hi = "∞" if int(mx) >= 999999 else _fmt_int(mx)
        parts.append(f"{_fmt_int(mn)}–{hi}: Rp{_fmt_int(price)}")
    return "  Harga Grosir: " + ", ".join(parts)


def _stock_get_variant_line(variant: dict, base_sku: str) -> str:
    units = int(variant["stock_units"])
    pieces = units * int(variant["multiplier"])
    return _summary_variant_line(
        raw_sku=variant["raw_sku"],
        base_sku=base_sku,
        units=units,
        pieces=pieces,
        weight_grams=variant.get("weight_grams"),
        price_idr=variant.get("price_idr"),
    )


def _summary_variant_line(
        *,
        raw_sku: str,
        base_sku: str,
        units: int,
        pieces: int,
        weight_grams: int | None,
        price_idr: int | None = None,
        raw_suffix: str = "",
) -> str:
    weight = _fmt_weight(weight_grams)
    price = _fmt_price(price_idr)
    price_suffix = f" — {price}" if price else ""
    if raw_suffix and not price_suffix:
        price_suffix = f" {raw_suffix}"
    return (
        f"• {_pack_label(raw_sku, base_sku)}: "
        f"{_fmt_int(units)} unit = {_fmt_int(pieces)} pcs — {weight}{price_suffix}"
    )


def _pack_label(raw_sku: str, base_sku: str) -> str:
    if raw_sku == base_sku:
        return "1PCS"
    suffix = f"-{base_sku}"
    if raw_sku.endswith(suffix):
        return raw_sku[:-len(suffix)]
    return raw_sku


def _fmt_int(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _fmt_weight(grams: int | None) -> str:
    if not grams:
        return "—"
    return f"{_fmt_int(int(grams))} g"


def _fmt_price(value: int | None) -> str:
    if value is None:
        return ""
    return f"Rp{_fmt_int(int(value))}"


def _signed(n: int) -> str:
    if n > 0:
        return f"+{_fmt_int(n)}"
    if n < 0:
        return _fmt_int(n)
    return "±0"


def _strip_sku_prefix(reason: str) -> str:
    return _SKU_PREFIX_RE.sub("", reason, count=1)


def _label_for_platform(platform: str) -> str:
    p = (platform or "").strip()
    if p == "Shopee":
        return SHOPEE_LABEL
    if p == "TikTok Shop":
        return TIKTOKSHOP_LABEL
    return p


def _decorate_platforms(text: str) -> str:
    if "🟦" not in text:
        text = re.sub(r"\bTikTok Shop\b", TIKTOKSHOP_LABEL, text)
    if "🟧" not in text:
        text = re.sub(r"\bShopee\b", SHOPEE_LABEL, text)
    return text
