"""
excel_reader.py
---------------
Reads inventory.xlsx (or any path the operator passes on the CLI).

Expected format:
  Row 1: headers (text ignored; column ORDER matters)
  Row 2+: one SKU per row
    Column A: SKU         (string, the BASE SKU as published)
    Column B: Stock       (int, total physical pieces in warehouse)

Behaviour:
  - Empty rows are skipped silently.
  - Rows where Stock isn't an integer are skipped with a row-level warning.
  - Negative stock is skipped with a warning.
  - Duplicate SKU rows: last value wins, with a warning printed.
  - Pack-size variant SKUs (e.g. "25PCS-...") in the Excel are REJECTED
    with a warning. The operator must supply the BASE SKU only — the
    bot fans out to every variant on both platforms automatically.

Returns:
  (desired_stock: dict[base_sku, int], skipped_variants: list[str])
"""

from __future__ import annotations

from pathlib import Path

import openpyxl

from src.inventory_allocator import PACK_SIZE_PATTERN


def read_inventory(path: Path) -> tuple[dict[str, int], list[str]]:
    """See module docstring."""
    workbook = openpyxl.load_workbook(path, data_only=True)
    sheet = workbook.active

    result: dict[str, int] = {}
    skipped_variants: list[str] = []

    for row_num, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        if not row or len(row) < 2:
            continue

        sku_raw, stock_raw = row[0], row[1]

        if sku_raw is None or str(sku_raw).strip() == "":
            continue

        sku = str(sku_raw).strip()

        if PACK_SIZE_PATTERN.match(sku):
            print(
                f"  Row {row_num}: SKU '{sku}' is a pack-size variant; "
                f"skipping (provide the base SKU instead — variants auto-fan-out)"
            )
            skipped_variants.append(sku)
            continue

        try:
            stock = int(stock_raw)
        except (TypeError, ValueError):
            print(f"  Row {row_num}: invalid stock '{stock_raw}' for SKU '{sku}', skipping")
            continue

        if stock < 0:
            print(f"  Row {row_num}: negative stock {stock} for SKU '{sku}', skipping")
            continue

        if sku in result and result[sku] != stock:
            print(
                f"  Row {row_num}: SKU '{sku}' duplicate — overwriting "
                f"earlier value {result[sku]} with {stock}"
            )
        result[sku] = stock

    return result, skipped_variants
