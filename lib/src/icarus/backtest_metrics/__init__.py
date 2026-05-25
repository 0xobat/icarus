"""Backtest metrics — deflated Sharpe ratio and OOS gate.

Pure-compute numerical helpers used by the backtest-worker to score
candidate strategies. No I/O, no logging, no ORM.

- `deflated_sharpe` — Bailey & López de Prado (2014) deflated Sharpe ratio,
  the search objective term in the blueprint's W3 backtest engine.
- `oos_gate` — regime-segmented out-of-sample acceptance gate. Consumes
  tuples shaped like `db.models.WalkForwardResult` rows (test_sharpe,
  test_max_dd, regime_label) and returns a structured pass/fail decision.
"""

from __future__ import annotations

from icarus.backtest_metrics.deflated_sharpe import deflated_sharpe
from icarus.backtest_metrics.oos_gate import GateDecision, RegimeStats, oos_gate

__all__ = ["GateDecision", "RegimeStats", "deflated_sharpe", "oos_gate"]
