"""Lake-governance change detectors — Page-Hinkley decay + per-template breaker.

Pure-compute numerical helpers used by `lake-governor` to decide when a
live candidate has decayed away from its paper-trade-baseline performance
and when a whole template's aggregate live performance has fallen far
enough below expectation to demote every candidate inside it.

- `PageHinkleyDetector` — per-candidate change-point detector on rolling
  Sharpe vs. paper-trade-baseline Sharpe (blueprint W5 default decay).
- `DetectorState` — JSON-serialisable Page-Hinkley state, persisted in
  `LakeRoster.decay_state_json` so detection survives lake-governor
  restarts.
- `TemplateBreaker` — per-template aggregate-performance breaker; trips
  when realised Sharpe drops > 2 sigma below expected over a 14-day rolling
  window.

No I/O, no logging, no ORM — caller (lake-governor) manages persistence.
"""

from __future__ import annotations

from icarus.lake_metrics.breaker import TemplateBreaker
from icarus.lake_metrics.decay import DetectorState, PageHinkleyDetector

__all__ = ["DetectorState", "PageHinkleyDetector", "TemplateBreaker"]
