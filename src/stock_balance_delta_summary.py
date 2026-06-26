"""Stock-balance runner with compact Telegram balance formatting."""

from __future__ import annotations

import re

from src import stock_balance_price_rule as _stock_balance_price_rule
from src import telegram_sender
from src.shopee_detail_enrichment import enrich_shopee_prices

_run_stock_balance_multi = _stock_balance_price_rule.run_stock_balance_multi

_RAW_LINE_RE = re.compile(
    r"^• `(?P<raw>[^`]+)`: (?P<units>[\d.]+) unit \(= (?P<pcs>[\d.]+) pcs\)(?P<price>.*)$"
)


def run_stock_balance_multi(base_skus: list[str], dry_run: bool) -> int:
    """Run stock balance while formatting Telegram balance summaries compactly."""
    original_single_sender = telegram_sender.send_stock_balance_summary
    original_multi_sender = telegram_sender.send_stock_balance_multi_summary
    original_balance_single_sender = _stock_balance_price_rule._send_single_balance_telegram
    original_shopee_detail_builder = _stock_balance_price_rule._build_shopee_detail_variants

    def _build_shopee_detail_variants_with_price(target_pieces: int, variants: list[dict]) -> list[dict]:
        enrich_shopee_prices(variants)
        return original_shopee_detail_builder(target_pieces, variants)

    telegram_sender.send_stock_balance_summary = _send_stock_balance_summary_compact
    telegram_sender.send_stock_balance_multi_summary = _send_stock_balance_multi_summary_with_delta
    _stock_balance_price_rule._send_single_balance_telegram = _send_stock_balance_result_compact
    if len(base_skus) == 1:
        _stock_balance_price_rule._build_shopee_detail_variants = _build_shopee_detail_variants_with_price
    try:
        return _run_stock_balance_multi(base_skus, dry_run=dry_run)
    finally:
        telegram_sender.send_stock_balance_summary = original_single_sender
        telegram_sender.send_stock_balance_multi_summary = original_multi_sender
        _stock_balance_price_rule._send_single_balance_telegram = original_balance_single_sender
        _stock_balance_price_rule._build_shopee_detail_variants = original_shopee_detail_builder


def _send_stock_balance_result_compact(result: dict, dry_run: bool) -> None:
    """Single-SKU /stock_balance sender that preserves detail variant metadata."""
    if result["status"] == "skipped":
        telegram_sender.send_alert(result["reason"], mode="Balance Stock")
        return

    report = dict(result)
    report["dry_run"] = dry_run
    _send_stock_balance_summary_compact(report)


def _send_stock_balance_summary_compact(report: dict) -> None:
    """Single-SKU /stock_balance summary with compact detail rows."""
    header = "🔄 *Balance Stock* — DRY RUN" if report["dry_run"] else "🔄 *Balance Stock* — Selesai"
    sku = report["base_sku"]

    lines = [
        header,
        "",
        f"✅ `{sku}`",
        f"Σ Total: {_fmt_int(report['total_pieces'])} pcs",
        "",
        "📊 *Ringkas*",
        _platform_change_line(
            telegram_sender.SHOPEE_LABEL,
            report["shopee_before_pieces"],
            report["shopee_after_pieces"],
        ),
        _platform_change_line(
            telegram_sender.TIKTOKSHOP_LABEL,
            report["tiktokshop_before_pieces"],
            report["tiktokshop_after_pieces"],
        ),
        "",
        "📦 *Detail*",
        telegram_sender.SHOPEE_LABEL,
    ]
    lines.extend(_compact_variant_lines(
        report.get("shopee_detail_variants"),
        report["shopee_lines"],
        sku,
    ))
    lines.append("")
    lines.append(telegram_sender.TIKTOKSHOP_LABEL)
    lines.extend(_compact_variant_lines(
        report.get("tiktokshop_detail_variants"),
        report["tiktokshop_lines"],
        sku,
    ))

    if report["dry_run"]:
        lines.append("")
        lines.append("_Dry run — tidak ada write API yang dipanggil._")

    telegram_sender._send(telegram_sender._join(lines))  # noqa: SLF001 - Telegram formatting reuse


def _send_stock_balance_multi_summary_with_delta(report: dict) -> None:
    """Compact multi-SKU /stock_balance summary with non-zero platform deltas."""
    results = report["results"]
    dry_run = bool(report.get("dry_run", False))
    total = len(results)

    ok_count = sum(1 for r in results if r["status"] in ("ok", "dry_run"))
    skip_count = sum(1 for r in results if r["status"] == "skipped")
    fail_count = sum(1 for r in results if r["status"] == "failed")

    suffix = " — DRY RUN" if dry_run else " — Selesai"
    lines = [f"🔄 *Balance Stock*{suffix} ({total} SKU)", ""]

    for result in results:
        sku = result["base_sku"]
        status = result["status"]
        if status in ("ok", "dry_run"):
            icon = "🔍" if status == "dry_run" else "✅"
            lines.append(f"{icon} `{sku}`")
            lines.append(_platform_change_line(
                telegram_sender.SHOPEE_LABEL,
                result["shopee_before_pieces"],
                result["shopee_after_pieces"],
            ))
            lines.append(_platform_change_line(
                telegram_sender.TIKTOKSHOP_LABEL,
                result["tiktokshop_before_pieces"],
                result["tiktokshop_after_pieces"],
            ))
            lines.append(_total_line(
                result["shopee_after_pieces"],
                result["tiktokshop_after_pieces"],
            ))
        elif status == "skipped":
            lines.append(f"⏭️ `{sku}`")
            lines.append(telegram_sender._truncate(  # noqa: SLF001 - formatting helper reuse
                telegram_sender._strip_sku_prefix(result["reason"]),  # noqa: SLF001
                200,
            ))
        else:
            lines.append(f"❌ `{sku}`")
            lines.append(telegram_sender._truncate(  # noqa: SLF001 - formatting helper reuse
                telegram_sender._strip_sku_prefix(result["reason"]),  # noqa: SLF001
                200,
            ))
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

    telegram_sender._send(telegram_sender._join(lines))  # noqa: SLF001 - Telegram formatting reuse


def _compact_variant_lines(
        detail_variants: list[dict] | None,
        fallback_lines: list[str],
        base_sku: str,
) -> list[str]:
    if detail_variants:
        return [_compact_detail_variant_line(variant, base_sku) for variant in detail_variants]
    if not fallback_lines:
        return ["_(tidak ada varian)_"]
    return [_compact_variant_line(line, base_sku) for line in fallback_lines]


def _compact_detail_variant_line(variant: dict, base_sku: str) -> str:
    pack_label = _pack_label(variant["raw_sku"], base_sku)
    units = _fmt_int(variant["units"])
    pieces = _fmt_int(variant["pieces"])
    weight = _fmt_weight(variant.get("weight_grams"))
    price = _fmt_price(variant.get("price_idr"))
    price_suffix = f" — {price}" if price else ""
    return f"• {pack_label}: {units} unit = {pieces} pcs — {weight}{price_suffix}"


def _compact_variant_line(line: str, base_sku: str) -> str:
    match = _RAW_LINE_RE.match(line)
    if not match:
        return line

    raw_sku = match.group("raw")
    units = match.group("units")
    pcs = match.group("pcs")
    price = match.group("price").strip()
    pack_label = _pack_label(raw_sku, base_sku)
    price_suffix = f" {price}" if price else ""
    return f"• {pack_label}: {units} unit = {pcs} pcs — —{price_suffix}"


def _pack_label(raw_sku: str, base_sku: str) -> str:
    if raw_sku == base_sku:
        return "1PCS"
    suffix = f"-{base_sku}"
    if raw_sku.endswith(suffix):
        return raw_sku[:-len(suffix)]
    return raw_sku


def _platform_change_line(label: str, before: int, after: int) -> str:
    suffix = _delta_suffix(after - before)
    return f"{label} {_fmt_int(before)} → {_fmt_int(after)}{suffix}"


def _total_line(shopee_after: int, tiktokshop_after: int) -> str:
    """Combined physical stock across both platforms (Σ)."""
    return f"Σ Total: {_fmt_int(int(shopee_after) + int(tiktokshop_after))} pcs"


def _delta_suffix(delta: int) -> str:
    if delta > 0:
        return f" (+{_fmt_int(delta)})"
    if delta < 0:
        return f" ({_fmt_int(delta)})"
    return ""


def _fmt_int(value: int | str) -> str:
    return telegram_sender._fmt_int(int(str(value).replace(".", "")))  # noqa: SLF001


def _fmt_weight(value: int | None) -> str:
    if not value:
        return "—"
    return f"{_fmt_int(value)} g"


def _fmt_price(value: int | None) -> str:
    if value is None:
        return ""
    return f"Rp{_fmt_int(value)}"
