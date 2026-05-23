"""Protocol interfaces — DI seams between modules and services.

Every implementation in `decision-engine/`, `lake-governor/`, etc. that is
swappable (rules-based vs LLM, grid vs Bayesian, DefiLlama vs Dune) must
satisfy one of these Protocols. Tests target the Protocol, not the impl.

Stability rule (from blueprint): changing a Protocol method signature is a
breaking change for the v3 migration path. Add Protocols freely; modify
existing ones with care.

Async vs sync convention:
  - Async: any method that touches network, disk, or sleeps (data adapters,
    extractor calls, long-running backtests).
  - Sync: pure compute (regime classification, decay detection, allocation).
"""

from icarus.protocols.allocator import (
    ADVISOR_ERROR_PREFIX,
    AllocationDecision,
    Allocator,
)
from icarus.protocols.backtest import (
    BacktestEngine,
    BayesianSearchConfig,
    GridSearchConfig,
    ResultSurface,
    SearchConfig,
)
from icarus.protocols.data import DataAdapter
from icarus.protocols.decay import DecayDetector, DecayEvent
from icarus.protocols.extractor import Extractor, ExtractorOutput
from icarus.protocols.regime import (
    FundingRegime,
    Regime,
    RegimeClassifier,
    TrendRegime,
    TvlRegime,
    VolRegime,
)

__all__ = [
    "ADVISOR_ERROR_PREFIX",
    "AllocationDecision",
    "Allocator",
    "BacktestEngine",
    "BayesianSearchConfig",
    "DataAdapter",
    "DecayDetector",
    "DecayEvent",
    "Extractor",
    "ExtractorOutput",
    "FundingRegime",
    "GridSearchConfig",
    "Regime",
    "RegimeClassifier",
    "ResultSurface",
    "SearchConfig",
    "TrendRegime",
    "TvlRegime",
    "VolRegime",
]
