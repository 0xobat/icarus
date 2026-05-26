"""Redis bus envelopes — pydantic models for cross-service messages.

Three runtime channel families (per blueprint §"Inter-process contracts"),
each partitioned by chain via the channel name:

  market:events:{base|solana}        chain executor → decision-engine
  execution:orders:{base|solana}     decision-engine → chain executor
  execution:results:{base|solana}    chain executor → decision-engine

Plus two worker queues (Redis lists, BLMOVE atomic claim):

  research:papers:pending            operator/scraper → extractor-worker
  research:search:pending            decision-engine/operator → backtest-worker

Every envelope carries:
  - `version`: schema semver, currently "1.0.0".
  - `chain`: redundant with channel name but always present for audit /
    cross-channel routing / receive-side validation.
  - `correlation_id`: traces a request → response chain across services.

Order + result envelopes also carry `template_id` and `candidate_id`. The
v4.2 schemas used `strategy: <id>` — v2 extends to the two-level template
+ candidate addressing so the allocator can demote a single candidate
without affecting siblings.

Solana-specific fields (slot, signature, lamports, priority_fee) live
under `chain_specific` in each envelope so the EVM half of the bus does
not pay a schema cost.
"""

from icarus.envelopes.market_events import (
    BaseChainSpecific,
    MarketEvent,
    MarketEventType,
    SolanaChainSpecific,
)
from icarus.envelopes.orders import (
    ExecutionOrder,
    OrderAction,
    OrderLimits,
    OrderParams,
    OrderPriority,
    SolanaSpecificOrder,
)
from icarus.envelopes.research import PaperJob, SearchJob, SourceType
from icarus.envelopes.results import ExecutionResult, ExecutionStatus, SolanaSpecificResult

__all__ = [
    "BaseChainSpecific",
    "ExecutionOrder",
    "ExecutionResult",
    "ExecutionStatus",
    "MarketEvent",
    "MarketEventType",
    "OrderAction",
    "OrderLimits",
    "OrderParams",
    "OrderPriority",
    "PaperJob",
    "SearchJob",
    "SolanaChainSpecific",
    "SolanaSpecificOrder",
    "SolanaSpecificResult",
    "SourceType",
]
