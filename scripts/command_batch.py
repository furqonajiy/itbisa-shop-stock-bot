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
import time
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
# Commands that mutate the TikTok Shop product via Edit Product (202309,
# full-replace). The result propagates on TikTok's clock (often minutes) to
# both the 202309 product detail and the 202502 search, and a variation
# rebuild reissues sku_ids — so a later command on the SAME base SKU must
# wait for the catalog to settle first (see _gate_after).
TIKTOK_MUTATING_COMMANDS = {"/varian_set", "/berat_set"}
SETTLE_TIMEOUT_S = 360
SETTLE_INTERVAL_S = 20


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


def _line_base_sku(args: list[str]) -> str | None:
    """Base SKU of a command line = first argument token, uppercased."""
    tokens, _dry = strip_dry_flag(args)
    if not tokens:
        return None
    return tokens[0].upper()


def _gate_after(
    command: str,
    raw_args: list[str],
    remaining: list[tuple[str, list[str], str]],
) -> tuple[str, list[int] | None] | None:
    """Decide whether to wait for TikTok Shop to settle after this command.

    /varian_set and /berat_set edit the product via full-replace; TikTok
    takes minutes to propagate the result. Running the next same-SKU command
    immediately means: the next Edit Product rebuilds its full-replace
    payload from a stale detail snapshot and silently wipes the pending
    variant, and a stock/price write lands on the reissued (dead) sku_ids
    the stale search still returns.

    Returns (base_sku, required_packs) when a settle wait is needed —
    required_packs is the requested pack list for /varian_set, None for
    /berat_set (sku-id consistency only). Returns None when no wait is
    needed: non-mutating command, dry-run line (nothing written), or no
    later line referencing the same base SKU.
    """
    if command not in TIKTOK_MUTATING_COMMANDS:
        return None
    tokens, dry_run = strip_dry_flag(raw_args)
    if dry_run or not tokens:
        return None
    base_sku = tokens[0].upper()
    if not any(_line_base_sku(later_args) == base_sku for _cmd, later_args, _raw in remaining):
        return None
    required_packs: list[int] | None = None
    if command == "/varian_set":
        packs = [int(t) for t in tokens[1:] if t.isdigit()]
        required_packs = packs or None
    return base_sku, required_packs


def _settle_check(client, base_sku: str, required_packs: list[int] | None) -> str | None:
    """One settle poll. Returns None when settled, else a short reason.

    Settled means: the search catalog shows the base SKU (with every
    requested pack size when given), AND for each of its products the
    sku_id set the search returned equals the sku_id set in the product
    detail — the equality is what catches sku_ids reissued by a variation
    rebuild that the search snapshot still serves stale. The search set is
    collected across the WHOLE catalog because a product's siblings can
    group under other base keys (e.g. ITBISA-BUBBLE-WRAP).
    """
    try:
        catalog = client.fetch_catalog()
    except Exception as e:  # noqa: BLE001 - best-effort poll, retry next round
        return f"katalog belum bisa dibaca ({e})"
    variants = catalog.get(base_sku) or []
    if not variants:
        return "SKU belum terlihat di hasil search"
    if required_packs:
        present = {v.get("multiplier") for v in variants}
        missing = sorted(set(required_packs) - present)
        if missing:
            return "varian belum lengkap (kurang: " + ", ".join(f"{m}PCS" for m in missing) + ")"
    search_ids_by_product: dict[str, set] = {}
    for base_variants in catalog.values():
        for v in base_variants:
            search_ids_by_product.setdefault(v.get("product_id"), set()).add(v.get("sku_id"))
    for product_id in sorted({v.get("product_id") for v in variants}):
        try:
            detail = client.fetch_product_detail_raw(product_id)
        except Exception as e:  # noqa: BLE001 - best-effort poll, retry next round
            return f"detail produk belum bisa dibaca ({e})"
        detail_ids = {s.get("id") for s in (detail.get("skus") or []) if s.get("id")}
        if detail_ids != search_ids_by_product.get(product_id, set()):
            return "sku_id hasil search belum sama dengan detail produk"
    return None


def _wait_tiktokshop_settled(
    base_sku: str,
    required_packs: list[int] | None = None,
    timeout_s: int = SETTLE_TIMEOUT_S,
    interval_s: int = SETTLE_INTERVAL_S,
    client=None,
    sleep=time.sleep,
) -> bool:
    """Poll until TikTok Shop shows the settled catalog for base_sku.

    Every failure mode of a poll just means "belum siap" — retry next round.
    True when settled, False on timeout. `client` and `sleep` are injectable
    for tests; production lazily imports the real client here so the pure
    parsing above stays importable without platform secrets.
    """
    if client is None:
        sys.path.insert(0, str(PROJECT_ROOT))
        from src import tiktokshop_client as client
    deadline = time.monotonic() + timeout_s
    attempt = 0
    while True:
        attempt += 1
        reason = _settle_check(client, base_sku, required_packs)
        if reason is None:
            print(f"✅ Varian TikTok Shop untuk {base_sku} sudah konsisten (search = detail) — lanjut.")
            return True
        if time.monotonic() >= deadline:
            print(
                f"✗ TikTok Shop untuk {base_sku} belum stabil sampai batas waktu — {reason}",
                file=sys.stderr,
            )
            return False
        print(
            f"⏳ Menunggu varian TikTok Shop siap (SKU {base_sku}): "
            f"percobaan {attempt} — {reason}, tunggu {int(interval_s)} dtk"
        )
        sleep(interval_s)


def _send_batch_abort_alert(text: str) -> None:
    """Best-effort Telegram alert — a send failure must not mask the abort."""
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from src.telegram_sender import send_alert
        send_alert(text, mode="Batch")
    except Exception as e:  # noqa: BLE001 - alert is best-effort
        print(f"  (peringatan Telegram gagal terkirim: {e})", file=sys.stderr)


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

        gate = _gate_after(command, raw_args, commands[idx:])
        if gate is not None:
            base_sku, required_packs = gate
            print()
            print(
                f"⏳ Perintah berikutnya memakai SKU {base_sku} juga — "
                f"menunggu perubahan TikTok Shop stabil dulu..."
            )
            if not _wait_tiktokshop_settled(base_sku, required_packs=required_packs):
                minutes = SETTLE_TIMEOUT_S // 60
                message = (
                    f"Varian TikTok Shop untuk '{base_sku}' belum muncul setelah {minutes} menit "
                    f"— sisa perintah batch dibatalkan, jalankan ulang nanti."
                )
                print(f"✗ {message}", file=sys.stderr)
                _send_batch_abort_alert(message)
                return 1

    print()
    print("=" * 70)
    print("Batch completed.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
