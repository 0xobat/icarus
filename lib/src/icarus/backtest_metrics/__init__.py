"""Backtest metrics — deflated Sharpe ratio, OOS gate, multi-test correction.

Pure-compute numerical helpers used by the backtest-worker to score
candidate strategies. No I/O, no logging, no ORM.

- `deflated_sharpe` — Bailey & López de Prado (2014) deflated Sharpe ratio,
  the search objective term in the blueprint's W3 backtest engine.
- `oos_gate` — regime-segmented out-of-sample acceptance gate. Consumes
  tuples shaped like `db.models.WalkForwardResult` rows (test_sharpe,
  test_max_dd, regime_label) and returns a structured pass/fail decision.
- `multi_test` — cohort-level Bonferroni (FWER) and Benjamini-Hochberg
  (FDR) corrections. Tightens the OOS promotion gate so a large lake
  doesn't yield false-positive promotions.
"""

from __future__ import annotations

from icarus.backtest_metrics.deflated_sharpe import deflated_sharpe
from icarus.backtest_metrics.multi_test import (
    CohortCorrectionResult,
    apply_cohort_correction,
    benjamini_hochberg,
    bonferroni_correct,
    deflated_sharpe_to_pvalue,
)
from icarus.backtest_metrics.oos_gate import GateDecision, RegimeStats, oos_gate

__all__ = [
    "CohortCorrectionResult",
    "GateDecision",
    "RegimeStats",
    "apply_cohort_correction",
    "benjamini_hochberg",
    "bonferroni_correct",
    "deflated_sharpe",
    "deflated_sharpe_to_pvalue",
    "oos_gate",
]
