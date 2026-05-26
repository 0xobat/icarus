"""Page-Hinkley change-point detector for per-candidate Sharpe decay.

Implements the blueprint W5 default candidate-decay detector:

    "Page-Hinkley test on rolling 7-day Sharpe vs. paper-trade-baseline
    Sharpe, threshold tuned per-template via initial paper-trade variance
    estimate."

Page-Hinkley is a sequential change-point test on the cumulative deviation
of an observed stream from a reference mean. For decay detection we
monitor *downward* shifts: the candidate's realised Sharpe falling
significantly below its paper-trade baseline. The test stays stateful
across updates so detection survives restarts (state round-trips through
`DetectorState`, which `LakeRoster.decay_state_json` persists as JSON).

Reference: Page (1954); Mouss et al. (2004) for the standard
data-stream formulation used here.

Algorithm (downward-shift orientation, per update with observation x_t):

    m_t  = m_{t-1} + (x_t - baseline + delta)
    M_t  = max(M_{t-1}, m_t)         # running maximum of m_t
    PH_t = M_t - m_t                  # gap between running max and current
    fire iff PH_t > lambda_threshold

When x_t hovers near `baseline` (with `delta` worth of slack absorbing
noise), m_t drifts upward and M_t tracks it, so PH_t stays near zero.
When x_t persistently drops below `baseline - delta`, m_t starts
falling; M_t stays pinned at its prior maximum; PH_t = M_t - m_t grows
until it crosses `lambda_threshold` and the test fires.

`delta` is the minimum-detectable magnitude (in Sharpe units) — the test's
tolerance to small noise. `lambda_threshold` is the alarm threshold,
typically tuned from paper-trade variance so the false-positive rate on a
stationary stream is < 5%.

Pure compute. No I/O, no DB writes.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING

from icarus.inference import commentary_for_decay

if TYPE_CHECKING:
    from icarus.inference import OllamaClient


@dataclass(frozen=True, slots=True)
class DetectorState:
    """JSON-serialisable Page-Hinkley state for one candidate.

    Persisted by lake-governor into `LakeRoster.decay_state_json` so
    detection survives restarts. All fields are floats / ints so the
    state round-trips cleanly through `json.dumps` / `json.loads`.

    Attributes:
        baseline: reference mean (paper-trade-baseline Sharpe).
        delta: minimum-detectable magnitude (Sharpe units of noise tolerance).
        lambda_threshold: alarm threshold on the test statistic PH_t.
        m_t: running cumulative deviation (Page-Hinkley `m` variable).
        max_m_t: running maximum of m_t observed so far (Page-Hinkley `M`).
        n_observations: number of `.update()` calls observed.
        fired: True once the detector has tripped (latching).
    """

    baseline: float
    delta: float
    lambda_threshold: float
    m_t: float = 0.0
    max_m_t: float = 0.0
    n_observations: int = 0
    fired: bool = False

    def to_json(self) -> str:
        """Serialise to a JSON string suitable for `decay_state_json`."""
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> DetectorState:
        """Round-trip a previously-serialised state."""
        return cls(**json.loads(raw))


class PageHinkleyDetector:
    """Stateful Page-Hinkley downward-shift detector.

    One instance per candidate. Caller (`lake-governor`) constructs the
    detector once when the candidate enters `live_capped` (using the
    paper-trade-baseline Sharpe and per-template-tuned threshold), feeds
    each fresh rolling-Sharpe observation through `.update()`, persists
    `.state` after each cycle, and rehydrates via the constructor's
    `state` keyword on restart.

    The detector latches: once `.update()` has returned True, every
    subsequent `.update()` also returns True (the candidate has decayed;
    re-arming is the caller's decision, by constructing a fresh detector).
    """

    __slots__ = ("_state",)

    def __init__(
        self,
        *,
        baseline_sharpe: float | None = None,
        delta: float | None = None,
        lambda_threshold: float | None = None,
        state: DetectorState | None = None,
    ) -> None:
        """Construct from fresh parameters or from a persisted state.

        Exactly one of these forms must be used:

        - Fresh:  ``baseline_sharpe`` + ``delta`` + ``lambda_threshold``.
        - Rehydrate: ``state`` (a previously-serialised `DetectorState`).
        """
        if state is not None:
            if any(
                v is not None
                for v in (baseline_sharpe, delta, lambda_threshold)
            ):
                raise ValueError(
                    "pass either `state` or the fresh parameters, not both"
                )
            self._state = state
            return

        if baseline_sharpe is None or delta is None or lambda_threshold is None:
            raise ValueError(
                "fresh construction requires baseline_sharpe, delta, "
                "and lambda_threshold"
            )
        if delta < 0:
            raise ValueError(f"delta must be non-negative, got {delta}")
        if lambda_threshold <= 0:
            raise ValueError(
                f"lambda_threshold must be positive, got {lambda_threshold}"
            )

        self._state = DetectorState(
            baseline=float(baseline_sharpe),
            delta=float(delta),
            lambda_threshold=float(lambda_threshold),
        )

    @property
    def state(self) -> DetectorState:
        """Snapshot of internal state for persistence."""
        return self._state

    @property
    def fired(self) -> bool:
        """Whether the detector has tripped at least once (latching)."""
        return self._state.fired

    def update(self, observed_sharpe: float) -> bool:
        """Feed one observation; return True iff the detector has fired.

        Once the detector has fired it stays fired (latching) — the caller
        responds to the demotion edge by transitioning the candidate to
        `demoted_paper`, then drops the detector. Continued updates after
        a fire still return True so polling callers cannot miss the edge.
        """
        if self._state.fired:
            return True

        x = float(observed_sharpe)
        # Downward-shift orientation: accumulate (x - baseline + delta).
        # While x ≈ baseline, m_t drifts upward by delta and max_m_t tracks
        # it, so PH_t stays near zero. A persistent drop pushes m_t
        # downward; max_m_t lags behind; PH_t = max_m_t - m_t grows and
        # eventually crosses lambda_threshold.
        new_m_t = self._state.m_t + (x - self._state.baseline + self._state.delta)
        new_max_m_t = max(self._state.max_m_t, new_m_t)
        ph_t = new_max_m_t - new_m_t
        fired = ph_t > self._state.lambda_threshold

        self._state = replace(
            self._state,
            m_t=new_m_t,
            max_m_t=new_max_m_t,
            n_observations=self._state.n_observations + 1,
            fired=fired,
        )
        return fired


async def explain_decay_trip(
    *,
    client: OllamaClient,
    candidate_id: str,
    recent_sharpe_history: list[float],
) -> str:
    """Post-trip LLM advisor explanation for a Page-Hinkley decay event.

    Module-level wrapper over `icarus.inference.commentary_for_decay` so
    that lake-governor's decay-watcher imports a single domain-coherent
    symbol from the same module that owns the detector. The detector
    itself is pure compute and stays that way; this function is the
    explicit, discoverable seam where the advisor is invoked after a
    trip is observed.

    Returns the model's prose on success, or a string prefixed with
    ``ADVISOR_ERROR_PREFIX`` ("advisor error: ") on inference failure.
    Never raises — the caller's decay-handling path must continue.
    """
    return await commentary_for_decay(client, candidate_id, recent_sharpe_history)
