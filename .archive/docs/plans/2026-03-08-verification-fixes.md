# Verification Audit Fixes — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix all 17 issues found during the 4-agent codebase verification audit (30 verified, 6 mostly verified, 11 partial out of 48 features).

**Architecture:** Each task is scoped to a single module. No cross-cutting changes. All fixes are additive — existing tests must continue to pass. Tasks are ordered by severity (critical → medium → low) and by dependency (foundational fixes first).

**Tech Stack:** Python 3.12 (py-engine), TypeScript/Node 22 (ts-executor), pytest, vitest

---

## Task 1: Wire real circuit breaker states into InsightSynthesizer

**Severity:** CRITICAL — Claude currently sees hardcoded `circuit_breakers_active: False`
**Feature:** AI-002

**Files:**

- Modify: `py-engine/ai/insight_synthesis.py:387-398`
- Test: `py-engine/tests/test_insight_synthesis.py`

**Context:** `_collect_risk_status()` is a stub that hardcodes all risk fields to `False`. The `InsightSynthesizer.__init__()` already receives dependencies — we need to add circuit breaker references and query their actual state.

**Step 1: Write the failing test**

```python
def test_collect_risk_status_queries_breakers(synth_with_breakers):
    """Risk status should reflect actual circuit breaker states."""
    synth = synth_with_breakers
    synth.drawdown.is_triggered.return_value = True
    synth.gas_spike._is_active = True
    synth.tx_failures.can_execute.return_value = False

    result = synth._collect_risk_status()

    assert result["circuit_breakers_active"] is True
    assert result["drawdown_triggered"] is True
    assert result["gas_spike_active"] is True
    assert result["tx_failures_paused"] is True
    assert "timestamp" in result
```

**Step 2: Run test to verify it fails**

Run: `cd py-engine && uv run pytest tests/test_insight_synthesis.py::test_collect_risk_status_queries_breakers -v`
Expected: FAIL — `InsightSynthesizer` doesn't accept breaker args yet

**Step 3: Implement**

Update `InsightSynthesizer.__init__()` to accept optional circuit breaker references:

```python
def __init__(
    self,
    price_feed,
    gas_monitor,
    defi_metrics,
    tracker,
    lifecycle,
    *,
    drawdown=None,
    gas_spike=None,
    tx_failures=None,
    position_loss=None,
    tvl_monitor=None,
    hold_mode=None,
):
```

Replace the stub `_collect_risk_status()`:

```python
def _collect_risk_status(self) -> dict[str, Any]:
    dd_triggered = self._drawdown.is_triggered() if self._drawdown else False
    gas_active = self._gas_spike._is_active if self._gas_spike else False
    tx_paused = not self._tx_failures.can_execute() if self._tx_failures else False
    pos_loss = self._position_loss.is_any_in_cooldown() if self._position_loss else False
    hold_active = self._hold_mode.is_active() if self._hold_mode else False

    any_active = dd_triggered or gas_active or tx_paused or pos_loss

    return {
        "circuit_breakers_active": any_active,
        "drawdown_triggered": dd_triggered,
        "gas_spike_active": gas_active,
        "tx_failures_paused": tx_paused,
        "position_loss_cooldown": pos_loss,
        "hold_mode_active": hold_active,
        "trading_paused": any_active or hold_active,
        "entries_paused": any_active or hold_active,
        "timestamp": datetime.now(UTC).isoformat(),
    }
```

**Step 4: Update `main.py` to pass breakers to synthesizer**

In `DecisionLoop.__init__()`, update the `InsightSynthesizer` constructor call to pass the circuit breaker instances:

```python
self.synthesizer = InsightSynthesizer(
    self.price_feed, self.gas_monitor, self.defi_metrics,
    self.tracker, self.lifecycle,
    drawdown=self.drawdown,
    gas_spike=self.gas_spike,
    tx_failures=self.tx_failures,
    position_loss=self.position_loss,
    tvl_monitor=self.tvl_monitor,
    hold_mode=self.hold_mode,
)
```

**Step 5: Run full test suite**

Run: `cd py-engine && uv run pytest tests/ --tb=short -q`
Expected: All pass (existing tests use default `None` args)

**Step 6: Commit**

```bash
git add py-engine/ai/insight_synthesis.py py-engine/main.py py-engine/tests/test_insight_synthesis.py
git commit -m "fix(icarus): wire real circuit breaker states into InsightSynthesizer (AI-002)"
```

---

## Task 2: Include strategy report observations/signals in InsightSnapshot

**Severity:** CRITICAL — Claude doesn't see strategy observations or signal details
**Feature:** AI-002

**Files:**

- Modify: `py-engine/ai/insight_synthesis.py:368-385` (`_collect_strategies`)
- Modify: `py-engine/main.py` (pass latest reports to synthesizer)
- Test: `py-engine/tests/test_insight_synthesis.py`

**Context:** `_collect_strategies()` currently collects strategy IDs and performance metrics, but not the actual `StrategyReport` content (observations, signals, recommendations). The `DecisionLoop` already accumulates reports in `self._latest_reports` — we need to feed those into the synthesizer.

**Step 1: Write the failing test**

```python
def test_strategies_include_report_content(synth_with_reports):
    """Strategy section should include observations and signals from latest reports."""
    synth = synth_with_reports
    result = synth._collect_strategies()

    assert any(s.get("latest_report") for s in result)
    report = next(s["latest_report"] for s in result if s.get("latest_report"))
    assert "observations" in report
    assert "signals" in report
```

**Step 2: Run test to verify it fails**

Run: `cd py-engine && uv run pytest tests/test_insight_synthesis.py::test_strategies_include_report_content -v`
Expected: FAIL — no `latest_report` key in strategy dicts

**Step 3: Implement**

Add a method to accept latest reports:

```python
def update_strategy_reports(self, reports: dict[str, StrategyReport]) -> None:
    """Update cached strategy reports for inclusion in snapshots."""
    self._latest_reports = reports
```

Update `_collect_strategies()` to merge report data:

```python
def _collect_strategies(self) -> list[dict[str, Any]]:
    # ... existing strategy collection ...
    for strategy in strategies:
        sid = strategy.get("strategy_id", "")
        if sid in self._latest_reports:
            report = self._latest_reports[sid]
            strategy["latest_report"] = {
                "observations": [asdict(o) for o in report.observations],
                "signals": [asdict(s) for s in report.signals],
                "recommendation": asdict(report.recommendation) if report.recommendation else None,
                "timestamp": report.timestamp,
            }
    return strategies
```

**Step 4: Wire in main.py**

After strategy evaluation in `run_cycle()`, call:

```python
self.synthesizer.update_strategy_reports(self._latest_reports)
```

Place this before `self.synthesizer.synthesize()` at ~line 368.

**Step 5: Run full test suite**

Run: `cd py-engine && uv run pytest tests/ --tb=short -q`
Expected: All pass

**Step 6: Commit**

```bash
git add py-engine/ai/insight_synthesis.py py-engine/main.py py-engine/tests/test_insight_synthesis.py
git commit -m "fix(icarus): include strategy report observations/signals in InsightSnapshot (AI-002)"
```

---

## Task 3: Add STRATEGY.md objectives to Claude's prompt context

**Severity:** HIGH — Claude has no visibility into strategy definitions
**Features:** AI-001, AI-002

**Files:**

- Modify: `py-engine/ai/decision_engine.py:191-221` (system prompt)
- Modify: `py-engine/ai/insight_synthesis.py` (add objectives section)
- Test: `py-engine/tests/test_decision_engine.py`, `py-engine/tests/test_insight_synthesis.py`

**Context:** The system prompt describes Claude's role but doesn't include the strategy definitions from `STRATEGY.md`. The snapshot should include an `objectives` section summarizing active strategy guidelines.

**Step 1: Write the failing test**

```python
def test_snapshot_includes_objectives(synth):
    """Snapshot should include strategy objectives."""
    snapshot = synth.synthesize()
    snapshot_dict = snapshot.to_dict()
    assert "objectives" in snapshot_dict or "objectives" in snapshot_dict.get("market_data", {})
```

**Step 2: Run test to verify it fails**

Run: `cd py-engine && uv run pytest tests/test_insight_synthesis.py::test_snapshot_includes_objectives -v`
Expected: FAIL

**Step 3: Implement**

Add an `objectives` field to `InsightSnapshot`:

```python
@dataclass
class InsightSnapshot:
    market_data: dict[str, Any]
    positions: dict[str, Any]
    risk_status: dict[str, Any]
    strategies: list[dict[str, Any]]
    recent_decisions: list[dict[str, Any]]
    objectives: dict[str, Any] | None = None  # NEW
    timestamp: str = ""
    snapshot_version: str = "1.0.0"
```

Add `_collect_objectives()` to `InsightSynthesizer`:

```python
def _collect_objectives(self) -> dict[str, Any]:
    """Load strategy objectives from STRATEGY.md for prompt context."""
    strategy_path = Path(__file__).parent.parent / "STRATEGY.md"
    if not strategy_path.exists():
        strategy_path = Path(__file__).parent.parent.parent / "STRATEGY.md"
    if not strategy_path.exists():
        return {"source": "STRATEGY.md", "status": "not_found"}
    content = strategy_path.read_text()
    # Trim to keep token-efficient — first 2000 chars covers both strategies
    return {
        "source": "STRATEGY.md",
        "content": content[:2000],
    }
```

Wire into `synthesize()` between step 4 and step 5.

**Step 4: Run full test suite**

Run: `cd py-engine && uv run pytest tests/ --tb=short -q`
Expected: All pass

**Step 5: Commit**

```bash
git add py-engine/ai/insight_synthesis.py py-engine/tests/test_insight_synthesis.py
git commit -m "fix(icarus): load STRATEGY.md objectives into InsightSnapshot (AI-001, AI-002)"
```

---

## Task 4: Enforce stable-pair filtering in Aerodrome LP strategy

**Severity:** MEDIUM — safety constraint defined but unenforced
**Feature:** STRAT-004

**Files:**

- Modify: `py-engine/strategies/aerodrome_lp.py:80`
- Test: `py-engine/tests/test_aerodrome_lp.py`

**Context:** `STABLE_PAIRS` frozenset is defined at line 25-30 but never used. Pool filtering at line 80 only checks `p.protocol == "aerodrome"`, allowing volatile pairs through.

**Step 1: Write the failing test**

```python
def test_rejects_volatile_pair():
    """Volatile/stable pairs should be filtered out."""
    snapshot = make_snapshot(pools=[
        make_pool(pool_id="vAMM-ETH/USDC", protocol="aerodrome", apy=0.10, tvl=1_000_000),
    ])
    strategy = AerodromeLpStrategy()
    report = strategy.evaluate(snapshot)
    # Should have no entry signal for a volatile pair
    assert not any(s.actionable for s in report.signals)
```

**Step 2: Run test to verify it fails**

Run: `cd py-engine && uv run pytest tests/test_aerodrome_lp.py::test_rejects_volatile_pair -v`
Expected: FAIL — volatile pair passes through and gets entry signal

**Step 3: Implement**

Add a helper method and update pool filtering at line 80:

```python
def _is_stable_pair(self, pool_id: str) -> bool:
    """Check if pool_id represents a known stable-stable pair."""
    # Pool IDs follow pattern "sAMM-TOKEN_A/TOKEN_B" or similar
    for a, b in STABLE_PAIRS:
        if a in pool_id and b in pool_id:
            return True
    return False
```

Update line 80:

```python
aero_pools = [
    p for p in snapshot.pools
    if p.protocol == "aerodrome" and self._is_stable_pair(p.pool_id)
]
```

**Step 4: Run full test suite**

Run: `cd py-engine && uv run pytest tests/test_aerodrome_lp.py -v`
Expected: All pass (existing tests use stable pair pool_ids)

**Step 5: Commit**

```bash
git add py-engine/strategies/aerodrome_lp.py py-engine/tests/test_aerodrome_lp.py
git commit -m "fix(icarus): enforce stable-pair filtering in Aerodrome LP strategy (STRAT-004)"
```

---

## Task 5: Wire TVL exit condition in Aave lending strategy

**Severity:** MEDIUM — dead code constant
**Feature:** STRAT-003

**Files:**

- Modify: `py-engine/strategies/aave_lending.py` (add TVL drop signal to `evaluate()`)
- Test: `py-engine/tests/test_aave_lending.py`

**Context:** `TVL_DROP_THRESHOLD = 0.30` is defined at line 38 but never referenced in `evaluate()`. The strategy should emit an exit signal when pool TVL drops significantly.

**Step 1: Write the failing test**

```python
def test_tvl_drop_emits_exit_signal():
    """A significant TVL drop should produce an exit signal."""
    # Pool with APY above floor but very low TVL (below $1M minimum)
    snapshot = make_snapshot(pools=[
        make_pool(pool_id="USDC", protocol="aave_v3", apy=0.05, tvl=400_000),
    ])
    strategy = AaveLendingStrategy()
    report = strategy.evaluate(snapshot)
    # Pool should be filtered out by _filter_pools (tvl < MIN_LIQUIDITY_USD)
    # and strategy should observe the TVL concern
    assert any("tvl" in o.metric.lower() or "liquidity" in o.context.lower()
               for o in report.observations)
```

**Step 2: Run test to verify it fails**

Run: `cd py-engine && uv run pytest tests/test_aave_lending.py::test_tvl_drop_emits_exit_signal -v`
Expected: Verify behavior — `_filter_pools` already rejects TVL < $1M, so the existing filtering handles this. The dead constant `TVL_DROP_THRESHOLD` can be removed since TVL exit is handled by the RISK-005 circuit breaker (which directly emits withdrawal orders). Add a comment documenting this design choice.

**Step 3: Document the design decision**

Replace the dead constant with a comment:

```python
# TVL-based exit is handled by the RISK-005 circuit breaker (tvl_monitor.py),
# which directly emits CB:tvl_drop withdrawal orders. The strategy does not
# duplicate this check — it only monitors APY and liquidity thresholds.
```

Remove `TVL_DROP_THRESHOLD = 0.30` (line 38).

**Step 4: Run full test suite**

Run: `cd py-engine && uv run pytest tests/test_aave_lending.py -v`
Expected: All pass (constant was never referenced)

**Step 5: Commit**

```bash
git add py-engine/strategies/aave_lending.py py-engine/tests/test_aave_lending.py
git commit -m "fix(icarus): remove dead TVL_DROP_THRESHOLD from Aave strategy, document CB delegation (STRAT-003)"
```

---

## Task 6: Make Aave entry check use APY differential vs current position

**Severity:** MEDIUM — entry logic doesn't match STRATEGY.md spec
**Feature:** STRAT-003

**Files:**

- Modify: `py-engine/strategies/aave_lending.py:193-212` (`_check_entry`)
- Test: `py-engine/tests/test_aave_lending.py`

**Context:** `_check_entry()` checks `pool.apy < MIN_APY_IMPROVEMENT` as an absolute floor, but STRATEGY.md says "Target market APY exceeds **current position** APY by at least 0.5% after gas." The check should compare against the current position's APY, not just an absolute threshold.

**Step 1: Write the failing test**

```python
def test_entry_requires_differential_vs_current():
    """Entry should require 0.5% improvement over current position APY, not absolute."""
    # Current position at 3.0% APY, new pool at 3.3% — should NOT trigger
    snapshot = make_snapshot(
        pools=[make_pool(pool_id="USDC", protocol="aave_v3", apy=0.033, tvl=2_000_000)],
        current_position_apy=0.030,
    )
    strategy = AaveLendingStrategy()
    report = strategy.evaluate(snapshot)
    assert not any(s.type == SignalType.ENTRY_MET and s.actionable for s in report.signals)
```

**Step 2: Run test to verify it fails**

Run: `cd py-engine && uv run pytest tests/test_aave_lending.py::test_entry_requires_differential_vs_current -v`
Expected: FAIL — current code would trigger entry since 3.3% > 0.5% absolute

**Step 3: Implement**

Update `_check_entry()` to accept an optional `current_apy` parameter:

```python
def _check_entry(self, pool: PoolState, gas: GasInfo, current_apy: float = 0.0) -> bool:
    improvement = pool.apy - current_apy
    if improvement < MIN_APY_IMPROVEMENT:
        return False
    if gas.current_gwei > gas.avg_24h_gwei * 3:
        return False
    return True
```

Update the call site in `evaluate()` to pass current position APY from the snapshot (if available via recommendation parameters or position context).

**Step 4: Run full test suite**

Run: `cd py-engine && uv run pytest tests/test_aave_lending.py -v`
Expected: All pass

**Step 5: Commit**

```bash
git add py-engine/strategies/aave_lending.py py-engine/tests/test_aave_lending.py
git commit -m "fix(icarus): use APY differential vs current position for Aave entry check (STRAT-003)"
```

---

## Task 7: Implement data_window pre-slicing in StrategyManager

**Severity:** MEDIUM — strategies receive full snapshot instead of windowed data
**Feature:** STRAT-001

**Files:**

- Modify: `py-engine/strategies/manager.py:175-181`
- Test: `py-engine/tests/test_strategy_manager.py`

**Context:** `evaluate_all()` passes the full `MarketSnapshot` to every strategy. The spec requires pre-slicing data by each strategy's `data_window`. Since `MarketSnapshot` contains lists of `TokenPrice` and `PoolState` with timestamps, slicing means filtering entries to those within `data_window` of `snapshot.timestamp`.

**Step 1: Write the failing test**

```python
def test_evaluate_all_slices_by_data_window():
    """Each strategy should receive data sliced to its data_window."""
    # Strategy with 1h data_window should not see 2h-old prices
    manager = make_manager_with_strategy(data_window=timedelta(hours=1))
    old_price = make_price(timestamp=datetime.now(UTC) - timedelta(hours=2))
    recent_price = make_price(timestamp=datetime.now(UTC) - timedelta(minutes=30))
    snapshot = make_snapshot(prices=[old_price, recent_price])

    await manager.evaluate_all(snapshot)

    # Strategy.evaluate was called with snapshot containing only the recent price
    call_snapshot = manager._strategies["test"].evaluate.call_args[0][0]
    assert len(call_snapshot.prices) == 1
```

**Step 2: Run test to verify it fails**

Run: `cd py-engine && uv run pytest tests/test_strategy_manager.py::test_evaluate_all_slices_by_data_window -v`
Expected: FAIL — snapshot passed as-is

**Step 3: Implement**

Add a snapshot slicing function and use it in `_run()`:

```python
def _slice_snapshot(snapshot: MarketSnapshot, window: timedelta) -> MarketSnapshot:
    cutoff = snapshot.timestamp - window
    return MarketSnapshot(
        prices=[p for p in snapshot.prices if p.timestamp >= cutoff],
        gas=snapshot.gas,
        pools=[p for p in snapshot.pools if getattr(p, 'timestamp', snapshot.timestamp) >= cutoff],
        timestamp=snapshot.timestamp,
    )
```

Update `_run()` in `evaluate_all()`:

```python
async def _run(sid: str) -> StrategyReport | None:
    try:
        instance = self._get_instance(sid)
        sliced = _slice_snapshot(snapshot, instance.data_window)
        report = await asyncio.to_thread(instance.evaluate, sliced)
        ...
```

**Step 4: Run full test suite**

Run: `cd py-engine && uv run pytest tests/ --tb=short -q`
Expected: All pass

**Step 5: Commit**

```bash
git add py-engine/strategies/manager.py py-engine/tests/test_strategy_manager.py
git commit -m "fix(icarus): implement data_window pre-slicing in StrategyManager (STRAT-001)"
```

---

## Task 8: Compile AllowlistGuard bytecode and wire into deploy script

**Severity:** MEDIUM — deploy script non-functional
**Feature:** RISK-009

**Files:**

- Modify: `ts-executor/scripts/deploy-guard.ts:62`
- Create: `contracts/AllowlistGuard.sol` compilation step in `package.json` or Makefile
- Test: `ts-executor/tests/allowlist-guard.test.ts`

**Context:** `GUARD_BYTECODE` is a placeholder string `'0x__REPLACE_WITH_COMPILED_BYTECODE__'`. The Solidity contract exists at `contracts/AllowlistGuard.sol` but no compilation pipeline exists.

**Step 1: Add solc compilation script**

Add to `ts-executor/package.json` scripts:

```json
"compile:guard": "solc --bin --abi --optimize contracts/AllowlistGuard.sol -o contracts/build/"
```

Or use a simpler approach — inline the compiled bytecode. Run solc locally:

```bash
cd ts-executor && npx solc --bin contracts/AllowlistGuard.sol -o contracts/build/
```

**Step 2: Replace the placeholder**

Read the compiled `.bin` file and replace line 62 in `deploy-guard.ts`:

```typescript
const GUARD_BYTECODE = "0x<actual-compiled-bytecode>" as `0x${string}`;
```

**Step 3: Add runtime cross-verification function**

Add a `verifyGuardMatchesAllowlist()` function that reads on-chain guard state and compares to `CONTRACT_ALLOWLIST` env var:

```typescript
async function verifyGuardMatchesAllowlist(
  publicClient: PublicClient,
  guardAddress: Address,
  expectedAllowlist: Address[],
): Promise<boolean> {
  for (const addr of expectedAllowlist) {
    const allowed = await publicClient.readContract({
      address: guardAddress,
      abi: GUARD_ABI,
      functionName: "allowlisted",
      args: [addr],
    });
    if (!allowed) return false;
  }
  return true;
}
```

**Step 4: Run tests**

Run: `cd ts-executor && pnpm test`
Expected: All pass

**Step 5: Commit**

```bash
git add ts-executor/scripts/deploy-guard.ts ts-executor/package.json
git commit -m "fix(icarus): compile AllowlistGuard bytecode and add cross-verification (RISK-009)"
```

---

## Task 9: Fix threshold boundary conditions (>=20% → >20%, >=30% → >30%)

**Severity:** LOW — more conservative than spec but inconsistent
**Features:** RISK-001, RISK-005

**Files:**

- Modify: `py-engine/risk/drawdown_breaker.py:121`
- Modify: `py-engine/risk/tvl_monitor.py:234-235`
- Test: `py-engine/tests/test_drawdown_breaker.py`, `py-engine/tests/test_tvl_monitor.py`

**Step 1: Write boundary tests**

```python
# drawdown_breaker
def test_exactly_20_percent_does_not_trigger():
    """Exactly 20% drawdown should NOT trigger (spec says >20%)."""
    breaker = DrawdownBreaker()
    breaker.update(Decimal("1000"))  # peak
    breaker.update(Decimal("800"))   # exactly 20% drop
    assert not breaker.should_unwind_all()

# tvl_monitor
def test_exactly_30_percent_drop_does_not_trigger():
    """Exactly 30% TVL drop should NOT trigger (spec says >30%)."""
    ...
```

**Step 2: Run tests to verify they fail**

Expected: FAIL — current code uses `>=`

**Step 3: Fix**

`drawdown_breaker.py:121`: change `>=` to `>`
`tvl_monitor.py:234`: change `>=` to `>`

**Step 4: Run full test suite, update any tests that assert on boundary**

Run: `cd py-engine && uv run pytest tests/test_drawdown_breaker.py tests/test_tvl_monitor.py -v`

**Step 5: Commit**

```bash
git add py-engine/risk/drawdown_breaker.py py-engine/risk/tvl_monitor.py py-engine/tests/
git commit -m "fix(icarus): correct threshold boundaries to >20% and >30% per spec (RISK-001, RISK-005)"
```

---

## Task 10: Add .env.example

**Severity:** LOW — developer onboarding gap
**Feature:** INFRA-001

**Files:**

- Create: `.env.example`

**Step 1: Create the file**

Scan all `os.environ.get()` and `process.env.` calls across both services. Create `.env.example` documenting every required and optional env var with placeholder values and comments.

Key vars to include:

```env
# Required — Wallet
WALLET_PRIVATE_KEY=0x_your_private_key_here
SAFE_ADDRESS=0x_your_safe_address
RECOVERY_ADDRESS=0x_human_recovery_signer

# Required — Alchemy
ALCHEMY_BASE_WS_URL=wss://base-mainnet.g.alchemy.com/v2/YOUR_KEY
ALCHEMY_BASE_HTTP_URL=https://base-mainnet.g.alchemy.com/v2/YOUR_KEY

# Required — AI
ANTHROPIC_API_KEY=sk-ant-your_key_here

# Required — Database
DATABASE_URL=postgresql+psycopg2://icarus:icarus@localhost:5432/icarus
REDIS_URL=redis://localhost:6379

# Optional — Risk Limits
MAX_SINGLE_PROTOCOL_PERCENT=40
MAX_SINGLE_ASSET_PERCENT=60
MIN_STABLECOIN_RESERVE_PERCENT=15
CONTRACT_ALLOWLIST=0xaddr1,0xaddr2

# Optional — Tuning
STREAM_MAX_LENGTH=10000
STREAM_TRIM_INTERVAL_CYCLES=100
PRICE_CACHE_TTL=30
```

**Step 2: Commit**

```bash
git add .env.example
git commit -m "fix(icarus): add .env.example for developer onboarding (INFRA-001)"
```

---

## Task 11: Clarify HARNESS-003 blocking semantics in feature spec

**Severity:** LOW — spec/impl mismatch, implementation is intentionally non-blocking
**Feature:** HARNESS-003

**Files:**

- Modify: `harness/features.json` (update step 3 wording)

**Step 1: Update feature step**

Change step 3 from:

```json
"Blocks execution until approval received or timeout"
```

To:

```json
"Non-blocking approval lifecycle: request returns PENDING, callers poll check_approval(), auto-times-out"
```

This aligns the spec with the intentional non-blocking implementation documented in the ApprovalGateManager docstring.

**Step 2: Commit**

```bash
git add harness/features.json
git commit -m "docs(icarus): align HARNESS-003 spec with non-blocking approval implementation"
```

---

## Task 12: Extend calldata-validation.test.ts with Aerodrome + cross-adapter tests

**Severity:** LOW — test coverage gap
**Feature:** TEST-002

**Files:**

- Modify: `ts-executor/tests/calldata-validation.test.ts`

**Step 1: Add Aerodrome roundtrip tests**

```typescript
describe('Aerodrome calldata validation', () => {
  it('addLiquidity encode/decode roundtrip', () => { ... });
  it('removeLiquidity encode/decode roundtrip', () => { ... });
  it('swap encode/decode roundtrip', () => { ... });
});
```

**Step 2: Add cross-adapter selector uniqueness test**

```typescript
describe('Cross-adapter selector uniqueness', () => {
  it('no selector collisions between Aave and Aerodrome', () => {
    const aaveSelectors = [encodeSupply(...).slice(0,10), encodeWithdraw(...).slice(0,10)];
    const aeroSelectors = [encodeAddLiquidity(...).slice(0,10), encodeSwap(...).slice(0,10)];
    const all = [...aaveSelectors, ...aeroSelectors];
    expect(new Set(all).size).toBe(all.length);
  });
});
```

**Step 3: Run tests**

Run: `cd ts-executor && pnpm test -- calldata-validation`
Expected: All pass

**Step 4: Commit**

```bash
git add ts-executor/tests/calldata-validation.test.ts
git commit -m "test(icarus): extend calldata tests with Aerodrome roundtrips and selector uniqueness (TEST-002)"
```

---

## Task 13: Add AERO swap liquidity check to Aerodrome LP entry

**Severity:** LOW — missing entry condition
**Feature:** STRAT-004

**Files:**

- Modify: `py-engine/strategies/aerodrome_lp.py:134-137`
- Test: `py-engine/tests/test_aerodrome_lp.py`

**Context:** STRATEGY.md requires "AERO token has sufficient swap liquidity" as an entry condition. Currently not checked.

**Step 1: Write the failing test**

```python
def test_entry_requires_aero_swap_liquidity():
    """Entry should check AERO swap liquidity."""
    # Pool with good APR/TVL but no AERO price data (proxy for no liquidity)
    snapshot = make_snapshot(
        pools=[make_pool(pool_id="sAMM-USDC/DAI", protocol="aerodrome", apy=0.05, tvl=1_000_000)],
        prices=[],  # no AERO price = no swap liquidity
    )
    strategy = AerodromeLpStrategy()
    report = strategy.evaluate(snapshot)
    assert not any(s.type == SignalType.ENTRY_MET and s.actionable for s in report.signals)
```

**Step 2: Implement**

Add AERO liquidity check to entry conditions at ~line 137:

```python
aero_has_liquidity = aero_price is not None and aero_price > 0
entry_met = entry_apr_met and entry_tvl_met and aero_has_liquidity
```

**Step 3: Run tests**

Run: `cd py-engine && uv run pytest tests/test_aerodrome_lp.py -v`

**Step 4: Commit**

```bash
git add py-engine/strategies/aerodrome_lp.py py-engine/tests/test_aerodrome_lp.py
git commit -m "fix(icarus): require AERO swap liquidity for LP entry (STRAT-004)"
```

---

## Task 14: Configure explicit block confirmation depth for EXEC-001

**Severity:** LOW — finality guarantee weaker than intended
**Feature:** EXEC-001

**Files:**

- Modify: `ts-executor/src/wallet/safe-wallet.ts` (`waitForTransactionReceipt` call)
- Test: `ts-executor/tests/safe-wallet.test.ts`

**Context:** `waitForTransactionReceipt` uses viem's 1-confirmation default. For Base L2 with `finalityBlocks: 12`, the system should wait for more confirmations.

**Step 1: Add confirmations parameter**

```typescript
const receipt = await this.publicClient.waitForTransactionReceipt({
  hash,
  confirmations: this.config.confirmations ?? 1,
  timeout: this.config.confirmationTimeoutMs,
});
```

Add `confirmations` to the SafeWalletConfig type and default to 12 for Base.

**Step 2: Run tests**

Run: `cd ts-executor && pnpm test -- safe-wallet`

**Step 3: Commit**

```bash
git add ts-executor/src/wallet/safe-wallet.ts ts-executor/tests/safe-wallet.test.ts
git commit -m "fix(icarus): configure explicit block confirmation depth for Base L2 (EXEC-001)"
```

---

## Execution Order Summary

| Task | Feature(s)         | Severity | Dependencies       |
| ---- | ------------------ | -------- | ------------------ |
| 1    | AI-002             | CRITICAL | None               |
| 2    | AI-002             | CRITICAL | Task 1 (same file) |
| 3    | AI-001, AI-002     | HIGH     | Task 2             |
| 4    | STRAT-004          | MEDIUM   | None               |
| 5    | STRAT-003          | MEDIUM   | None               |
| 6    | STRAT-003          | MEDIUM   | Task 5             |
| 7    | STRAT-001          | MEDIUM   | None               |
| 8    | RISK-009           | MEDIUM   | None               |
| 9    | RISK-001, RISK-005 | LOW      | None               |
| 10   | INFRA-001          | LOW      | None               |
| 11   | HARNESS-003        | LOW      | None               |
| 12   | TEST-002           | LOW      | None               |
| 13   | STRAT-004          | LOW      | Task 4             |
| 14   | EXEC-001           | LOW      | None               |

**Parallelizable groups:**

- Wave 1: Tasks 1, 4, 5, 7, 8, 9, 10, 11, 12, 14 (all independent)
- Wave 2: Tasks 2, 6, 13 (depend on wave 1)
- Wave 3: Task 3 (depends on task 2)
