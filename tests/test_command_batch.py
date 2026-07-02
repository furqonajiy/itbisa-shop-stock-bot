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
    _gate_after,
    _wait_tiktokshop_settled,
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


# ---------------------------------------------------------------------------
# Settle gate decision — which commands must wait for TikTok Shop, and how
# ---------------------------------------------------------------------------

def _line(text):
    [(command, args, raw)] = parse_command_lines(text)
    return command, args, raw


def test_varian_set_followed_by_same_sku_gates_with_packs():
    remaining = [_line("/harga_set ITBISA-A 1 749 50 739"), _line("/stok_set ITBISA-A 100")]
    assert _gate_after("/varian_set", ["itbisa-a", "1", "20", "50"], remaining) == (
        "ITBISA-A",
        [1, 20, 50],
    )


def test_berat_set_followed_by_same_sku_gates_without_packs():
    remaining = [_line("/stok_set ITBISA-A 100")]
    assert _gate_after("/berat_set", ["itbisa-a", "1000pcs", "1700g"], remaining) == (
        "ITBISA-A",
        None,
    )


def test_mutation_with_no_later_same_sku_line_needs_no_gate():
    remaining = [_line("/stok_set ITBISA-B 100"), _line("/stok_low")]
    assert _gate_after("/varian_set", ["ITBISA-A", "1", "20"], remaining) is None
    assert _gate_after("/varian_set", ["ITBISA-A", "1", "20"], []) is None


def test_dry_run_mutation_never_gates():
    remaining = [_line("/stok_set ITBISA-A 100")]
    assert _gate_after("/varian_set", ["ITBISA-A", "1", "20", "dry"], remaining) is None
    assert _gate_after("/berat_set", ["ITBISA-A", "1000", "1700g", "dry"], remaining) is None


def test_non_mutating_commands_never_gate():
    remaining = [_line("/stok_get ITBISA-A")]
    assert _gate_after("/stok_get", ["ITBISA-A"], remaining) is None
    assert _gate_after("/stok_set", ["ITBISA-A", "100"], remaining) is None
    assert _gate_after("/harga_set", ["ITBISA-A", "1", "749"], remaining) is None


def test_later_line_sku_match_is_case_insensitive():
    remaining = [_line("/stok_get itbisa-a")]
    assert _gate_after("/varian_set", ["ITBISA-A", "1", "20"], remaining) == (
        "ITBISA-A",
        [1, 20],
    )


# ---------------------------------------------------------------------------
# Settle polling — injected fake client, no network, no real sleeps
# ---------------------------------------------------------------------------

class _FakeClient:
    """Serves catalog snapshots in order (last one repeats) + fixed details."""

    def __init__(self, catalogs, details):
        self._catalogs = catalogs
        self._details = details
        self.calls = 0

    def fetch_catalog(self):
        snapshot = self._catalogs[min(self.calls, len(self._catalogs) - 1)]
        self.calls += 1
        return snapshot

    def fetch_product_detail_raw(self, product_id):
        detail = self._details[product_id]
        if isinstance(detail, Exception):
            raise detail
        return detail


def _variant(mult, sku_id, product_id="P1"):
    return {"multiplier": mult, "sku_id": sku_id, "product_id": product_id}


def test_wait_settles_once_packs_and_sku_ids_agree():
    stale = {"ITBISA-A": [_variant(1, "old-1")]}
    fresh = {"ITBISA-A": [_variant(1, "new-1"), _variant(20, "new-20")]}
    details = {"P1": {"skus": [{"id": "new-1"}, {"id": "new-20"}]}}
    client = _FakeClient([stale, fresh], details)
    assert _wait_tiktokshop_settled(
        "ITBISA-A", required_packs=[1, 20],
        timeout_s=60, interval_s=0, client=client, sleep=lambda _s: None,
    ) is True
    assert client.calls == 2


def test_wait_times_out_when_requested_pack_never_appears():
    stale = {"ITBISA-A": [_variant(1, "old-1")]}
    details = {"P1": {"skus": [{"id": "old-1"}]}}
    client = _FakeClient([stale], details)
    assert _wait_tiktokshop_settled(
        "ITBISA-A", required_packs=[1, 20],
        timeout_s=0, interval_s=0, client=client, sleep=lambda _s: None,
    ) is False


def test_wait_detects_reissued_sku_ids_via_search_detail_mismatch():
    # The search snapshot still returns the pre-edit (dead) sku_id while the
    # detail already shows the reissued one — not settled.
    search = {"ITBISA-A": [_variant(1, "dead-1")]}
    details = {"P1": {"skus": [{"id": "new-1"}]}}
    client = _FakeClient([search], details)
    assert _wait_tiktokshop_settled(
        "ITBISA-A", timeout_s=0, interval_s=0, client=client, sleep=lambda _s: None,
    ) is False


def test_wait_counts_sibling_skus_grouped_under_other_bases():
    # ITBISA-BUBBLE-WRAP shares the product but groups under its own base
    # key, so the search sku_id set must be collected catalog-wide.
    catalog = {
        "ITBISA-A": [_variant(1, "a-1"), _variant(20, "a-20")],
        "ITBISA-BUBBLE-WRAP": [_variant(1, "bw-1")],
    }
    details = {"P1": {"skus": [{"id": "a-1"}, {"id": "a-20"}, {"id": "bw-1"}]}}
    client = _FakeClient([catalog], details)
    assert _wait_tiktokshop_settled(
        "ITBISA-A", required_packs=[1, 20],
        timeout_s=0, interval_s=0, client=client, sleep=lambda _s: None,
    ) is True


def test_wait_retries_after_detail_read_failure():
    catalog = {"ITBISA-A": [_variant(1, "a-1")]}
    client = _FakeClient([catalog], {"P1": RuntimeError("HTTP 500")})
    assert _wait_tiktokshop_settled(
        "ITBISA-A", timeout_s=0, interval_s=0, client=client, sleep=lambda _s: None,
    ) is False
