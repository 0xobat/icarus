# Managed Portfolio — Risk-Gate Right-Sizing (drop lake-era checkers + capital env var)

> **This changes live service behavior** (the managed risk gate). Unit-tested at the gate/cycle seams; the checker *classes* and their tests are unchanged.

**Goal:** Remove the two lake-era checkers — `ExposureChecker` and `PositionLossChecker` — from the **managed** risk gate, and retire the `DECISION_ENGINE_TOTAL_CAPITAL_USD` env var (the exposure limiter was its only consumer). In the managed model the allocation **target + bands are the concentration policy**; the per-protocol/per-asset exposure caps don't fit (position-blind as wired, and "aerodrome exposure" is a category error), and the per-*strategy* position-loss cooldown is meaningless when every order is `REBAL:base` (one loss would freeze all rebalancing).

**Decision of record:** Option A from the 2026-06-05 design discussion. Keep the gate to the NAV/market-level breakers that DO fit: **drawdown + gas-spike + tx-failure**. A depeg breaker joins them next phase.

**Scope discipline (archive-first / leave-in-tree):**
- Do NOT delete `exposure_limits.py`, `position_loss_limit.py`, `ExposureChecker`, or `PositionLossChecker`. They stay exported + tested; P2 re-introduces exposure *with* per-tick position tracking and policy-consistent caps.
- Only the **managed wiring** (`__main__.py`) and the now-orphaned env var change.

**Tech Stack:** Python 3.13, `uv`, `pytest` (`asyncio_mode=auto`), `ruff` (line-length 100), structlog.

---

## Context (verified contracts)
- `decision-engine/__main__.py`: `_build_risk_gate(*, drawdown, exposure, gas_spike, position_loss, tx_failure)` builds `RiskGate([Drawdown, TxFailure, GasSpike, Exposure, PositionLoss])`. `_amain` constructs all five modules incl. the `DECISION_ENGINE_TOTAL_CAPITAL_USD` fail-loud guard + `ExposureLimiter(total_capital=Decimal(...))` + `PositionLossLimit()`. `Decimal` is imported *only* for that guard (line 23 → line 217).
- `test_managed_engine_unit.py` imports `ManagedEngine` + `_make_pending_recorder` only; it never references `_build_risk_gate`, the limiters, or the env var. Signatures of `ManagedEngine` / `_make_pending_recorder` are unchanged → it stays green.
- `risk_gate.py`: `ExposureChecker`/`PositionLossChecker` classes + `RiskContext.order_value_usd` remain. Their tests live in `test_risk_gate_unit.py`, `risk/test_exposure_limits.py`, `risk/test_position_loss_limit.py` — all unchanged.
- `config.py`: line 6 docstring references "matching the DECISION_ENGINE_TOTAL_CAPITAL_USD guard" — stale once the guard is gone.
- `managed_cycle.py`: currently sets `RiskContext(..., order_value_usd=plan.usd_amount)`. With no `ExposureChecker` in the managed gate this value is unconsumed → revert that one kwarg (the field + checker logic + regression test stay for P2).
- `.env.example`: a `DECISION_ENGINE_TOTAL_CAPITAL_USD` block (Runtime tuning section) labelled "required for the decision-engine to boot" — no longer required/used.

## File Structure
- **Modify** `decision-engine/src/decision_engine/__main__.py` — gate to 3 checkers; drop the two limiters, the env guard, and the now-unused `Decimal` import.
- **Modify** `decision-engine/src/decision_engine/managed_cycle.py` — drop the unused `order_value_usd=` kwarg from the `RiskContext` build.
- **Modify** `decision-engine/src/decision_engine/config.py` — fix the stale docstring line.
- **Modify** `.env.example` — remove the `DECISION_ENGINE_TOTAL_CAPITAL_USD` block.
- **No new tests** — existing suite covers the retained classes; `test_managed_engine_unit.py` covers the managed worker. (The 2026-06-05 wei→USD regression test in `test_risk_gate_unit.py` stays — it pins the retained class's correctness.)

---

## Task 1: Unwire the two lake-era checkers from the managed gate

**File:** `__main__.py`

- [ ] **Imports:** remove `ExposureLimiter`, `PositionLossLimit` (from `risk.*`), `ExposureChecker`, `PositionLossChecker` (from `risk_gate`), and `from decimal import Decimal`.
- [ ] **`_build_risk_gate`:** new signature `(*, drawdown, gas_spike, tx_failure)` → `RiskGate([DrawdownChecker(drawdown), TxFailureChecker(tx_failure), GasSpikeChecker(gas_spike)])`.
- [ ] **`_amain`:** delete the `total_capital_raw` fetch + `RuntimeError` guard, the `ExposureLimiter(...)` and `PositionLossLimit()` construction; update the `_build_risk_gate(...)` call; retune the "Risk modules" comment to reflect the right-sized gate (drawdown + gas-spike + tx-failure; depeg next phase).

## Task 2: Drop the now-unused notional threading

**File:** `managed_cycle.py`

- [ ] Remove `order_value_usd=plan.usd_amount` (and its comment) from the `RiskContext(...)` construction in `run_one`. (`RiskContext.order_value_usd` field, `ExposureChecker`'s use of it, and the regression test remain for P2.)

## Task 3: Docs + env cleanup

- [ ] **`config.py`:** reword the line-6 docstring — fail-loud validation no longer "matches the DECISION_ENGINE_TOTAL_CAPITAL_USD guard" (that guard is gone); describe it generically.
- [ ] **`.env.example`:** remove the `DECISION_ENGINE_TOTAL_CAPITAL_USD` block from Runtime tuning.

## Task 4: Verify

- [ ] `uv run ruff check decision-engine/` → clean (catch unused imports).
- [ ] `uv run pytest decision-engine/ -q` → all pass (expect 352).
- [ ] Manual read: managed gate now lists exactly `[DrawdownChecker, TxFailureChecker, GasSpikeChecker]`; no `DECISION_ENGINE_TOTAL_CAPITAL_USD` reference remains in the live path.

---

## Deferred (explicitly NOT this change)
- **PnL deposit tracker** (running contributed-capital from external input txs, net of withdrawals, per-deposit priced) — reporting feature, separate phase.
- **USDC peg oracle + depeg breaker** — ship together next phase; until then USDC stays `$1` (oracle-pricing without the breaker is the unsafe combo).
- **Exposure re-introduction** — P2, with per-tick position registration and caps set consistent with the 60/40 + band policy (asset cap ≥ upper band).
