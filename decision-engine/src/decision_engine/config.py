"""ManagedConfig — two-tier config for the managed portfolio.

Managed-portfolio P1.5c. Strategy dials come from a versioned TOML file;
secrets + per-deployment values (Safe address, chain) come from env. Loaded
into a frozen, validated dataclass at boot — fails loud on missing/invalid
values (SAFE_ADDRESS, CHAIN, and the weight/band/slippage ranges).
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from icarus.types.market import Chain

from decision_engine.managed_cycle import ManagedCycleConfig
from decision_engine.rebalance import RebalanceTarget

_PROTOCOL = "aerodrome"  # P1 swap venue on Base


@dataclass(frozen=True)
class ManagedConfig:
    crypto_symbol: str
    stable_symbol: str
    crypto_weight: Decimal
    band: Decimal
    slippage_bps: int
    cost_gate_margin: Decimal
    gas_units: int
    deadline_seconds: int
    interval_seconds: int
    safe_address: str
    chain: Chain
    chain_id: int = 8453  # Default to Base mainnet
    depeg_threshold_bps: int = 100  # USDC depeg breaker trips above this deviation

    def rebalance_target(self) -> RebalanceTarget:
        return RebalanceTarget(
            crypto_symbol=self.crypto_symbol,
            stable_symbol=self.stable_symbol,
            crypto_weight=self.crypto_weight,
            band=self.band,
        )

    def cycle_config(self) -> ManagedCycleConfig:
        return ManagedCycleConfig(
            recipient=self.safe_address,
            protocol=_PROTOCOL,
            slippage_bps=self.slippage_bps,
            cost_gate_margin=self.cost_gate_margin,
            gas_units=self.gas_units,
            deadline_seconds=self.deadline_seconds,
            chain_id=self.chain_id,
        )


def load_managed_config(
    toml_path: str | Path, *, env: Mapping[str, str] | None = None
) -> ManagedConfig:
    """Load + validate the managed config from TOML (dials) + env (secrets)."""
    env = env if env is not None else os.environ
    with open(toml_path, "rb") as fh:
        data = tomllib.load(fh)

    alloc = data["allocation"]
    reb = data["rebalance"]
    cad = data["cadence"]
    risk = data.get("risk", {})

    crypto_weight = Decimal(str(alloc["crypto_weight"]))
    band = Decimal(str(alloc["band"]))
    slippage_bps = int(reb["slippage_bps"])
    # Default 100 bps when [risk] / the key is absent → backward compatible.
    depeg_threshold_bps = int(risk.get("depeg_threshold_bps", 100))

    if not (Decimal("0") < crypto_weight < Decimal("1")):
        raise ValueError(f"crypto_weight must be in (0,1), got {crypto_weight}")
    if not (Decimal("0") <= band <= Decimal("0.5")):
        raise ValueError(f"band must be in [0,0.5], got {band}")
    if not (0 <= slippage_bps <= 1000):
        raise ValueError(f"slippage_bps must be in [0,1000], got {slippage_bps}")
    if not (0 < depeg_threshold_bps <= 2000):
        raise ValueError(
            f"depeg_threshold_bps must be in (0,2000], got {depeg_threshold_bps}"
        )

    safe_address = env.get("SAFE_ADDRESS")
    if not safe_address:
        raise RuntimeError("SAFE_ADDRESS env var is required (the wallet receiving swap output).")
    chain: Chain = env.get("CHAIN", "base")  # type: ignore[assignment]
    if chain not in ("base", "solana"):
        raise RuntimeError(f"CHAIN must be 'base' or 'solana', got {chain!r}")
    chain_id = int(env.get("CHAIN_ID", "8453"))

    return ManagedConfig(
        crypto_symbol=str(alloc["crypto_symbol"]),
        stable_symbol=str(alloc["stable_symbol"]),
        crypto_weight=crypto_weight,
        band=band,
        slippage_bps=slippage_bps,
        cost_gate_margin=Decimal(str(reb["cost_gate_margin"])),
        gas_units=int(reb["gas_units"]),
        deadline_seconds=int(reb["deadline_seconds"]),
        interval_seconds=int(cad["interval_seconds"]),
        safe_address=safe_address,
        chain=chain,
        chain_id=chain_id,
        depeg_threshold_bps=depeg_threshold_bps,
    )


__all__ = ["ManagedConfig", "load_managed_config"]
