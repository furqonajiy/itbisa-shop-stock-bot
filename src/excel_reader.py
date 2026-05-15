"""
excel_reader.py
---------------
Reads stock.xlsx (or any path the operator passes on the CLI).

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
  - SKU values are trusted as operator-provided base SKUs.

Returns:
  desired_stock: dict[base_sku, int]
"""

from __future__ import annotations

from pathlib import Path

import openpyxl


def read_stock(path: Path) -> dict[str, int]:
    """See module docstring."""
    workbook = openpyxl.load_workbook(path, data_only=True)
    sheet = workbook.active

    result: dict[str, int] = {}

    for row_num, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        if not row or len(row) < 2:
            continue

        sku_raw, stock_raw = row[0], row[1]

        if sku_raw is None or str(sku_raw).strip() == "":
            continue

        sku = str(sku_raw).strip()

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

    return result
