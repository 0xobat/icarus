"""Protocol TVL monitor circuit breaker — RISK-005.

Track Total Value Locked across DeFi protocols using dual sources
(DeFi Llama API + on-chain). Maintain a 24h rolling window of TVL
snapshots per protocol. Trigger emergency withdrawal when TVL drops
>30% in 24h. Configurable thresholds: warning at 15%, critical at 30%.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from monitoring.logger import get_logger

_logger = get_logger("tvl-monitor", enable_file=False)

# Default thresholds
WARNING_THRESHOLD = Decimal("0.15")  # 15%
CRITICAL_THRESHOLD = Decimal("0.30")  # 30%
DEFAULT_WINDOW_HOURS = 24


@dataclass
class TVLSnapshot:
    """A single TVL observation for a protocol on a chain.

    Attributes:
        protocol: Protocol identifier (e.g. "aave", "aerodrome").
        chain: Chain identifier (e.g. "ethereum", "base").
        tvl_usd: Total Value Locked in USD.
        source: Data source (e.g. "defillama", "on-chain").
        timestamp: When the snapshot was captured.
    """

    protocol: str
    chain: str
    tvl_usd: Decimal
    source: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class TVLMonitorConfig:
    """Configuration for the TVL monitor.

    Attributes:
        warning_threshold: Drop fraction that triggers a warning (default 0.15).
        critical_threshold: Drop fraction that triggers emergency withdrawal (default 0.30).
        window_hours: Rolling window size in hours for TVL comparison (default 24).
    """

    warning_threshold: Decimal = WARNING_THRESHOLD
    critical_threshold: Decimal = CRITICAL_THRESHOLD
    window_hours: int = DEFAULT_WINDOW_HOURS


class TVLMonitor:
    """Protocol TVL circuit breaker.

    Track TVL across DeFi protocols and trigger withdrawals when TVL
    drops beyond configured thresholds within a rolling window.

    - At 15% TVL drop: warning alert
    - At 30% TVL drop: critical alert, trigger emergency withdrawal
    """

    def __init__(self, config: TVLMonitorConfig | None = None) -> None:
        self._config = config or TVLMonitorConfig()
        self._snapshots: dict[tuple[str, str], list[TVLSnapshot]] = defaultdict(list)
        self._alerts: list[dict[str, Any]] = []

    @property
    def config(self) -> TVLMonitorConfig:
        """Return the current monitor configuration."""
        return self._config

    @property
    def alerts(self) -> list[dict[str, Any]]:
        """Return a copy of all TVL alerts."""
        return list(self._alerts)

    def record_tvl(
        self,
        protocol: str,
        chain: str,
        tvl_usd: Decimal,
        source: str,
    ) -> None:
        """Record a TVL snapshot for a protocol on a chain.

        Args:
            protocol: Protocol identifier.
            chain: Chain identifier.
            tvl_usd: Current TVL in USD.
            source: Data source name.
        """
        key = (protocol, chain)
        snapshot = TVLSnapshot(
            protocol=protocol,
            chain=chain,
            tvl_usd=tvl_usd,
            source=source,
        )
        self._snapshots[key].append(snapshot)
        self._prune_window(key)

        _logger.debug(
            "Recorded TVL snapshot",
            extra={"data": {
                "protocol": protocol,
                "chain": chain,
                "tvl_usd": str(tvl_usd),
                "source": source,
            }},
        )

    def check_protocol(self, protocol: str, chain: str) -> dict[str, Any]:
        """Check TVL health for a specific protocol on a chain.

        Args:
            protocol: Protocol identifier.
            chain: Chain identifier.

        Returns:
            Dict with keys: status, current_tvl, peak_tvl, drop_pct.
            Status is one of "normal", "warning", "critical", or "no_data".
        """
        key = (protocol, chain)
        snapshots = self._snapshots.get(key, [])

        if not snapshots:
            return {
                "status": "no_data",
                "current_tvl": None,
                "peak_tvl": None,
                "drop_pct": None,
            }

        current_tvl = snapshots[-1].tvl_usd
        peak_tvl = max(s.tvl_usd for s in snapshots)

        if peak_tvl <= 0:
            return {
                "status": "normal",
                "current_tvl": current_tvl,
                "peak_tvl": peak_tvl,
                "drop_pct": Decimal(0),
            }

        drop_pct = (peak_tvl - current_tvl) / peak_tvl

        status = self._classify_drop(drop_pct)
        now = datetime.now(UTC).isoformat()

        if status == "critical":
            alert = {
                "level": "critical",
                "protocol": protocol,
                "chain": chain,
                "drop_pct": str(drop_pct),
                "peak_tvl": str(peak_tvl),
                "current_tvl": str(current_tvl),
                "action": "emergency_withdrawal",
                "timestamp": now,
            }
            self._alerts.append(alert)
            _logger.critical(
                "CRITICAL TVL drop — emergency withdrawal triggered",
                extra={"data": alert},
            )
        elif status == "warning":
            alert = {
                "level": "warning",
                "protocol": protocol,
                "chain": chain,
                "drop_pct": str(drop_pct),
                "peak_tvl": str(peak_tvl),
                "current_tvl": str(current_tvl),
                "action": "monitor_closely",
                "timestamp": now,
            }
            self._alerts.append(alert)
            _logger.warning(
                "WARNING TVL drop — monitoring closely",
                extra={"data": alert},
            )

        return {
            "status": status,
            "current_tvl": current_tvl,
            "peak_tvl": peak_tvl,
            "drop_pct": drop_pct,
        }

    def get_withdrawal_targets(self) -> list[tuple[str, str]]:
        """Return all (protocol, chain) pairs that have breached the critical threshold.

        Returns:
            List of (protocol, chain) tuples requiring emergency withdrawal.
        """
        targets: list[tuple[str, str]] = []
        for key in self._snapshots:
            protocol, chain = key
            if self.should_withdraw(protocol, chain):
                targets.append(key)
        return targets

    def should_withdraw(self, protocol: str, chain: str) -> bool:
        """Check if a protocol has breached the critical TVL drop threshold.

        Args:
            protocol: Protocol identifier.
            chain: Chain identifier.

        Returns:
            True if TVL drop exceeds the critical threshold.
        """
        key = (protocol, chain)
        snapshots = self._snapshots.get(key, [])
        if not snapshots:
            return False

        peak_tvl = max(s.tvl_usd for s in snapshots)
        if peak_tvl <= 0:
            return False

        current_tvl = snapshots[-1].tvl_usd
        drop_pct = (peak_tvl - current_tvl) / peak_tvl
        return drop_pct > self._config.critical_threshold

    def is_healthy(self, protocol: str, chain: str) -> bool:
        """Check if a protocol's TVL is within normal range.

        Args:
            protocol: Protocol identifier.
            chain: Chain identifier.

        Returns:
            True if TVL drop is below the warning threshold (or no data exists).
        """
        key = (protocol, chain)
        snapshots = self._snapshots.get(key, [])
        if not snapshots:
            return True

        peak_tvl = max(s.tvl_usd for s in snapshots)
        if peak_tvl <= 0:
            return True

        current_tvl = snapshots[-1].tvl_usd
        drop_pct = (peak_tvl - current_tvl) / peak_tvl
        return drop_pct < self._config.warning_threshold

    def get_all_statuses(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Return health status for all monitored protocols.

        Returns:
            Dict mapping (protocol, chain) to their check_protocol result.
        """
        statuses: dict[tuple[str, str], dict[str, Any]] = {}
        for key in self._snapshots:
            protocol, chain = key
            statuses[key] = self.check_protocol(protocol, chain)
        return statuses

    def reset(self, protocol: str, chain: str) -> None:
        """Clear TVL history for a protocol after withdrawal.

        Args:
            protocol: Protocol identifier.
            chain: Chain identifier.
        """
        key = (protocol, chain)
        if key in self._snapshots:
            del self._snapshots[key]
            _logger.info(
                "Reset TVL history after withdrawal",
                extra={"data": {
                    "protocol": protocol,
                    "chain": chain,
                }},
            )

    def get_active_protocols(
        self, positions: list[dict[str, Any]],
    ) -> set[str]:
        """Extract protocol identifiers from active positions.

        Args:
            positions: List of position dicts from PositionTracker.query().

        Returns:
            Set of protocol identifiers that have active positions.
        """
        protocols: set[str] = set()
        for pos in positions:
            protocol = pos.get("protocol", "")
            if protocol:
                protocols.add(protocol)
        return protocols

    def check_active_protocols(
        self,
        positions: list[dict[str, Any]],
        chain: str = "base",
    ) -> dict[str, dict[str, Any]]:
        """Check TVL health only for protocols with active positions.

        Args:
            positions: List of position dicts from PositionTracker.query().
            chain: Chain to check (default "base").

        Returns:
            Dict mapping protocol to check_protocol result, only for
            protocols with active positions.
        """
        active = self.get_active_protocols(positions)
        results: dict[str, dict[str, Any]] = {}
        for protocol in active:
            results[protocol] = self.check_protocol(protocol, chain)
        return results

    def generate_withdrawal_orders(
        self,
        positions: list[dict[str, Any]],
        correlation_id: str,
    ) -> list[dict[str, Any]]:
        """Generate schema-compliant CB:tvl_drop withdrawal orders.

        Checks which protocols have breached the critical TVL threshold,
        filters positions to only those on affected protocols, and generates
        one withdrawal order per affected position.

        Args:
            positions: List of position dicts from PositionTracker.query().
            correlation_id: Correlation ID for tracing.

        Returns:
            List of execution-orders-schema-compliant withdrawal orders.
        """
        targets = self.get_withdrawal_targets()
        if not targets:
            return []

        # Build set of affected protocol names
        affected_protocols: set[str] = set()
        for protocol, _chain in targets:
            affected_protocols.add(protocol)

        orders: list[dict[str, Any]] = []
        now = datetime.now(UTC).isoformat()
        deadline = int(time.time()) + 300

        for pos in positions:
            pos_protocol = pos.get("protocol", "")
            if pos_protocol not in affected_protocols:
                continue

            asset = pos.get("asset", pos.get("tokenIn", "unknown"))
            amount = str(pos.get("current_value", pos.get("value_usd", "0")))

            order: dict[str, Any] = {
                "version": "1.0.0",
                "orderId": uuid.uuid4().hex,
                "correlationId": correlation_id,
                "timestamp": now,
                "chain": pos.get("chain", "base"),
                "protocol": pos_protocol,
                "action": "withdraw",
                "strategy": "CB:tvl_drop",
                "priority": "urgent",
                "params": {
                    "tokenIn": asset,
                    "amount": amount,
                },
                "limits": {
                    "maxGasWei": "500000000000000",
                    "maxSlippageBps": 50,
                    "deadlineUnix": deadline,
                },
            }
            orders.append(order)

            _logger.warning(
                "CB:tvl_drop withdrawal order generated",
                extra={"data": {
                    "protocol": pos_protocol,
                    "asset": asset,
                    "amount": amount,
                    "orderId": order["orderId"],
                }},
            )

        return orders

    def _prune_window(self, key: tuple[str, str]) -> None:
        """Remove snapshots older than the rolling window.

        Args:
            key: (protocol, chain) tuple.
        """
        cutoff = datetime.now(UTC) - timedelta(hours=self._config.window_hours)
        self._snapshots[key] = [
            s for s in self._snapshots[key] if s.timestamp >= cutoff
        ]

    def _classify_drop(self, drop_pct: Decimal) -> str:
        """Classify a TVL drop percentage into a severity level.

        Args:
            drop_pct: TVL drop as a fraction (0.0 to 1.0).

        Returns:
            One of "normal", "warning", or "critical".
        """
        if drop_pct > self._config.critical_threshold:
            return "critical"
        if drop_pct >= self._config.warning_threshold:
            return "warning"
        return "normal"
