"""Capital allocator — equal-weight (cold-start) and risk-parity (steady-state).

Implements the blueprint W6 allocator pair:

    "allocator (cold-start equal-weight → steady-state risk-parity)"

plus the per-template lake-level cap:

    "per-template allocation_max cap; lake-level template cap"

Both impls satisfy the :class:`icarus.protocols.allocator.Allocator` Protocol.
`compose_allocators` returns a callable that switches per-candidate based on
how much return history each candidate has accumulated, so a single decision
cycle can mix cold-start newcomers with steady-state veterans without forcing
the operator to flip a global mode flag.

Pure compute. No I/O. No logging. The decision-engine wraps these in the
ordinary structured-log envelope around each ``.allocate()`` call.
"""

from __future__ import annotations

from icarus.allocator.composed import ComposedAllocator, compose_allocators
from icarus.allocator.equal_weight import EqualWeightAllocator
from icarus.allocator.risk_parity import RiskParityAllocator
from icarus.allocator.types import CandidateInput, RosterEntry

__all__ = [
    "CandidateInput",
    "ComposedAllocator",
    "EqualWeightAllocator",
    "RiskParityAllocator",
    "RosterEntry",
    "compose_allocators",
]
