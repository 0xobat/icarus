# Managed Portfolio — USDC Peg Oracle + Depeg Breaker

> **Capital-protection feature.** Adds a real USDC price + a circuit breaker that halts rebalancing during a depeg. Backward compatible: with no USDC feed configured, USDC stays `$1` and the breaker never trips (today's behavior).

**Goal:** Stop assuming USDC = $1. Price USDC from a Chainlink USDC/USD feed at tick time (symmetric with ETH), and add a `DepegBreaker` + `DepegChecker` that **halts rebalancing when USDC is off-peg beyond a threshold** (default 100 bps). This closes the "peg-aware pricing without a guard is unsafe" hazard from the 2026-06-05 design discussion — the breaker is the point, not just the price.

**Why a breaker, not just a price:** if USDC drops to $0.97 and you only update the price, the stable sleeve shrinks → crypto weight rises → the planner's instinct is to **sell good crypto and buy more of the depegging stable**. The breaker prevents that by halting rebalancing while off-peg.

**Verified contracts (on-chain, 2026-06-06):**
- Chainlink USDC/USD proxy, **Base mainnet**: `0x458138Fc0D67027E9A6778ef40a6ffC318c69061` (8 decimals, "USDC / USD").
- Chainlink USDC/USD proxy, **Base Sepolia**: `0xd30e2101a97dcbAeBCBC04F14C3f624E67A35165` (verified live: "USDC / USD", 8 decimals, ~$0.9996, fresh `updatedAt`).
- ETH/USD feed pattern to mirror: `RpcAdapter(chainlink_eth_usd_address=...)` + `CHAINLINK_ETH_USD_ADDRESS` env (added 2026-06-05). `_AGGREGATOR_V3_ABI`, `_eth_price_usd()` (reads `latestRoundData()` + `decimals()`), `fetch_live` → `MarketSnapshot(prices={"ETH": ...})`.
- `pricing.price_usd("USDC", market)` currently returns `Decimal("1")` hardcoded; `price_usd("WETH"/"ETH")` reads `market.prices["ETH"]`.
- Risk-module style to mirror: `GasSpikeBreaker` (stateful `.update()`, `.is_active`/state props). `risk_gate.py` checker pattern: `GasSpikeChecker(breaker)` → `RiskDecision`. Managed gate built in `__main__._build_risk_gate` = `[Drawdown, TxFailure, GasSpike]`; fed per-tick in `ManagedEngine._tick`.
- `ManagedConfig` (frozen) + `load_managed_config(toml, *, env)`; `managed.toml` has `[allocation]/[rebalance]/[cadence]/[limits]`.

**Tech Stack:** Python 3.13, `uv`, `pytest` (`asyncio_mode=auto`), `ruff` (line-length 100), `web3` (AsyncWeb3), structlog, Decimal arithmetic.

---

## Design

### Peg oracle (price USDC from a feed, $1 fallback)
- `RpcAdapter.__init__` gains `chainlink_usdc_usd_address: str | None = None`. When set, `fetch_live` (and the historical path) reads it like the ETH feed and adds `prices["USDC"]` to the snapshot. When `None`, **omit USDC from prices** (no extra RPC call).
- A cached `_usdc_usd_contract` handle mirroring `_eth_usd_contract`; a `_usdc_price_usd()` mirroring `_eth_price_usd()` (negative/zero → raise, same guard).
- `pricing.price_usd("USDC", market)`: return `market.prices["USDC"]` **if present**, else `Decimal("1")` (unchanged fallback). Keep the stablecoin set logic but prefer a live price when the snapshot carries one.

### Depeg breaker
- New `decision_engine/risk/depeg_breaker.py`, `DepegBreaker`:
  - `__init__(self, *, threshold_bps: int = 100, peg: Decimal = Decimal("1"))`.
  - `update(self, usdc_price: Decimal) -> None` — store last price.
  - `is_tripped: bool` — `True` when `abs(price - peg) / peg * 10_000 > threshold_bps`. Untripped before the first `update` (no price → assume pegged, fail-safe-open is acceptable here because no feed = today's behavior; document it).
  - `current_price: Decimal | None`, `deviation_bps: Decimal` for logging.
- `risk_gate.py`: `DepegChecker(breaker)`:
  - `check(order, ctx)` → reject any swap when `breaker.is_tripped` (every managed rebalance touches USDC, so halt-all is correct). Reason: `f"USDC depeg (price={price}, dev={dev}bps > {threshold}bps)"`. Export it; add to `__all__`.

### Wiring (`__main__`)
- Read `CHAINLINK_USDC_USD_ADDRESS` from env (checksum-normalized like the ETH one); pass to `RpcAdapter`.
- Construct `DepegBreaker(threshold_bps=config.depeg_threshold_bps)`; add `DepegChecker(depeg)` to `_build_risk_gate` (signature gains `depeg=`). Order: put DepegChecker among the cheap state-reads (e.g. after GasSpike).
- In `ManagedEngine._tick`, after the market fetch: `usdc_price = market.prices.get("USDC")`; `if usdc_price is not None: self._depeg.update(usdc_price)` — fed before `cycle.run_one()` so the gate sees fresh state (same pattern as gas/drawdown). `ManagedEngine.__init__` gains `depeg` (optional, default None → skip update so existing tests pass).

### Config
- `ManagedConfig` gains `depeg_threshold_bps: int = 100`. `load_managed_config` reads `[risk] depeg_threshold_bps` from toml (default 100 if the section/key is absent → backward compatible). Validate `0 < depeg_threshold_bps <= 2000`.
- `config/managed.toml`: add `[risk]\ndepeg_threshold_bps = 100`.

### Env docs
- `.env.example` (managed section): add commented `CHAINLINK_USDC_USD_ADDRESS` with both verified addresses (per [[feedback-update-env-example-on-var-change]]).

---

## File Structure
- **Modify** `lib/src/icarus/data_adapters/rpc.py` — optional USDC feed read.
- **Modify** `decision-engine/src/decision_engine/pricing.py` — USDC from snapshot, $1 fallback.
- **Create** `decision-engine/src/decision_engine/risk/depeg_breaker.py` — `DepegBreaker`.
- **Modify** `decision-engine/src/decision_engine/risk_gate.py` — `DepegChecker`.
- **Modify** `decision-engine/src/decision_engine/config.py` — `depeg_threshold_bps`.
- **Modify** `config/managed.toml` — `[risk]` section.
- **Modify** `decision-engine/src/decision_engine/__main__.py` — feed address + breaker wiring + `_tick` feed.
- **Modify** `.env.example` — `CHAINLINK_USDC_USD_ADDRESS`.
- **Create/extend tests** — see tasks. TDD: write failing test first per task.

---

## Task 1: Depeg breaker (pure module, test-first)
**Files:** create `risk/depeg_breaker.py`; create `tests/risk/test_depeg_breaker.py`.
- [ ] Failing tests: untripped before first update; pegged ($1.000) untripped; $0.985 (150bps) tripped at default 100bps; boundary ($0.99 exactly = 100bps) NOT tripped (strict `>`); $1.02 tripped; `deviation_bps` correct.
- [ ] Implement `DepegBreaker` to pass.

## Task 2: Depeg checker (gate adapter, test-first)
**Files:** modify `risk_gate.py`; extend `tests/test_risk_gate_unit.py`.
- [ ] Failing tests: tripped breaker → reject (reason mentions depeg + bps); untripped → pass.
- [ ] Implement `DepegChecker`; export in `__all__`.

## Task 3: Peg oracle (adapter + pricing, test-first)
**Files:** modify `rpc.py`, `pricing.py`; extend `tests/test_pricing_unit.py` + the rpc adapter tests.
- [ ] Failing tests: `price_usd("USDC", market)` returns `market.prices["USDC"]` when present (e.g. `0.9996`), else `Decimal("1")`. Adapter with a mocked USDC feed populates `prices["USDC"]`; adapter with no USDC address omits it (no call).
- [ ] Implement; keep ETH path untouched.

## Task 4: Config + wiring
**Files:** modify `config.py`, `managed.toml`, `__main__.py`, `.env.example`; extend `tests/test_managed_config_unit.py` + `tests/test_managed_engine_unit.py`.
- [ ] `depeg_threshold_bps` load + validation tests; default 100 when absent.
- [ ] `_tick` feeds the depeg breaker from `market.prices["USDC"]` (extend the engine smoke test with a `depeg` breaker + a USDC price in the fake adapter; assert `depeg.update` saw it).
- [ ] Managed gate now includes `DepegChecker`; `_build_risk_gate` signature + call updated.
- [ ] `.env.example` documents `CHAINLINK_USDC_USD_ADDRESS` (both addresses).

## Task 5: Verify
- [ ] `uv run ruff check decision-engine/ lib/` → clean.
- [ ] `uv run pytest decision-engine/ -q` → all pass (new tests added).
- [ ] Read-through: with `CHAINLINK_USDC_USD_ADDRESS` unset, behavior is identical to today (USDC=$1, breaker never trips). With it set, USDC is live and a >100bps deviation halts rebalancing.

## Out of scope (later)
- Smarter depeg policy (halt only USDC-buys vs halt-all) — start halt-all.
- Depeg severity tiers / auto-unwind — P3.
- Secondary peg sources (reserves attestation, DEX depth via Dune) — monitoring layer, not the gate.
