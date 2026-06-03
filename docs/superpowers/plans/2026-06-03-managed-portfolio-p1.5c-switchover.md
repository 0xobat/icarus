# Managed Portfolio P1.5c — Live Switchover Plan (DRAFT for review)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Checkbox (`- [ ]`) steps.
> **Review status:** DRAFT — review before execution. Tasks 1–3 are unit-testable and autonomously buildable. **Task 4 flips the live service** and its full acceptance needs a running stack + testnet wallet — that step is operator-run.

**Goal:** Switch the decision-engine from the lake `DecisionCycle` to the `ManagedPortfolioCycle`, fed by real prices (`RpcAdapter`) and real on-chain holdings (`RpcHoldingsProvider`), with a two-tier config (`ManagedConfig`), per-tick breaker feeds, an append-only trade log, and the results-consumer loop — then observe one real rebalance round-trip on Base Sepolia.

**Locked decisions:** (1) testnet first (Base Sepolia), (2) on-chain `balanceOf` holdings, (3) **balance-truth-from-chain** + an append-only trade log (reuses the existing `Trade` model — no source-of-truth ledger), (4) **two-tier config** — env for secrets/deployment, versioned `config/managed.toml` for strategy dials → a frozen, validated `ManagedConfig`, (5) Base-only.

---

## Contracts already verified

- **Breakers (feed live each tick):** `DrawdownBreaker.update(portfolio_value: Decimal)`; `GasSpikeBreaker.update(current_gas: Decimal, average_gas: Decimal)`; tx-failure via the P1.5b `ResultsConsumer`. (Read in `decision_engine/risk/{drawdown_breaker,gas_spike_breaker,tx_failure_monitor}.py`.)
- **Adapter:** `RpcAdapter()` (from `icarus.data_adapters`) reads `ALCHEMY_BASE_HTTP_URL`, returns `MarketSnapshot` with real `prices["ETH"]` (Chainlink) + `gas_gwei`. **Use this, not `build_default_adapter()`** (which is the zeroed DefiLlama).
- **Trade log:** the existing `Trade` ORM model (`icarus.db.models.Trade`, table `trades`) already has `trade_id, correlation_id, timestamp, strategy, protocol, chain, action, asset_in, asset_out, amount_in, amount_out, price_at_execution, gas_used, gas_price_wei, slippage_bps, tx_hash, status, ...`. Reuse it.
- **Cycle (P1.4):** `ManagedPortfolioCycle(adapter, holdings, target, risk_gate, publisher, config)`, `RebalanceTarget(crypto_symbol, stable_symbol, crypto_weight, band)`, `ManagedCycleConfig(recipient, protocol, slippage_bps, cost_gate_margin, gas_units, deadline_seconds)`.
- **Holdings (P1.5a):** `RpcHoldingsProvider(w3, adapter, safe_address, crypto_symbol, stable_symbol, chain)`.
- **Results consumer (P1.5b):** `ResultsConsumer(tx_failure)`, `handle_result`, `run(pubsub)`.
- **Current `__main__`** wires the lake path (RosterListener + TemplateRegistry + `_LiveAllocator` + `DecisionCycle`, lines 179–276) under the roster-coupled `DecisionEngine` loop (76–122). Keep the risk-module construction (186–210), the `DECISION_ENGINE_TOTAL_CAPITAL_USD` fail-loud guard, Redis, DB.

---

## Task 1 — `ManagedConfig` (TOML dials + env secrets, validated, frozen) — UNIT-TESTABLE

**Files:** Create `decision-engine/src/decision_engine/config.py`; Create `config/managed.toml`; Test `decision-engine/tests/test_config_unit.py`.

`config/managed.toml`:
```toml
# Managed-portfolio strategy dials. Secrets + per-deployment values (RPC URL,
# wallet key, Safe address, chain) come from env, NOT this file.
[allocation]
crypto_symbol = "WETH"
stable_symbol = "USDC"
crypto_weight = 0.6      # strategic target fraction in crypto
band = 0.10              # +/- absolute-weight tolerance before rebalancing

[rebalance]
slippage_bps = 50
cost_gate_margin = 4     # rebalance only if correction >= margin * est cost
gas_units = 200000
deadline_seconds = 60

[cadence]
interval_seconds = 3600  # hourly read; act only on band breach

# Reserved for later phases (parsed but unused in P1):
[limits]
lp_cap = 0.15            # P3 LP overlay cap
per_venue_cap = 0.25     # P2 per-venue exposure cap
```

- [ ] **Step 1: Failing test** — `decision-engine/tests/test_config_unit.py`:

```python
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
```

- [ ] **Step 2: Run → fails** (`ModuleNotFoundError`).
- [ ] **Step 3: Implement** `decision-engine/src/decision_engine/config.py`:

```python
"""ManagedConfig — two-tier config for the managed portfolio.

Managed-portfolio P1.5c. Strategy dials come from a versioned TOML file;
secrets + per-deployment values (Safe address, chain) come from env. Loaded
into a frozen, validated dataclass at boot — fails loud on missing/invalid
values, matching the DECISION_ENGINE_TOTAL_CAPITAL_USD guard.
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

    crypto_weight = Decimal(str(alloc["crypto_weight"]))
    band = Decimal(str(alloc["band"]))
    slippage_bps = int(reb["slippage_bps"])

    if not (Decimal("0") < crypto_weight < Decimal("1")):
        raise ValueError(f"crypto_weight must be in (0,1), got {crypto_weight}")
    if not (Decimal("0") <= band <= Decimal("0.5")):
        raise ValueError(f"band must be in [0,0.5], got {band}")
    if not (0 <= slippage_bps <= 1000):
        raise ValueError(f"slippage_bps must be in [0,1000], got {slippage_bps}")

    safe_address = env.get("SAFE_ADDRESS")
    if not safe_address:
        raise RuntimeError("SAFE_ADDRESS env var is required (the wallet receiving swap output).")
    chain: Chain = env.get("CHAIN", "base")  # type: ignore[assignment]

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
    )


__all__ = ["ManagedConfig", "load_managed_config"]
```

- [ ] **Step 4: Run → 5 passed.** **Step 5: Commit** `feat(daedalus): P1.5c ManagedConfig — two-tier TOML+env config`.

---

## Task 2 — Trade-log persistence in `ResultsConsumer` — UNIT-TESTABLE

Extend `ResultsConsumer` to append a `Trade` row on every result via an injected **trade sink** (a callable), so the core stays unit-testable (fake sink) and `__main__` passes the DB-backed sink. Reuses the existing `Trade` model — no new table.

**Files:** Modify `decision-engine/src/decision_engine/results_consumer.py`; Modify `decision-engine/tests/test_results_consumer_unit.py`.

- [ ] **Step 1: Failing test** — append:

```python
def test_handle_result_appends_a_trade_record() -> None:
    monitor = TxFailureMonitor()
    captured: list[dict] = []
    consumer = ResultsConsumer(tx_failure=monitor, trade_sink=captured.append)
    consumer.handle_result(_result("confirmed"))
    assert len(captured) == 1
    rec = captured[0]
    assert rec["order_id"] == "o1"
    assert rec["status"] == "confirmed"
    assert rec["chain"] == "base"


def test_trade_sink_optional() -> None:
    # No sink → no crash (backward compatible with P1.5b tests).
    consumer = ResultsConsumer(tx_failure=TxFailureMonitor())
    consumer.handle_result(_result("confirmed"))  # must not raise
```

- [ ] **Step 2: Run → fails** (`TypeError: unexpected keyword 'trade_sink'`).
- [ ] **Step 3: Implement** — change `ResultsConsumer.__init__` to `(*, tx_failure, trade_sink: Callable[[dict], None] | None = None)`; in `handle_result`, after the monitor routing, if `self._trade_sink` is set call it with a dict projected from the result (`order_id, correlation_id, chain, status, tx_hash, amount_out, fill_price, timestamp`). Keep the audit log. (Exact projection dict shown in the test above; mirror the `Trade` columns.)
- [ ] **Step 4: Run → 9 passed.** **Step 5: Commit** `feat(daedalus): P1.5c results consumer — append-only trade-log sink`.

> The DB-backed sink (a small function that opens a session and writes a `Trade` row from the dict, reusing `icarus.db.models.Trade`) is wired in Task 4 and exercised live; the unit tests use a list sink.

---

## Task 3 — Rolling gas-average tracker (for the gas-spike feed) — UNIT-TESTABLE

`GasSpikeBreaker.update(current, average)` needs an average the worker maintains. A small EMA over recent ticks.

**Files:** Create `decision-engine/src/decision_engine/gas_tracker.py`; Test `decision-engine/tests/test_gas_tracker_unit.py`.

- [ ] **Step 1: Failing test:**

```python
from decimal import Decimal
from decision_engine.gas_tracker import GasAverageTracker

def test_first_sample_is_its_own_average() -> None:
    t = GasAverageTracker(alpha=Decimal("0.2"))
    assert t.update(Decimal("10")) == Decimal("10")

def test_ema_converges_toward_samples() -> None:
    t = GasAverageTracker(alpha=Decimal("0.5"))
    t.update(Decimal("10"))
    avg = t.update(Decimal("20"))  # 0.5*20 + 0.5*10 = 15
    assert avg == Decimal("15")
    assert t.average == Decimal("15")
```

- [ ] **Step 2: Run → fails.** **Step 3: Implement** `gas_tracker.py`:

```python
"""Rolling gas-average tracker (EMA) for the gas-spike breaker feed (P1.5c)."""

from __future__ import annotations

from decimal import Decimal


class GasAverageTracker:
    """Exponential moving average of gas price (gwei). First sample seeds it."""

    def __init__(self, *, alpha: Decimal = Decimal("0.1")) -> None:
        if not (Decimal("0") < alpha <= Decimal("1")):
            raise ValueError(f"alpha must be in (0,1], got {alpha}")
        self._alpha = alpha
        self._avg: Decimal | None = None

    @property
    def average(self) -> Decimal:
        return self._avg if self._avg is not None else Decimal("0")

    def update(self, sample: Decimal) -> Decimal:
        self._avg = sample if self._avg is None else self._alpha * sample + (Decimal("1") - self._alpha) * self._avg
        return self._avg


__all__ = ["GasAverageTracker"]
```

- [ ] **Step 4: Run → 2 passed.** **Step 5: Commit** `feat(daedalus): P1.5c gas-average tracker (EMA) for breaker feed`.

---

## Task 4 — `__main__` switchover (LIVE — operator-run acceptance)

Rewire `decision-engine/src/decision_engine/__main__.py` from the lake path to the managed path. **This changes live service behavior; full acceptance is the operator's testnet run.**

- [ ] **Remove** the lake wiring: `RosterListener`, `TemplateRegistry`/`build_db_verdict_lookup`, `_LiveAllocator`/`ComposedAllocator`/`_empty_returns_lookup`, `RulesRegimeClassifier`, the `DecisionCycle` construction, and the late-binding stubs. (Leave those modules in the tree; just stop importing/wiring them here.)
- [ ] **Keep** the risk-module construction (`DrawdownBreaker`, `ExposureLimiter` + the `DECISION_ENGINE_TOTAL_CAPITAL_USD` fail-loud guard, `GasSpikeBreaker`, `PositionLossLimit`, `TxFailureMonitor`), `_build_risk_gate`, Redis, DB (`db.create_tables()` now also creates the `trades` table).
- [ ] **Add** construction:
  - `config = load_managed_config(os.environ.get("MANAGED_CONFIG_PATH", "/app/config/managed.toml"))`
  - `adapter = RpcAdapter()`  *(reads `ALCHEMY_BASE_HTTP_URL`)*
  - one shared `AsyncWeb3` for holdings (reuse the adapter's provider or build from the same URL)
  - `holdings = RpcHoldingsProvider(w3=..., adapter=adapter, safe_address=config.safe_address, crypto_symbol=config.crypto_symbol, stable_symbol=config.stable_symbol, chain=config.chain)`
  - `cycle = ManagedPortfolioCycle(adapter=adapter, holdings=holdings, target=config.rebalance_target(), risk_gate=risk_gate, publisher=RedisExecutorPublisher(redis_client), config=config.cycle_config())`
  - `gas_tracker = GasAverageTracker()`
  - a DB-backed `trade_sink` (writes a `Trade` row from the projection dict) and `consumer = ResultsConsumer(tx_failure=tx_failure, trade_sink=trade_sink)`
- [ ] **Replace the worker loop** (`DecisionEngine`) with a managed loop:
  - background task: subscribe Redis to `execution:results:{chain}` and run `consumer.run(pubsub)`.
  - each tick (interval `config.interval_seconds`): fetch a `MarketSnapshot` once; `gas_spike.update(market.gas_gwei, gas_tracker.update(market.gas_gwei))`; `crypto_usd, stable_usd = await holdings.current_usd_holdings()`; `drawdown.update(crypto_usd + stable_usd)`; `await cycle.run_one()`; structured tick log. Keep SIGTERM/SIGINT handling.
- [ ] **Manual verification (operator, Base Sepolia)** — the acceptance criteria from the P1.5 brief: real non-zero balances + ETH price; an out-of-band holdings state emits one correct order; ts-executor executes a real testnet swap; result lands on `execution:results:base`, a `Trade` row is written, the tx-failure monitor sees the success; next tick reads post-swap balances and holds; a tiny drift is cost-gated; gas/drawdown breakers show live (non-zero) state.

> Task 4 has no unit test that can prove the live behavior; it is validated by the operator run. A smoke-level test (construct the managed loop with stub collaborators and assert one tick wires the feeds) MAY be added, but the real gate is the testnet round-trip.

---

## Build order & review
Tasks 1–3 are autonomously buildable + unit-tested now (subagent-driven, same as P1.1–P1.5b). Task 4 is written, then **executed with the operator** against a Base Sepolia deployment. After Task 4's operator run passes, P1 is complete and a cleanup phase removes the now-dead lake modules.
