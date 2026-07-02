#!/usr/bin/env python3
"""Execute multiple Telegram-style stock commands sequentially.

This runner is intentionally thin: it parses one command per line and delegates
to the existing CLI scripts so each command keeps its current validation,
logging, Telegram summary, and API behavior.
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DRY_FLAG_RE = re.compile(r"^(dry|dryrun|dry-run)$", re.IGNORECASE)
QTY_TOKEN_RE = re.compile(r"^(\d+)\s*([a-zA-Z]*)$")
PCS_UNIT_RE = re.compile(r"^(pcs|pc)?$", re.IGNORECASE)
GRAM_UNIT_RE = re.compile(r"^(g|gr|gram|grams)?$", re.IGNORECASE)
# Legacy English command names stay accepted forever as aliases of the
# Indonesian primaries (same convention as /berat_set with alias /weight_set).
COMMAND_ALIASES = {
    "/stock_set": "/stok_set",
    "/stock_get": "/stok_get",
    "/stock_balance": "/stok_balance",
    "/stock_low": "/stok_low",
    "/variant_set": "/varian_set",
    "/weight_set": "/berat_set",
}
SUPPORTED_COMMANDS = {
    "/stok_set",
    "/stok_get",
    "/stok_balance",
    "/stok_low",
    "/harga_set",
    "/varian_set",
    "/berat_set",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a batch of Telegram stock commands.")
    parser.add_argument(
        "--commands-file",
        type=Path,
        required=True,
        help="Text file containing one /command per line.",
    )
    return parser.parse_args()


def strip_dry_flag(tokens: list[str]) -> tuple[list[str], bool]:
    tokens = tokens[:]
    dry_run = False
    if tokens and DRY_FLAG_RE.match(tokens[-1]):
        dry_run = True
        tokens.pop()
    return tokens, dry_run


def parse_qty(token: str, unit_re: re.Pattern[str]) -> int | None:
    match = QTY_TOKEN_RE.match(token or "")
    if not match:
        return None
    if not unit_re.match(match.group(2) or ""):
        return None
    return int(match.group(1))


def build_invocation(command: str, args: list[str]) -> list[str]:
    command = command.lower()
    command = COMMAND_ALIASES.get(command, command)

    if command == "/stok_set":
        tokens, dry_run = strip_dry_flag(args)
        if len(tokens) < 2 or len(tokens) % 2 != 0:
            raise ValueError("/stok_set requires SKU JUMLAH pairs")
        skus = [tokens[i].upper() for i in range(0, len(tokens), 2)]
        pieces = [tokens[i] for i in range(1, len(tokens), 2)]
        cmd = [sys.executable, "scripts/stock_set_price.py", "--sku", *skus, "--pieces", *pieces]
        if dry_run:
            cmd.append("--dry-run")
        return cmd

    if command == "/stok_get":
        if not args:
            raise ValueError("/stok_get requires at least one SKU or keyword")
        return [sys.executable, "scripts/stock_get.py", "--sku", "\n".join(a.upper() for a in args)]

    if command == "/stok_balance":
        tokens, dry_run = strip_dry_flag(args)
        if not tokens:
            raise ValueError("/stok_balance requires at least one SKU")
        cmd = [sys.executable, "scripts/stock_balance.py", "--sku", *[t.upper() for t in tokens]]
        if dry_run:
            cmd.append("--dry-run")
        return cmd

    if command == "/stok_low":
        return [sys.executable, "scripts/stock_low.py"]

    if command == "/harga_set":
        tokens, dry_run = strip_dry_flag(args)
        if len(tokens) < 3:
            raise ValueError("/harga_set requires SKU JUMLAH HARGA pairs")
        sku = tokens[0].upper()
        tiers = tokens[1:]
        if len(tiers) < 2 or len(tiers) % 2 != 0:
            raise ValueError("/harga_set tiers must be JUMLAH HARGA pairs")
        cmd = [sys.executable, "scripts/harga_set.py", "--sku", sku, "--tiers", *tiers]
        if dry_run:
            cmd.append("--dry-run")
        return cmd

    if command == "/varian_set":
        tokens, dry_run = strip_dry_flag(args)
        if len(tokens) < 2:
            raise ValueError("/varian_set requires SKU and at least one pack size")
        sku = tokens[0].upper()
        packs = tokens[1:]
        cmd = [sys.executable, "scripts/variant_set.py", "--sku", sku, "--packs", *packs]
        if dry_run:
            cmd.append("--dry-run")
        return cmd

    if command == "/berat_set":
        tokens, dry_run = strip_dry_flag(args)
        if len(tokens) != 3:
            raise ValueError("/berat_set requires SKU REF_PCS BERAT")
        sku = tokens[0].upper()
        ref_pcs = parse_qty(tokens[1], PCS_UNIT_RE)
        grams = parse_qty(tokens[2], GRAM_UNIT_RE)
        if ref_pcs is None or ref_pcs < 1 or grams is None or grams < 1:
            raise ValueError("/berat_set REF_PCS and BERAT must be positive numbers")
        cmd = [
            sys.executable,
            "scripts/weight_set.py",
            "--sku",
            sku,
            "--ref-pcs",
            str(ref_pcs),
            "--grams",
            str(grams),
        ]
        if dry_run:
            cmd.append("--dry-run")
        return cmd

    raise ValueError(f"Unsupported command: {command}")


def parse_command_lines(text: str) -> list[tuple[str, list[str], str]]:
    commands: list[tuple[str, list[str], str]] = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = shlex.split(line)
        if not parts:
            continue
        command = parts[0]
        at_idx = command.find("@")
        if at_idx > 0:
            command = command[:at_idx]
        command = command.lower()
        command = COMMAND_ALIASES.get(command, command)
        if command not in SUPPORTED_COMMANDS:
            raise ValueError(f"Line {line_no}: unsupported command {command}")
        commands.append((command, parts[1:], line))
    return commands


def main() -> int:
    args = parse_args()
    text = args.commands_file.read_text(encoding="utf-8")
    commands = parse_command_lines(text)
    if not commands:
        print("No commands to run.", file=sys.stderr)
        return 2

    print("=" * 70)
    print(f"ITBisa Shop Stock Bot — Batch mode ({len(commands)} command(s))")
    print("=" * 70)

    for idx, (command, raw_args, raw_line) in enumerate(commands, start=1):
        print()
        print("-" * 70)
        print(f"[{idx}/{len(commands)}] {raw_line}")
        print("-" * 70)
        try:
            invocation = build_invocation(command, raw_args)
        except ValueError as e:
            print(f"✗ Invalid command: {e}", file=sys.stderr)
            return 2

        result = subprocess.run(invocation, cwd=PROJECT_ROOT, check=False)
        if result.returncode != 0:
            print(f"✗ Command failed with exit code {result.returncode}: {raw_line}", file=sys.stderr)
            return result.returncode

    print()
    print("=" * 70)
    print("Batch completed.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
