"""Managed-portfolio circuit breakers + exposure cap.

The active managed gate (see `decision_engine.risk_gate`) composes:
drawdown_breaker, gas_spike_breaker, tx_failure_monitor, depeg_breaker (USDC),
lst_depeg_breaker (P3.1), and managed_exposure (P2.5 per-asset/venue caps).

The lake-era breakers (exposure_limits, position_loss_limit, tvl_monitor,
oracle_guard) were removed in the managed-portfolio cleanup — the allocation
bands + managed exposure cap are the concentration policy, and the per-strategy
loss cooldown / per-protocol exposure limiter were category errors here.
`.archive/` retains the v4.2 modules for rollback.
"""
