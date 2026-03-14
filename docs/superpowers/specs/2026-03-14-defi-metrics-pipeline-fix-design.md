# DeFi Metrics → Strategy Data Pipeline Fix — Design Spec

**Date:** 2026-03-14
**Status:** Approved
**Scope:** Fix the integration layer in `main.py` that maps DeFi metrics collector output into `PoolState` objects consumed by strategies. Add debug logging and an integration test.

---

## Problem

Strategies never produce actionable signals because `main.py:_evaluate_strategies` builds `PoolState` objects with incorrect data:

| # | Bug | Producer | Consumer | Impact |
|---|-----|----------|----------|--------|
| 1 | Protocol key mismatch | `main.py` passes `"aave"` | `aave_lending.py` filters for `"aave_v3"` | All Aave pools filtered out |
| 2 | Dict key mismatch | Aerodrome metrics use `"pools"` key | `main.py` reads `"markets"` key | No Aerodrome pools built |
| 3 | TVL field mismatch (Aave) | Aave metrics use `"available_liquidity"` | `main.py` reads `"tvl"` | TVL defaults to 0, fails $1M threshold |
| 4 | TVL field mismatch (Aerodrome) | Aerodrome metrics use `"tvl_usd"` | `main.py` reads `"tvl"` | TVL defaults to 0 |
| 5 | Evaluation ordering | `_evaluate_strategies()` runs after `synthesizer.update_strategy_reports()` | Synthesizer gets stale/empty reports | Claude never sees current-cycle strategy data |

## Solution

Four changes, all in `py-engine/`. No changes to `defi_metrics.py` output, strategy logic, Redis channels, or frontend.

### 1. Fix pool-building loop — `main.py:_evaluate_strategies`

Replace the hardcoded field reads with normalized mapping:

```python
# Protocol key mapping: defi_metrics keys → strategy-expected keys
PROTOCOL_KEY_MAP = {"aave": "aave_v3", "aerodrome": "aerodrome"}

# Inside the loop:
for protocol_key in ("aave", "aerodrome"):
    try:
        metrics = self.defi_metrics.get_metrics(protocol_key)
        if metrics and isinstance(metrics, dict):
            # Aerodrome uses "pools", Aave uses "markets"
            items = metrics.get("markets", metrics.get("pools", []))
            mapped_protocol = PROTOCOL_KEY_MAP.get(protocol_key, protocol_key)

            for item in items:
                # Normalize TVL: try each field without defaults so
                # Python truthiness correctly falls through missing keys.
                tvl = float(
                    item.get("tvl")
                    or item.get("available_liquidity")
                    or item.get("tvl_usd")
                    or 0
                )
                # Normalize APY: try "supply_apy", "apy"
                apy = float(
                    item.get("supply_apy")
                    or item.get("apy")
                    or 0
                )

                pools.append(PoolState(
                    protocol=mapped_protocol,
                    pool_id=item.get("symbol", "unknown"),
                    tvl=tvl,
                    apy=apy,
                    utilization=(
                        float(item["utilization_rate"])
                        if "utilization_rate" in item else None
                    ),
                ))
    except Exception:
        _logger.debug("Failed to build pools from %s metrics", protocol_key, exc_info=True)
```

Note: The `except` block logs at DEBUG level instead of silently passing. This is the exact code path where all 5 bugs hid — silent swallowing made them invisible.

### 2. Reorder strategy evaluation — `main.py:run_cycle`

Move `_evaluate_strategies()` call and report accumulation to happen **before** `synthesizer.update_strategy_reports()`:

Current order (broken):
```
# line ~400
self.synthesizer.update_strategy_reports(self._latest_reports)  # stale/empty
snapshot = self.synthesizer.synthesize()
# ... rebalancer evaluation ...

# line ~434
strategy_reports = self._evaluate_strategies(prices, gas)  # too late
for report in strategy_reports:
    self._latest_reports[report.strategy_id] = report
```

Fixed order:
```
# line ~400 — evaluate strategies FIRST (prices and gas are already defined earlier in run_cycle)
strategy_reports = self._evaluate_strategies(prices, gas)
for report in strategy_reports:
    self._latest_reports[report.strategy_id] = report

# THEN feed current reports to synthesizer
self.synthesizer.update_strategy_reports(self._latest_reports)
snapshot = self.synthesizer.synthesize()
# ... rebalancer evaluation ...

# The snapshot_dict["strategy_reports"] injection at lines ~440-443 remains unchanged —
# it reads from self._latest_reports which is already populated by this point.
```

### 3. Add debug logging — both strategies

**`aave_lending.py` — in `_filter_pools` method (line ~191):**
```python
_logger.debug("Pool filter input: %d pools, required protocol=%s", len(pools), ALLOWED_PROTOCOL)
# After filtering:
_logger.debug("Pool filter result: %d eligible (protocol match, TVL >= $1M, APY > 0)", len(eligible))
for p in pools:
    if p not in eligible:
        reasons = []
        if p.protocol != ALLOWED_PROTOCOL:
            reasons.append(f"protocol={p.protocol}")
        if p.tvl < MIN_LIQUIDITY_USD:
            reasons.append(f"tvl=${p.tvl:,.0f}")
        if p.apy <= 0:
            reasons.append(f"apy={p.apy}")
        _logger.debug("  Filtered out: %s — %s", p.pool_id, ", ".join(reasons))
```

**`aave_lending.py` — in `_check_entry` method (line ~201):**
```python
_logger.debug("Entry check: pool=%s apy=%.4f current=%.4f improvement=%.4f threshold=%.4f",
              pool.pool_id, pool.apy, current_apy, improvement, MIN_APY_IMPROVEMENT)
```

**`aerodrome_lp.py` — inline in `evaluate()` method:**

Pool filtering is inline at lines ~80-83 (no separate method). Add debug logging after the comprehension that filters pools:
```python
# After line ~83 where eligible pools are computed:
_logger.debug("Aerodrome pool filter: %d input, %d eligible (protocol=aerodrome, TVL>$500K)", len(snapshot.pools), len(eligible))
```

Entry check is inline at lines ~130-145. Add debug logging after threshold checks:
```python
# After APR threshold check:
_logger.debug("Aerodrome entry check: pool=%s apr=%.4f threshold=%.4f tvl=$%.0f", pool.pool_id, apr, MIN_EMISSION_APR, pool.tvl)
```

Both strategies need `from monitoring.logger import get_logger` and `_logger = get_logger("strategy-name", enable_file=False)` at module level.

All at `DEBUG` level — invisible in normal operation, visible with `LOG_LEVEL=DEBUG`.

### 4. Integration test

New file `tests/test_metrics_integration.py` that validates the full chain with concrete mock data shapes matching actual `defi_metrics.py` output:

```python
def test_defi_metrics_to_pool_state_aave():
    """Verify main.py maps Aave defi_metrics output to PoolState with correct protocol and TVL."""
    # Mock defi_metrics.get_metrics("aave") returns:
    aave_metrics = {
        "markets": [
            {
                "symbol": "USDC",
                "supply_apy": 0.0312,
                "utilization_rate": 0.82,
                "available_liquidity": 5_000_000.0,
            },
        ],
    }
    # Call _evaluate_strategies() with mocked defi_metrics
    # Assert: PoolState.protocol == "aave_v3"
    # Assert: PoolState.tvl == 5_000_000.0 (from available_liquidity)
    # Assert: PoolState.apy == 0.0312 (from supply_apy)
    # Assert: PoolState.pool_id == "USDC"


def test_defi_metrics_to_pool_state_aerodrome():
    """Verify main.py maps Aerodrome defi_metrics output to PoolState."""
    # Mock defi_metrics.get_metrics("aerodrome") returns:
    aerodrome_metrics = {
        "pools": [
            {
                "symbol": "USDC/USDbC",
                "tvl_usd": 2_000_000.0,
                "apy": 0.045,
            },
        ],
    }
    # Call _evaluate_strategies() with mocked defi_metrics
    # Assert: PoolState.protocol == "aerodrome"
    # Assert: PoolState.tvl == 2_000_000.0 (from tvl_usd)
    # Assert: PoolState.apy == 0.045 (from apy)
    # Assert: PoolState.pool_id == "USDC/USDbC"
```

## Files Changed

| File | Change |
|------|--------|
| `py-engine/main.py` | Fix pool-building loop (lines ~244-259), reorder evaluation (lines ~400-440) |
| `py-engine/strategies/aave_lending.py` | Add logger import, debug logging in `_filter_pools` and `_check_entry` |
| `py-engine/strategies/aerodrome_lp.py` | Add logger import, debug logging inline in `evaluate()` after pool filtering (~line 83) and entry check (~line 141) |
| `py-engine/tests/test_metrics_integration.py` | New integration test with concrete Aave and Aerodrome mock data shapes |

## Files NOT Changed

- `py-engine/data/defi_metrics.py` — Output shape unchanged
- `py-engine/strategies/base.py` — PoolState dataclass unchanged
- Strategy thresholds — No parameter changes
- Frontend — No changes
- Shared schemas — No changes

## Success Criteria

1. `docker compose up` → strategies evaluate with real pool data (non-zero TVL and APY)
2. `docker compose logs py-engine` shows DEBUG pool filter results when LOG_LEVEL=DEBUG
3. Strategy signals fire when market conditions meet thresholds (APY > 0.5% for LEND-001)
4. All existing tests pass
5. New integration test passes
