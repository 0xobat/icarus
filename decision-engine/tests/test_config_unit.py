"""Unit tests for ManagedConfig (managed-portfolio P1.5c)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from decision_engine.config import ManagedConfig, load_managed_config

_TOML = """
[allocation]
crypto_symbol = "WETH"
stable_symbol = "USDC"
crypto_weight = 0.6
band = 0.10
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
    assert cfg.crypto_weight == Decimal("0.6")
    assert cfg.band == Decimal("0.10")
    assert cfg.slippage_bps == 50
    assert cfg.cost_gate_margin == Decimal("4")
    assert cfg.interval_seconds == 3600
    assert cfg.safe_address == _ENV["SAFE_ADDRESS"]
    assert cfg.chain == "base"


def test_derives_rebalance_target_and_cycle_config(tmp_path: Path) -> None:
    cfg = load_managed_config(_write(tmp_path), env=_ENV)
    target = cfg.rebalance_target()
    assert target.crypto_symbol == "WETH"
    assert target.stable_symbol == "USDC"
    assert target.crypto_weight == Decimal("0.6")
    assert target.band == Decimal("0.10")
    cc = cfg.cycle_config()
    assert cc.recipient == _ENV["SAFE_ADDRESS"]
    assert cc.protocol == "aerodrome"
    assert cc.slippage_bps == 50
    assert cc.gas_units == 200000


def test_missing_safe_address_fails_loud(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="SAFE_ADDRESS"):
        load_managed_config(_write(tmp_path), env={"CHAIN": "base"})


def test_invalid_weight_fails_loud(tmp_path: Path) -> None:
    bad = _TOML.replace("crypto_weight = 0.6", "crypto_weight = 1.5")
    with pytest.raises(ValueError, match="crypto_weight"):
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


# ── Task 4A: chain_id threading ────────────────────────────────────────────────

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
