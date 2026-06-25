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
    assert "`/stock_set`" in text  # hint still tap-to-copy


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
        "weight_lines": ["• 1PCS = 0.0017 kg", "• 1000PCS = 1.7 kg"],
        "status": "✅ berhasil",
        "dry_run": False,
    })
    _assert_legacy_markdown_ok(text)
