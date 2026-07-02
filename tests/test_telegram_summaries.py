"""Regression tests: /variant_set + /weight_set Telegram summaries must be
valid legacy Markdown (balanced entities, no code span nested in italic)."""

import re

from src import telegram_sender


def _capture(monkeypatch, fn, report):
    captured = {}
    monkeypatch.setattr(telegram_sender, "_send", lambda text: captured.update(text=text))
    fn(report)
    return captured["text"]


def _code_span_nested_in_italic(text):
    """True if any `code` span sits inside an _italic_ span (illegal in legacy
    Markdown). Underscores INSIDE a code span are literal, not delimiters — so
    split on backticks: even segments are outside code, odd segments are the
    code spans. A code span is nested if an odd number of italic underscores
    preceded it (italic still open)."""
    parts = text.split("`")
    underscores_before = 0
    for i, part in enumerate(parts):
        if i % 2 == 0:
            underscores_before += part.count("_")
        elif underscores_before % 2 == 1:
            return True
    return False


def _assert_legacy_markdown_ok(text):
    # Backticks must be balanced (each code span opens and closes)...
    assert text.count("`") % 2 == 0, f"unbalanced `: {text!r}"
    # ...bold/italic delimiters OUTSIDE code spans must be balanced...
    outside = re.sub(r"`[^`]*`", "", text)
    assert outside.count("*") % 2 == 0, f"unbalanced *: {text!r}"
    assert outside.count("_") % 2 == 0, f"unbalanced _: {text!r}"
    # ...and no code span may be nested inside an italic span (legacy Markdown
    # rejects nested entities → the whole message 400s and renders as raw text).
    assert not _code_span_nested_in_italic(text), f"code span nested in italic: {text!r}"


def test_variant_summary_success_is_valid_markdown(monkeypatch):
    text = _capture(monkeypatch, telegram_sender.send_variant_set_summary, {
        "base_sku": "ITBISA-IC-CD4094BM-SMD-SOP16",
        "value_names": ["1PCS", "10PCS", "50PCS", "100PCS", "500PCS", "1000PCS", "Bubble Wrap"],
        "status": "✅ berhasil",
        "dry_run": False,
    })
    _assert_legacy_markdown_ok(text)
    assert "`/stok_set`" in text  # hint still tap-to-copy


def test_variant_summary_dry_run_is_valid_markdown(monkeypatch):
    text = _capture(monkeypatch, telegram_sender.send_variant_set_summary, {
        "base_sku": "ITBISA-IC-CD4094BM-SMD-SOP16",
        "value_names": ["1PCS", "10PCS", "Bubble Wrap"],
        "status": "🔍 dry-run",
        "dry_run": True,
    })
    _assert_legacy_markdown_ok(text)


def test_weight_summary_is_valid_markdown(monkeypatch):
    text = _capture(monkeypatch, telegram_sender.send_weight_set_summary, {
        "base_sku": "ITBISA-IC-CD4094BM-SMD-SOP16",
        "per_pcs_g": 1.7,
        "weight_lines": ["• 1PCS = 2 g", "• 1000PCS = 1700 g"],
        "status": "✅ berhasil",
        "dry_run": False,
    })
    _assert_legacy_markdown_ok(text)


def test_balance_multi_summary_shows_combined_total(monkeypatch):
    from src import stock_balance_delta_summary as bds

    captured = {}
    monkeypatch.setattr(telegram_sender, "_send", lambda text: captured.update(text=text))
    bds._send_stock_balance_multi_summary_with_delta({
        "results": [{
            "base_sku": "ITBISA-7SEGMENT-ANODE-RED-1.20-1BIT",
            "status": "ok",
            "shopee_before_pieces": 100, "shopee_after_pieces": 120,
            "tiktokshop_before_pieces": 120, "tiktokshop_after_pieces": 100,
        }],
        "dry_run": False,
    })
    text = captured["text"]
    assert "🧮 Total: 220 pcs" in text       # 120 Shopee + 100 TikTok Shop
    _assert_legacy_markdown_ok(text)


def test_balance_single_compact_shows_combined_total(monkeypatch):
    from src import stock_balance_delta_summary as bds

    captured = {}
    monkeypatch.setattr(telegram_sender, "_send", lambda text: captured.update(text=text))
    bds._send_stock_balance_summary_compact({
        "base_sku": "ITBISA-7SEGMENT-ANODE-RED-1.20-1BIT",
        "dry_run": False, "total_pieces": 220,
        "shopee_before_pieces": 100, "shopee_after_pieces": 120,
        "tiktokshop_before_pieces": 120, "tiktokshop_after_pieces": 100,
        "shopee_status": "✅ berhasil", "tiktokshop_status": "✅ berhasil",
        "shopee_lines": [], "tiktokshop_lines": [],
        "shopee_detail_variants": None, "tiktokshop_detail_variants": None,
    })
    text = captured["text"]
    assert "🧮 Total: 220 pcs" in text
    _assert_legacy_markdown_ok(text)
