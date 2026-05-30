"""Stock-balance runner with compact multi-SKU delta formatting."""

from __future__ import annotations

from src import telegram_sender
from src.stock_balance_price_rule import run_stock_balance_multi as _run_stock_balance_multi


def run_stock_balance_multi(base_skus: list[str], dry_run: bool) -> int:
    """Run stock balance while formatting multi-SKU summaries with deltas."""
    original_sender = telegram_sender.send_stock_balance_multi_summary
    telegram_sender.send_stock_balance_multi_summary = _send_stock_balance_multi_summary_with_delta
    try:
        return _run_stock_balance_multi(base_skus, dry_run=dry_run)
    finally:
        telegram_sender.send_stock_balance_multi_summary = original_sender


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


def _platform_change_line(label: str, before: int, after: int) -> str:
    suffix = _delta_suffix(after - before)
    return (
        f"{label} {telegram_sender._fmt_int(before)} → "  # noqa: SLF001
        f"{telegram_sender._fmt_int(after)}{suffix}"  # noqa: SLF001
    )


def _delta_suffix(delta: int) -> str:
    if delta > 0:
        return f" (+{telegram_sender._fmt_int(delta)})"  # noqa: SLF001
    if delta < 0:
        return f" ({telegram_sender._fmt_int(delta)})"  # noqa: SLF001
    return ""
