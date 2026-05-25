"""Per-template aggregate-performance breaker.

Implements the blueprint W5 per-template breaker:

    "Per-template breaker trip (template's aggregate live performance
    < expected by 2 sigma over rolling 14-day window) → all candidates in
    template → demoted_paper."

`TemplateBreaker` consumes a stream of ``(timestamp, realized_sharpe)``
observations for a single template's aggregate live performance and
trips once that 14-day rolling realised Sharpe drops more than
``sigma_threshold`` (default 2.0) standard deviations below the
expected Sharpe.

Pure compute. The caller groups observations by ``template_id`` and
maintains one `TemplateBreaker` per template (state lives on the
caller's lake-governor side, typically alongside the template registry).

No I/O, no DB writes.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class _Observation:
    """One (timestamp, realised_sharpe) sample inside the rolling window."""

    timestamp: datetime
    realized_sharpe: float


class TemplateBreaker:
    """14-day rolling-window 2-sigma breaker on aggregate template Sharpe.

    The breaker is parameterised by:

    - ``expected_sharpe`` — the per-template baseline (set from paper-trade
      aggregate Sharpe at the template's promotion time).
    - ``sigma`` — the per-template realised-Sharpe standard deviation
      estimate (also from paper-trade variance).
    - ``sigma_threshold`` — number of sigma below expected that trips the
      breaker (blueprint: 2.0).
    - ``window`` — rolling-window width (blueprint: 14 days).
    - ``min_observations`` — minimum sample count inside the window
      before the breaker is allowed to fire. Prevents tripping on the
      first noisy sample after promotion.

    Latching: once the breaker has tripped, `.update()` keeps returning
    True (caller demotes the whole template and discards the breaker).
    """

    __slots__ = (
        "_expected",
        "_min_observations",
        "_observations",
        "_sigma",
        "_sigma_threshold",
        "_tripped",
        "_window",
    )

    def __init__(
        self,
        *,
        expected_sharpe: float,
        sigma: float,
        sigma_threshold: float = 2.0,
        window: timedelta = timedelta(days=14),
        min_observations: int = 3,
    ) -> None:
        if sigma <= 0:
            raise ValueError(f"sigma must be positive, got {sigma}")
        if sigma_threshold <= 0:
            raise ValueError(
                f"sigma_threshold must be positive, got {sigma_threshold}"
            )
        if window <= timedelta(0):
            raise ValueError(f"window must be positive, got {window}")
        if min_observations < 1:
            raise ValueError(
                f"min_observations must be >= 1, got {min_observations}"
            )

        self._expected = float(expected_sharpe)
        self._sigma = float(sigma)
        self._sigma_threshold = float(sigma_threshold)
        self._window = window
        self._min_observations = int(min_observations)
        self._observations: deque[_Observation] = deque()
        self._tripped = False

    @property
    def tripped(self) -> bool:
        """Whether the breaker has fired at least once (latching)."""
        return self._tripped

    @property
    def threshold(self) -> float:
        """The realised-Sharpe floor below which the breaker trips."""
        return self._expected - self._sigma_threshold * self._sigma

    @property
    def window_size(self) -> int:
        """Observation count currently inside the rolling window."""
        return len(self._observations)

    def update(
        self, timestamp: datetime, realized_sharpe: float
    ) -> bool:
        """Append one observation and re-evaluate; return True iff tripped.

        Timestamps must arrive in non-decreasing order — the caller (the
        lake-governor's per-template observer loop) controls ingestion
        order. Observations older than ``window`` relative to the newest
        timestamp are discarded before the breaker condition is checked.
        """
        if self._tripped:
            return True

        if self._observations and timestamp < self._observations[-1].timestamp:
            raise ValueError(
                "timestamps must arrive in non-decreasing order; "
                f"got {timestamp} after {self._observations[-1].timestamp}"
            )

        self._observations.append(
            _Observation(timestamp=timestamp, realized_sharpe=float(realized_sharpe))
        )

        # Evict observations strictly older than `window` relative to the
        # newest sample. Using strict-less-than keeps a sample exactly
        # `window` old (e.g. the leading edge of a sliding fortnight)
        # inside the window.
        cutoff = timestamp - self._window
        while self._observations and self._observations[0].timestamp < cutoff:
            self._observations.popleft()

        if len(self._observations) < self._min_observations:
            return False

        mean_realized = sum(o.realized_sharpe for o in self._observations) / len(
            self._observations
        )
        if mean_realized < self.threshold:
            self._tripped = True
            return True
        return False
