"""Unit tests for the batch command runner's parsing (pure logic).

Covers the Indonesian primary command names (/stok_set, /stok_get,
/stok_balance, /stok_low, /varian_set, /berat_set) and the legacy English
aliases (/stock_set, /stock_get, /stock_balance, /stock_low, /variant_set,
/weight_set), which stay accepted forever. No subprocess is spawned here —
only the line parsing and invocation building are tested.
"""

import pytest

from scripts.command_batch import (
    COMMAND_ALIASES,
    SUPPORTED_COMMANDS,
    build_invocation,
    parse_command_lines,
)


# ---------------------------------------------------------------------------
# Alias map / supported set
# ---------------------------------------------------------------------------

def test_every_alias_maps_to_a_supported_primary():
    for alias, primary in COMMAND_ALIASES.items():
        assert primary in SUPPORTED_COMMANDS
        assert alias not in SUPPORTED_COMMANDS  # normalized before the gate


def test_supported_set_is_the_primary_names():
    assert SUPPORTED_COMMANDS == {
        "/stok_set",
        "/stok_get",
        "/stok_balance",
        "/stok_low",
        "/harga_set",
        "/varian_set",
        "/berat_set",
    }


# ---------------------------------------------------------------------------
# parse_command_lines — primary names, aliases, normalization
# ---------------------------------------------------------------------------

def test_parse_primary_and_alias_lines_normalize_to_primary():
    text = "\n".join([
        "/stok_set ITBISA-A 100",
        "/stock_set ITBISA-B 200",
        "/varian_set ITBISA-C 1 20",
        "/variant_set ITBISA-D 1 20",
        "/berat_set ITBISA-E 1000 1700g",
        "/weight_set ITBISA-F 1000 1700g",
    ])
    commands = [c for c, _args, _raw in parse_command_lines(text)]
    assert commands == [
        "/stok_set", "/stok_set",
        "/varian_set", "/varian_set",
        "/berat_set", "/berat_set",
    ]


def test_parse_strips_bot_username_suffix_on_new_names():
    commands = parse_command_lines("/stok_get@ITBisaShopBot 555")
    assert commands == [("/stok_get", ["555"], "/stok_get@ITBisaShopBot 555")]


def test_parse_rejects_unsupported_command():
    with pytest.raises(ValueError, match="unsupported command"):
        parse_command_lines("/resi_all")


# ---------------------------------------------------------------------------
# build_invocation — same CLI script for primary name and legacy alias
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", ["/stok_set", "/stock_set"])
def test_stok_set_dispatches_price_aware_runner(command):
    cmd = build_invocation(command, ["itbisa-a", "100", "ITBISA-B", "200", "dry"])
    assert cmd[1] == "scripts/stock_set_price.py"
    assert cmd[2:] == ["--sku", "ITBISA-A", "ITBISA-B", "--pieces", "100", "200", "--dry-run"]


@pytest.mark.parametrize("command", ["/stok_get", "/stock_get"])
def test_stok_get_dispatches_stock_get(command):
    cmd = build_invocation(command, ["555", "ne555"])
    assert cmd[1] == "scripts/stock_get.py"
    assert cmd[2:] == ["--sku", "555\nNE555"]


@pytest.mark.parametrize("command", ["/stok_balance", "/stock_balance"])
def test_stok_balance_dispatches_stock_balance(command):
    cmd = build_invocation(command, ["itbisa-a", "dry"])
    assert cmd[1] == "scripts/stock_balance.py"
    assert cmd[2:] == ["--sku", "ITBISA-A", "--dry-run"]


@pytest.mark.parametrize("command", ["/stok_low", "/stock_low"])
def test_stok_low_dispatches_stock_low(command):
    cmd = build_invocation(command, [])
    assert cmd[1] == "scripts/stock_low.py"


@pytest.mark.parametrize("command", ["/varian_set", "/variant_set"])
def test_varian_set_dispatches_variant_set(command):
    cmd = build_invocation(command, ["itbisa-a", "1", "20", "50"])
    assert cmd[1] == "scripts/variant_set.py"
    assert cmd[2:] == ["--sku", "ITBISA-A", "--packs", "1", "20", "50"]


@pytest.mark.parametrize("command", ["/berat_set", "/weight_set"])
def test_berat_set_dispatches_weight_set(command):
    # /berat_set was previously missing from SUPPORTED_COMMANDS even though the
    # Telegram Worker routes it into batches — both spellings must work.
    cmd = build_invocation(command, ["itbisa-a", "1000pcs", "1700g", "dry"])
    assert cmd[1] == "scripts/weight_set.py"
    assert cmd[2:] == ["--sku", "ITBISA-A", "--ref-pcs", "1000", "--grams", "1700", "--dry-run"]


def test_harga_set_unchanged():
    cmd = build_invocation("/harga_set", ["itbisa-a", "1", "749", "50", "739"])
    assert cmd[1] == "scripts/harga_set.py"
    assert cmd[2:] == ["--sku", "ITBISA-A", "--tiers", "1", "749", "50", "739"]


# ---------------------------------------------------------------------------
# Malformed lines still abort — error text names the canonical command
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", ["/stok_set", "/stock_set"])
def test_stok_set_odd_tokens_rejected_with_canonical_name(command):
    with pytest.raises(ValueError, match="/stok_set requires SKU JUMLAH pairs"):
        build_invocation(command, ["ITBISA-A"])


@pytest.mark.parametrize("command", ["/berat_set", "/weight_set"])
def test_berat_set_bad_args_rejected_with_canonical_name(command):
    with pytest.raises(ValueError, match="/berat_set requires SKU REF_PCS BERAT"):
        build_invocation(command, ["ITBISA-A", "1000"])
    with pytest.raises(ValueError, match="/berat_set REF_PCS and BERAT"):
        build_invocation(command, ["ITBISA-A", "1000kg", "1700g"])


def test_unknown_command_rejected():
    with pytest.raises(ValueError, match="Unsupported command"):
        build_invocation("/resi_all", [])
