"""Unit tests for ManagedConfig (managed-portfolio P2.2 multi-asset)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from decision_engine.config import ManagedConfig, load_managed_config

_TOML = """
[allocation]
hub = "USDC"
band = 0.10
[allocation.weights]
USDC = 0.40
WETH = 0.60
[rebalance]
slippage_bps = 50
cost_gate_margin = 4
gas_units = 200000
deadline_seconds = 60
[cadence]
interval_seconds = 3600
[limits]
lp_cap = 0.15
per_venue_cap = 0.25
"""

_ENV = {"SAFE_ADDRESS": "0x1111111111111111111111111111111111111111", "CHAIN": "base"}


def _write(tmp_path: Path, body: str = _TOML) -> Path:
    p = tmp_path / "managed.toml"
    p.write_text(body)
    return p


def test_load_builds_validated_config(tmp_path: Path) -> None:
    cfg = load_managed_config(_write(tmp_path), env=_ENV)
    assert isinstance(cfg, ManagedConfig)
    assert cfg.band == Decimal("0.10")
    assert cfg.hub == "USDC"
    assert cfg.weights == {"USDC": Decimal("0.40"), "WETH": Decimal("0.60")}
    assert cfg.slippage_bps == 50
    assert cfg.cost_gate_margin == Decimal("4")
    assert cfg.interval_seconds == 3600
    assert cfg.safe_address == _ENV["SAFE_ADDRESS"]
    assert cfg.chain == "base"


def test_derives_multi_asset_target_and_cycle_config(tmp_path: Path) -> None:
    cfg = load_managed_config(_write(tmp_path), env=_ENV)
    target = cfg.multi_asset_target()
    assert target.hub == "USDC"
    assert target.band == Decimal("0.10")
    assert target.weights == {"USDC": Decimal("0.40"), "WETH": Decimal("0.60")}
    cc = cfg.cycle_config()
    assert cc.recipient == _ENV["SAFE_ADDRESS"]
    assert cc.protocol == "aerodrome"
    assert cc.slippage_bps == 50
    assert cc.gas_units == 200000


def test_exposure_caps_and_venue_map(tmp_path: Path) -> None:
    # [limits] max_asset_pct + per_venue_cap → ManagedConfig caps; venue map
    # marks USDC lent (aave_v3) and WETH held (wallet). The base _TOML omits
    # max_asset_pct → default 0.80 (>= WETH 0.60 + band 0.10 = 0.70 invariant).
    cfg = load_managed_config(_write(tmp_path), env=_ENV)
    assert cfg.max_asset_pct == Decimal("0.80")
    assert cfg.max_venue_pct == Decimal("0.25")
    assert cfg.venue_by_asset() == {"USDC": "aave_v3", "WETH": "wallet"}
    assert cfg.cycle_config().venue_by_asset == {"USDC": "aave_v3", "WETH": "wallet"}


def test_exposure_caps_default_when_limits_absent(tmp_path: Path) -> None:
    # The base _TOML carries [limits]; a config without it falls back to defaults.
    body = "\n".join(
        line for line in _TOML.splitlines()
        if not line.startswith(("[limits]", "lp_cap", "per_venue_cap", "max_asset_pct"))
    )
    cfg = load_managed_config(_write(tmp_path, body), env=_ENV)
    assert cfg.max_asset_pct == Decimal("0.80")
    assert cfg.max_venue_pct == Decimal("0.25")


def test_max_asset_pct_below_largest_upper_band_fails_loud(tmp_path: Path) -> None:
    # WETH 0.60 + band 0.10 = 0.70 upper; a 0.65 cap would block a legitimate
    # rebalance to the band edge → must fail loud at boot.
    bad = _TOML.replace("[limits]", "[limits]\nmax_asset_pct = 0.65", 1)
    with pytest.raises(ValueError, match="max_asset_pct"):
        load_managed_config(_write(tmp_path, bad), env=_ENV)


def test_three_asset_target(tmp_path: Path) -> None:
    body = """
[allocation]
hub = "USDC"
band = 0.10
[allocation.weights]
USDC = 0.40
WETH = 0.32
WBTC = 0.28
[rebalance]
slippage_bps = 50
cost_gate_margin = 4
gas_units = 200000
deadline_seconds = 60
[cadence]
interval_seconds = 3600
"""
    cfg = load_managed_config(_write(tmp_path, body), env=_ENV)
    target = cfg.multi_asset_target()
    assert set(target.weights) == {"USDC", "WETH", "WBTC"}
    assert target.weights["WBTC"] == Decimal("0.28")


def test_missing_safe_address_fails_loud(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="SAFE_ADDRESS"):
        load_managed_config(_write(tmp_path), env={"CHAIN": "base"})


def test_weights_not_summing_to_one_fails_loud(tmp_path: Path) -> None:
    bad = _TOML.replace("WETH = 0.60", "WETH = 0.80")  # sum 1.20
    with pytest.raises(ValueError, match="sum"):
        load_managed_config(_write(tmp_path, bad), env=_ENV)


def test_hub_not_in_weights_fails_loud(tmp_path: Path) -> None:
    bad = _TOML.replace('hub = "USDC"', 'hub = "DAI"')
    with pytest.raises(ValueError, match="hub"):
        load_managed_config(_write(tmp_path, bad), env=_ENV)


def test_invalid_band_fails_loud(tmp_path: Path) -> None:
    bad = _TOML.replace("band = 0.10", "band = 0.9")
    with pytest.raises(ValueError, match="band"):
        load_managed_config(_write(tmp_path, bad), env=_ENV)


def test_invalid_slippage_fails_loud(tmp_path: Path) -> None:
    bad = _TOML.replace("slippage_bps = 50", "slippage_bps = 1001")
    with pytest.raises(ValueError, match="slippage_bps"):
        load_managed_config(_write(tmp_path, bad), env=_ENV)


def test_invalid_chain_fails_loud(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="CHAIN"):
        load_managed_config(
            _write(tmp_path),
            env={"SAFE_ADDRESS": _ENV["SAFE_ADDRESS"], "CHAIN": "testnet"},
        )


# ── chain_id threading ───────────────────────────────────────────────────────

def test_chain_id_defaults_to_mainnet(tmp_path: Path) -> None:
    cfg = load_managed_config(_write(tmp_path), env=_ENV)
    assert cfg.chain_id == 8453


def test_chain_id_from_env(tmp_path: Path) -> None:
    env = {**_ENV, "CHAIN_ID": "84532"}
    cfg = load_managed_config(_write(tmp_path), env=env)
    assert cfg.chain_id == 84532


def test_cycle_config_inherits_chain_id(tmp_path: Path) -> None:
    env = {**_ENV, "CHAIN_ID": "84532"}
    cfg = load_managed_config(_write(tmp_path), env=env)
    cc = cfg.cycle_config()
    assert cc.chain_id == 84532


# ── Depeg threshold ─────────────────────────────────────────────────────────

def test_depeg_threshold_defaults_to_100_when_absent(tmp_path: Path) -> None:
    cfg = load_managed_config(_write(tmp_path), env=_ENV)
    assert cfg.depeg_threshold_bps == 100


def test_depeg_threshold_read_from_toml(tmp_path: Path) -> None:
    body = _TOML + "\n[risk]\ndepeg_threshold_bps = 75\n"
    cfg = load_managed_config(_write(tmp_path, body), env=_ENV)
    assert cfg.depeg_threshold_bps == 75


def test_depeg_threshold_rejects_zero(tmp_path: Path) -> None:
    body = _TOML + "\n[risk]\ndepeg_threshold_bps = 0\n"
    with pytest.raises(ValueError, match="depeg_threshold_bps"):
        load_managed_config(_write(tmp_path, body), env=_ENV)


def test_depeg_threshold_rejects_too_large(tmp_path: Path) -> None:
    body = _TOML + "\n[risk]\ndepeg_threshold_bps = 2001\n"
    with pytest.raises(ValueError, match="depeg_threshold_bps"):
        load_managed_config(_write(tmp_path, body), env=_ENV)


# ── Operator funding addresses (PnL deposit-tracker) ─────────────────────────

_ADDR_A = "0x000000000000000000000000000000000000dEaD"
_ADDR_B = "0x52908400098527886E0F7030069857D2E4169EE7"


def test_funding_addresses_empty_when_unset(tmp_path: Path) -> None:
    cfg = load_managed_config(_write(tmp_path), env=_ENV)
    assert cfg.operator_funding_addresses == frozenset()


def test_funding_addresses_parsed_and_checksummed(tmp_path: Path) -> None:
    env = {**_ENV, "OPERATOR_FUNDING_ADDRESSES": f"{_ADDR_A.lower()},{_ADDR_B.lower()}"}
    cfg = load_managed_config(_write(tmp_path), env=env)
    assert cfg.operator_funding_addresses == frozenset({_ADDR_A, _ADDR_B})


def test_funding_addresses_tolerate_whitespace_and_blanks(tmp_path: Path) -> None:
    env = {**_ENV, "OPERATOR_FUNDING_ADDRESSES": f"  {_ADDR_A} , , {_ADDR_B}  "}
    cfg = load_managed_config(_write(tmp_path), env=env)
    assert cfg.operator_funding_addresses == frozenset({_ADDR_A, _ADDR_B})


def test_funding_addresses_reject_invalid(tmp_path: Path) -> None:
    env = {**_ENV, "OPERATOR_FUNDING_ADDRESSES": "not-an-address"}
    with pytest.raises(ValueError):
        load_managed_config(_write(tmp_path), env=env)
