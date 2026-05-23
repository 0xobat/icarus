"""Pre-trade risk gate + circuit breakers.

Ported from .archive/py-engine/risk/ during W1D4 validated copy-back.
6 of 7 v4.2 modules: drawdown, exposure_limits, gas_spike, position_loss,
tvl_monitor, tx_failure_monitor. oracle_guard requires the price_feed
adapter (deferred to the lib/icarus/data_adapters/ work in W3).

Per blueprint, type adaptation for v2 envelopes (template_id, candidate_id,
chain) is the next pass after this verbatim-with-import-fixup copy-back.
The risk logic is unchanged; only the order-envelope shape evolves.
"""
