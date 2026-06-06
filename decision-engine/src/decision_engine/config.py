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
from web3 import Web3

from decision_engine.managed_cycle import ManagedCycleConfig
from decision_engine.rebalance import MultiAssetTarget

_PROTOCOL = "aerodrome"  # P1 swap venue on Base

# Assets lent on Aave (the rest are held in the wallet/Safe). Used to build the
# venue-by-asset map the P2.5 exposure checker consults. wstETH/WETH are held.
_LENT_ASSETS = frozenset({"USDC", "USDT", "DAI", "cbBTC"})


def _venue_for(symbol: str) -> str:
    return "aave_v3" if symbol in _LENT_ASSETS else "wallet"


@dataclass(frozen=True)
class ManagedConfig:
    weights: Mapping[str, Decimal]  # asset symbol → target NAV fraction (sum≈1)
    hub: str  # stable funding asset; all corrective trades route through it
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
    # P2.5 exposure caps (NAV fractions). max_asset_pct is a safety net >= the
    # largest upper band; max_venue_pct caps overlay venues; lp_cap is the LP
    # overlay's tighter cap (P3.3).
    max_asset_pct: Decimal = Decimal("0.60")
    max_venue_pct: Decimal = Decimal("0.25")
    lp_cap: Decimal = Decimal("0.15")
    # Operator funding addresses for the PnL deposit-tracker (reporting only).
    # Deposits = inbound to the Safe FROM one of these; withdrawals = outbound
    # TO one of these. Empty → tracker disabled (PnL not computed).
    operator_funding_addresses: frozenset[str] = frozenset()

    def multi_asset_target(self) -> MultiAssetTarget:
        return MultiAssetTarget(weights=dict(self.weights), band=self.band, hub=self.hub)

    def venue_by_asset(self) -> dict[str, str]:
        """Map each target asset to its venue (lent on Aave vs held)."""
        return {sym: _venue_for(sym) for sym in self.weights}

    def cycle_config(self) -> ManagedCycleConfig:
        return ManagedCycleConfig(
            recipient=self.safe_address,
            protocol=_PROTOCOL,
            slippage_bps=self.slippage_bps,
            cost_gate_margin=self.cost_gate_margin,
            gas_units=self.gas_units,
            deadline_seconds=self.deadline_seconds,
            chain_id=self.chain_id,
            venue_by_asset=self.venue_by_asset(),
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
    limits = data.get("limits", {})

    weights = {sym: Decimal(str(w)) for sym, w in alloc["weights"].items()}
    hub = str(alloc["hub"])
    band = Decimal(str(alloc["band"]))
    slippage_bps = int(reb["slippage_bps"])
    # Default 100 bps when [risk] / the key is absent → backward compatible.
    depeg_threshold_bps = int(risk.get("depeg_threshold_bps", 100))

    # Validate the allocation by constructing the target now (fail loud at boot
    # on bad weights/hub/band rather than at the first tick).
    MultiAssetTarget(weights=weights, band=band, hub=hub)
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

    # Optional operator funding addresses (PnL deposit-tracker). Comma-separated;
    # parsed into a frozenset of checksummed addresses. Empty/unset → tracker
    # disabled. An invalid address fails loud (a typo would silently mis-track).
    funding_raw = env.get("OPERATOR_FUNDING_ADDRESSES", "")
    operator_funding_addresses = frozenset(
        Web3.to_checksum_address(part.strip())
        for part in funding_raw.split(",")
        if part.strip()
    )

    return ManagedConfig(
        weights=weights,
        hub=hub,
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
        operator_funding_addresses=operator_funding_addresses,
        max_asset_pct=Decimal(str(limits.get("max_asset_pct", "0.60"))),
        max_venue_pct=Decimal(str(limits.get("per_venue_cap", "0.25"))),
        lp_cap=Decimal(str(limits.get("lp_cap", "0.15"))),
    )


__all__ = ["ManagedConfig", "load_managed_config"]
