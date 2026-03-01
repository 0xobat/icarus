"""Rebalancing engine — detect drift, generate gas-aware rebalancing orders."""

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
    # Order generation
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
