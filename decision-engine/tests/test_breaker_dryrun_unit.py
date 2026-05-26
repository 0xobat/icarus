"""Unit-level tests for the W11 circuit-breaker dry-run driver.

Imports each per-breaker function from ``harness/breaker_dryrun.py`` and
asserts that the driver, when fed the same synthetic state, returns all
PASS results. This double-checks the dry-run contract from the test
runner so a CI failure is caught even when the shell wrapper is not
invoked (e.g. pytest-only sweeps).

There is one test per breaker (6 total) plus a sanity test on
``main()`` to confirm the overall driver exits 0 when everything is
healthy. The driver itself is also the integration test via
``harness/breaker_dryrun.sh``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Load harness/breaker_dryrun.py as a module. It lives outside any
# installed package so we use importlib's file-spec form.
_HARNESS_PATH = Path(__file__).resolve().parents[2] / "harness" / "breaker_dryrun.py"
_spec = importlib.util.spec_from_file_location("breaker_dryrun", _HARNESS_PATH)
assert _spec is not None and _spec.loader is not None
breaker_dryrun = importlib.util.module_from_spec(_spec)
sys.modules["breaker_dryrun"] = breaker_dryrun
_spec.loader.exec_module(breaker_dryrun)


def _assert_all_pass(results: list, label: str) -> None:
    failures = [r for r in results if not r.passed]
    assert not failures, (
        f"{label}: expected all checks to pass, failures: "
        + "; ".join(f"{r.name}={r.detail}" for r in failures)
    )
    assert results, f"{label}: at least one check expected, got 0"


# ──────────────────────────────────────────────────────────────────────────────
# Per-breaker unit tests
# ──────────────────────────────────────────────────────────────────────────────


def test_drawdown_breaker_dryrun() -> None:
    """Drawdown breaker fires at 22% DD and emits valid CB:drawdown envelopes on both chains."""
    results = breaker_dryrun.run_drawdown_breaker()
    _assert_all_pass(results, "drawdown_breaker")
    names = {r.name for r in results}
    assert "drawdown_breaker[base]" in names
    assert "drawdown_breaker[solana]" in names


def test_position_loss_limit_dryrun() -> None:
    """Position loss limit closes the -12% position and validates envelopes."""
    results = breaker_dryrun.run_position_loss_limit()
    _assert_all_pass(results, "position_loss_limit")
    names = {r.name for r in results}
    assert "position_loss_limit[base]" in names
    assert "position_loss_limit[solana]" in names


def test_tvl_monitor_dryrun() -> None:
    """TVL monitor fires at -70% TVL drop and emits CB:tvl_drop envelopes."""
    results = breaker_dryrun.run_tvl_monitor()
    _assert_all_pass(results, "tvl_monitor")
    names = {r.name for r in results}
    assert "tvl_monitor[base]" in names
    assert "tvl_monitor[solana]" in names


def test_gas_spike_breaker_dryrun() -> None:
    """Gas spike breaker activates at 16x average; urgent ops exempt."""
    results = breaker_dryrun.run_gas_spike_breaker()
    _assert_all_pass(results, "gas_spike_breaker")
    assert any(r.name == "gas_spike_breaker" for r in results)


def test_oracle_guard_dryrun() -> None:
    """Oracle guard rejects 4.9% USDC deviation (>2% threshold)."""
    results = breaker_dryrun.run_oracle_guard()
    _assert_all_pass(results, "oracle_guard")
    assert any(r.name == "oracle_guard" for r in results)


def test_tx_failure_monitor_dryrun() -> None:
    """TX failure monitor pauses execution after 4 reverts in window (>threshold 3)."""
    results = breaker_dryrun.run_tx_failure_monitor()
    _assert_all_pass(results, "tx_failure_monitor")
    assert any(r.name == "tx_failure_monitor" for r in results)


# ──────────────────────────────────────────────────────────────────────────────
# Driver entry-point sanity
# ──────────────────────────────────────────────────────────────────────────────


def test_main_exits_zero_with_synthetic_state() -> None:
    """The aggregate driver entry-point returns 0 with all-PASS synthetic inputs."""
    rc = breaker_dryrun.main()
    assert rc == 0, f"breaker_dryrun.main() returned {rc} (expected 0)"


def test_run_all_covers_six_breakers() -> None:
    """run_all() exercises exactly the 6 capital-protecting breakers."""
    assert len(breaker_dryrun.BREAKER_CHECKS) == 6
    names = {n for n, _ in breaker_dryrun.BREAKER_CHECKS}
    assert names == {
        "drawdown_breaker",
        "position_loss_limit",
        "tvl_monitor",
        "gas_spike_breaker",
        "oracle_guard",
        "tx_failure_monitor",
    }


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
