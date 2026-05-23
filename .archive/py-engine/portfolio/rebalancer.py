"""Rebalancing engine — detect drift, produce rebalance signals for decision pipeline.

The rebalancer is an analyst: it detects allocation drift and produces
``rebalance_needed`` signals compatible with the Strategy protocol's report
format.  The actual rebalancing decisions go through the normal decision
pipeline (Claude decides specifics).

Legacy ``generate_orders()`` is preserved for backward compatibility but the
primary interface is ``evaluate()``, which returns observations, signals, and
recommendations for the insight synthesizer.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from monitoring.logger import get_logger

_logger = get_logger("portfolio-rebalancer", enable_file=False)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RebalanceConfig:
    """Tunable parameters for the rebalancing engine.

    Args:
        drift_threshold_pct: Minimum drift from target to trigger a rebalance
            (e.g. 0.05 = 5%).
        min_trade_usd: Minimum trade size in USD; smaller actions are dropped.
        max_gas_cost_pct: Maximum gas cost as a fraction of trade value
            (e.g. 0.02 = 2%).
        cooldown_seconds: Minimum seconds between rebalance executions.
    """

    drift_threshold_pct: Decimal = Decimal("0.05")
    min_trade_usd: Decimal = Decimal("50")
    max_gas_cost_pct: Decimal = Decimal("0.02")
    cooldown_seconds: int = 3600


# ---------------------------------------------------------------------------
# Rebalance action
# ---------------------------------------------------------------------------
@dataclass
class RebalanceAction:
    """A single rebalancing adjustment to bring a position toward its target.

    Args:
        protocol: Protocol identifier (e.g. ``aave_v3``).
        chain: Target chain (e.g. ``ethereum``).
        action: Direction of the adjustment — ``increase`` or ``decrease``.
        token: Token symbol being adjusted.
        amount_usd: Absolute USD value of the adjustment.
        current_pct: Current allocation percentage for this position.
        target_pct: Target allocation percentage for this position.
    """

    protocol: str
    chain: str
    action: str  # "increase" | "decrease"
    token: str
    amount_usd: Decimal
    current_pct: Decimal
    target_pct: Decimal


# ---------------------------------------------------------------------------
# Rebalancer
# ---------------------------------------------------------------------------
class PortfolioRebalancer:
    """Detect portfolio drift and generate gas-aware rebalancing orders.

    Compares current allocation percentages against target percentages,
    identifies positions that have drifted beyond the configured threshold,
    and produces ``RebalanceAction`` objects. Actions are filtered for gas
    efficiency and subject to a cooldown between rebalance cycles.
    """

    def __init__(self, config: RebalanceConfig | None = None) -> None:
        self._config = config or RebalanceConfig()
        self._last_rebalance: float | None = None

    @property
    def config(self) -> RebalanceConfig:
        """Return the current rebalance configuration."""
        return self._config

    # ------------------------------------------------------------------
    # Drift detection
    # ------------------------------------------------------------------

    def should_rebalance(
        self,
        current: dict[str, Decimal],
        target: dict[str, Decimal],
    ) -> bool:
        """Return True if any position drifts beyond the threshold.

        Args:
            current: Mapping of position key to current allocation fraction.
            target: Mapping of position key to target allocation fraction.

        Returns:
            True when at least one key exceeds ``drift_threshold_pct``.
        """
        all_keys = set(current) | set(target)
        for key in all_keys:
            cur = current.get(key, Decimal(0))
            tgt = target.get(key, Decimal(0))
            if abs(cur - tgt) > self._config.drift_threshold_pct:
                return True
        return False

    def check_drift(
        self,
        current_allocations: dict[str, Decimal],
        target_allocations: dict[str, Decimal],
        *,
        total_value_usd: Decimal = Decimal(0),
        protocol_map: dict[str, str] | None = None,
        chain_map: dict[str, str] | None = None,
    ) -> list[RebalanceAction]:
        """Identify positions that have drifted beyond threshold.

        Only positions whose absolute drift exceeds ``drift_threshold_pct``
        are included.  Actions with a USD value below ``min_trade_usd`` are
        dropped to avoid dust trades.

        Args:
            current_allocations: Position key to current allocation fraction.
            target_allocations: Position key to target allocation fraction.
            total_value_usd: Total portfolio value used to compute USD amounts.
            protocol_map: Optional mapping of position key to protocol name.
            chain_map: Optional mapping of position key to chain name.

        Returns:
            List of ``RebalanceAction`` objects sorted largest-drift-first.
        """
        protocol_map = protocol_map or {}
        chain_map = chain_map or {}
        actions: list[RebalanceAction] = []

        all_keys = set(current_allocations) | set(target_allocations)
        for key in all_keys:
            cur = current_allocations.get(key, Decimal(0))
            tgt = target_allocations.get(key, Decimal(0))
            drift = cur - tgt

            if abs(drift) <= self._config.drift_threshold_pct:
                continue

            amount_usd = abs(drift) * total_value_usd

            if amount_usd < self._config.min_trade_usd:
                _logger.debug(
                    "Skipping dust rebalance",
                    extra={"data": {
                        "key": key, "amount_usd": str(amount_usd),
                        "min_trade": str(self._config.min_trade_usd),
                    }},
                )
                continue

            direction = "decrease" if drift > 0 else "increase"
            actions.append(RebalanceAction(
                protocol=protocol_map.get(key, "unknown"),
                chain=chain_map.get(key, "ethereum"),
                action=direction,
                token=key,
                amount_usd=amount_usd,
                current_pct=cur,
                target_pct=tgt,
            ))

        # Sort by largest drift first so the most impactful action comes first
        actions.sort(key=lambda a: a.amount_usd, reverse=True)

        if actions:
            _logger.info(
                "Drift detected",
                extra={"data": {
                    "action_count": len(actions),
                    "total_rebalance_usd": str(sum(a.amount_usd for a in actions)),
                }},
            )

        return actions

    # ------------------------------------------------------------------
    # Gas efficiency
    # ------------------------------------------------------------------

    def is_gas_efficient(self, action: RebalanceAction, gas_cost_usd: Decimal) -> bool:
        """Return True if the gas cost is acceptable relative to trade size.

        Args:
            action: The rebalancing action to evaluate.
            gas_cost_usd: Estimated gas cost in USD.

        Returns:
            True when ``gas_cost_usd / action.amount_usd`` is at or below
            ``max_gas_cost_pct``.
        """
        if action.amount_usd == 0:
            return False
        ratio = gas_cost_usd / action.amount_usd
        efficient = ratio <= self._config.max_gas_cost_pct
        if not efficient:
            _logger.debug(
                "Action not gas-efficient",
                extra={"data": {
                    "token": action.token,
                    "gas_cost": str(gas_cost_usd),
                    "amount_usd": str(action.amount_usd),
                    "ratio": str(ratio),
                    "max_ratio": str(self._config.max_gas_cost_pct),
                }},
            )
        return efficient

    def filter_gas_efficient(
        self,
        actions: list[RebalanceAction],
        gas_cost_usd: Decimal,
    ) -> list[RebalanceAction]:
        """Keep only actions whose gas cost is within the configured limit.

        Args:
            actions: Candidate rebalancing actions.
            gas_cost_usd: Estimated per-action gas cost in USD.

        Returns:
            Filtered list of gas-efficient actions (order preserved).
        """
        return [a for a in actions if self.is_gas_efficient(a, gas_cost_usd)]

    # ------------------------------------------------------------------
    # Cooldown
    # ------------------------------------------------------------------

    def can_rebalance(self) -> bool:
        """Return True if the cooldown period has elapsed.

        Returns:
            True when no previous rebalance was recorded, or enough time
            has passed since the last one.
        """
        if self._last_rebalance is None:
            return True
        elapsed = time.monotonic() - self._last_rebalance
        return elapsed >= self._config.cooldown_seconds

    def record_rebalance(self) -> None:
        """Mark that a rebalance just occurred, starting the cooldown timer."""
        self._last_rebalance = time.monotonic()
        _logger.info("Rebalance recorded, cooldown started")

    # ------------------------------------------------------------------
    # Signal evaluation (primary interface — PORT-003)
    # ------------------------------------------------------------------

    def evaluate(
        self,
        current_allocations: dict[str, Decimal],
        target_allocations: dict[str, Decimal],
        total_value_usd: Decimal,
        protocol_map: dict[str, str] | None = None,
        chain_map: dict[str, str] | None = None,
    ) -> dict:
        """Evaluate portfolio drift and produce a rebalance report.

        Returns a dict compatible with StrategyReport signal format.
        The report flows into the insight snapshot so Claude can reason
        about rebalancing through the normal decision pipeline.

        Args:
            current_allocations: Position key to current allocation fraction.
            target_allocations: Position key to target allocation fraction.
            total_value_usd: Total portfolio value in USD.
            protocol_map: Optional mapping of position key to protocol name.
            chain_map: Optional mapping of position key to chain name.

        Returns:
            Dict with ``observations``, ``signals``, and ``recommendation``
            keys, compatible with the StrategyReport structure.
        """
        observations: list[dict[str, str]] = []
        signals: list[dict] = []
        recommendation: dict | None = None

        all_keys = set(current_allocations) | set(target_allocations)

        drifted_keys: list[dict] = []
        for key in sorted(all_keys):
            cur = current_allocations.get(key, Decimal(0))
            tgt = target_allocations.get(key, Decimal(0))
            drift = cur - tgt

            # Record an observation for every tracked position
            drift_pct = drift * 100
            observations.append({
                "metric": f"allocation_drift_{key}",
                "value": f"{drift_pct:+.1f}%",
                "context": (
                    f"{key} allocation at {cur * 100:.1f}%, "
                    f"target is {tgt * 100:.1f}%, "
                    f"drift {drift_pct:+.1f}%"
                ),
            })

            if abs(drift) > self._config.drift_threshold_pct:
                amount_usd = abs(drift) * total_value_usd
                if amount_usd >= self._config.min_trade_usd:
                    drifted_keys.append({
                        "key": key,
                        "drift": drift,
                        "amount_usd": amount_usd,
                        "current_pct": cur,
                        "target_pct": tgt,
                        "protocol": (protocol_map or {}).get(key, "unknown"),
                        "chain": (chain_map or {}).get(key, "base"),
                    })

        actionable = len(drifted_keys) > 0 and self.can_rebalance()

        if drifted_keys:
            # Sort by largest drift first
            drifted_keys.sort(key=lambda d: d["amount_usd"], reverse=True)

            details_parts = []
            for d in drifted_keys:
                direction = "over" if d["drift"] > 0 else "under"
                details_parts.append(
                    f"{d['key']} {direction}-allocated by "
                    f"{abs(d['drift']) * 100:.1f}% "
                    f"(${d['amount_usd']:.0f})"
                )

            signals.append({
                "type": "rebalance_needed",
                "actionable": actionable,
                "details": "; ".join(details_parts),
            })

            # Build recommendation with suggested adjustments
            parameters: dict = {"adjustments": []}
            for d in drifted_keys:
                direction = "decrease" if d["drift"] > 0 else "increase"
                parameters["adjustments"].append({
                    "token": d["key"],
                    "direction": direction,
                    "amount_usd": str(d["amount_usd"]),
                    "current_pct": str(d["current_pct"]),
                    "target_pct": str(d["target_pct"]),
                    "protocol": d["protocol"],
                    "chain": d["chain"],
                })

            recommendation = {
                "action": "rebalance",
                "reasoning": (
                    f"{len(drifted_keys)} position(s) drifted beyond "
                    f"{self._config.drift_threshold_pct * 100:.0f}% threshold"
                ),
                "parameters": parameters,
            }

            _logger.info(
                "Rebalance signal produced",
                extra={"data": {
                    "drifted_count": len(drifted_keys),
                    "actionable": actionable,
                    "total_drift_usd": str(
                        sum(d["amount_usd"] for d in drifted_keys)
                    ),
                }},
            )
        else:
            _logger.debug("No significant drift detected")

        return {
            "strategy_id": "rebalancer",
            "timestamp": datetime.now(UTC).isoformat(),
            "observations": observations,
            "signals": signals,
            "recommendation": recommendation,
        }

    # ------------------------------------------------------------------
    # Order generation (legacy — kept for backward compatibility)
    # ------------------------------------------------------------------

    def generate_orders(
        self,
        actions: list[RebalanceAction],
        correlation_id: str,
    ) -> list[dict]:
        """Convert rebalance actions into execution order dicts.

        Produces one order per action, formatted to match the
        ``execution-orders.schema.json`` contract.  Only the first action
        is converted to enforce the one-adjustment-per-cycle rule.

        Args:
            actions: Rebalancing actions to convert.
            correlation_id: Correlation ID for tracing.

        Returns:
            A list containing at most one execution order dict.
        """
        if not actions:
            return []

        # One adjustment per cycle — take the largest-impact action only
        action = actions[0]

        order_action = "withdraw" if action.action == "decrease" else "supply"
        order_id = uuid.uuid4().hex

        order: dict = {
            "version": "1.0.0",
            "orderId": order_id,
            "correlationId": correlation_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "chain": action.chain,
            "protocol": action.protocol,
            "action": order_action,
            "strategy": "rebalancer",
            "priority": "normal",
            "params": {
                "tokenIn": action.token,
                "amount": str(action.amount_usd),
            },
            "limits": {
                "maxGasWei": "0",
                "maxSlippageBps": 50,
                "deadlineUnix": int(time.time()) + 300,
            },
        }

        _logger.info(
            "Rebalance order generated",
            extra={"data": {
                "orderId": order_id,
                "correlationId": correlation_id,
                "token": action.token,
                "action": order_action,
                "amount_usd": str(action.amount_usd),
            }},
        )

        return [order]
