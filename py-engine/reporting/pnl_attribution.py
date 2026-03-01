"""P&L attribution engine for strategy, protocol, chain, and time-based analysis.

Breaks down total portfolio performance by multiple dimensions to answer
"where did my returns come from?" Supports export to CSV and JSON formats.
All financial calculations use ``Decimal`` for precision.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from db.repository import DatabaseRepository
from monitoring.logger import get_logger

_logger = get_logger("pnl-attribution", enable_file=False)

# Gas price constants
_WEI_PER_ETH = Decimal("1000000000000000000")
_DEFAULT_ETH_PRICE_USD = Decimal("2000")


def _ensure_aware(dt: datetime) -> datetime:
    """Normalize a naive datetime to UTC-aware."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


@dataclass
class StrategyPnL:
    """P&L attribution for a single strategy."""

    strategy: str
    pnl_usd: Decimal
    gas_cost_usd: Decimal
    trade_count: int
    net_pnl_usd: Decimal
    contribution_pct: Decimal = Decimal("0")


@dataclass
class ProtocolPnL:
    """P&L attribution for a single protocol."""

    protocol: str
    pnl_usd: Decimal
    gas_cost_usd: Decimal
    trade_count: int
    net_pnl_usd: Decimal
    contribution_pct: Decimal = Decimal("0")


@dataclass
class ChainPnL:
    """P&L attribution for a single blockchain."""

    chain: str
    pnl_usd: Decimal
    gas_cost_usd: Decimal
    trade_count: int
    net_pnl_usd: Decimal
    contribution_pct: Decimal = Decimal("0")


@dataclass
class PeriodPnL:
    """P&L for a specific time period."""

    period_start: datetime
    period_end: datetime
    period_label: str
    pnl_usd: Decimal
    gas_cost_usd: Decimal
    trade_count: int
    net_pnl_usd: Decimal


class PnLAttributionEngine:
    """P&L attribution engine for multi-dimensional analysis.

    Aggregates trade data from ``DatabaseRepository`` across strategies,
    protocols, chains, and time periods. Supports CSV and JSON export.

    Args:
        repository: The database repository for trade data access.
        eth_price_usd: ETH price in USD for gas cost calculations.
    """

    def __init__(
        self,
        repository: DatabaseRepository,
        eth_price_usd: Decimal | None = None,
    ) -> None:
        self._repo = repository
        self._eth_price_usd = eth_price_usd or _DEFAULT_ETH_PRICE_USD

    def _gas_cost_usd(self, trade: Any) -> Decimal:
        """Calculate gas cost in USD from a trade record.

        Args:
            trade: A Trade ORM instance with gas_used and gas_price_wei.

        Returns:
            Gas cost in USD, or zero if gas data unavailable.
        """
        if trade.gas_used is None or trade.gas_price_wei is None:
            return Decimal("0")
        gas_eth = (
            Decimal(str(trade.gas_used))
            * Decimal(str(trade.gas_price_wei))
            / _WEI_PER_ETH
        )
        return gas_eth * self._eth_price_usd

    def _trade_pnl(self, trade: Any) -> Decimal:
        """Calculate P&L for a single trade.

        Args:
            trade: A Trade ORM instance.

        Returns:
            P&L in USD (amount_out - amount_in).
        """
        amount_in = Decimal(str(trade.amount_in)) if trade.amount_in else Decimal("0")
        amount_out = Decimal(str(trade.amount_out)) if trade.amount_out else Decimal("0")
        return amount_out - amount_in

    def _get_trades(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Any]:
        """Fetch confirmed trades within a time range.

        Args:
            since: Start of time range (inclusive).
            until: End of time range (inclusive).

        Returns:
            List of Trade ORM instances sorted chronologically.
        """
        trades = self._repo.get_trades(
            status="confirmed", since=since, limit=100000
        )
        if until is not None:
            trades = [t for t in trades if _ensure_aware(t.timestamp) <= until]
        return trades

    def get_attribution_by_strategy(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[StrategyPnL]:
        """Compute P&L attribution grouped by strategy.

        Args:
            since: Start of analysis period.
            until: End of analysis period.

        Returns:
            List of ``StrategyPnL`` entries, one per strategy.
        """
        trades = self._get_trades(since=since, until=until)

        groups: dict[str, dict[str, Any]] = {}
        for trade in trades:
            key = trade.strategy
            if key not in groups:
                groups[key] = {
                    "pnl": Decimal("0"),
                    "gas": Decimal("0"),
                    "count": 0,
                }
            groups[key]["pnl"] += self._trade_pnl(trade)
            groups[key]["gas"] += self._gas_cost_usd(trade)
            groups[key]["count"] += 1

        total_pnl = sum(g["pnl"] for g in groups.values())

        results: list[StrategyPnL] = []
        for key, data in groups.items():
            net = data["pnl"] - data["gas"]
            contribution = Decimal("0")
            if total_pnl != 0:
                contribution = (data["pnl"] / abs(total_pnl)) * Decimal("100")
            results.append(StrategyPnL(
                strategy=key,
                pnl_usd=data["pnl"],
                gas_cost_usd=data["gas"],
                trade_count=data["count"],
                net_pnl_usd=net,
                contribution_pct=contribution,
            ))

        return results

    def get_attribution_by_protocol(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[ProtocolPnL]:
        """Compute P&L attribution grouped by protocol.

        Args:
            since: Start of analysis period.
            until: End of analysis period.

        Returns:
            List of ``ProtocolPnL`` entries, one per protocol.
        """
        trades = self._get_trades(since=since, until=until)

        groups: dict[str, dict[str, Any]] = {}
        for trade in trades:
            key = trade.protocol
            if key not in groups:
                groups[key] = {
                    "pnl": Decimal("0"),
                    "gas": Decimal("0"),
                    "count": 0,
                }
            groups[key]["pnl"] += self._trade_pnl(trade)
            groups[key]["gas"] += self._gas_cost_usd(trade)
            groups[key]["count"] += 1

        total_pnl = sum(g["pnl"] for g in groups.values())

        results: list[ProtocolPnL] = []
        for key, data in groups.items():
            net = data["pnl"] - data["gas"]
            contribution = Decimal("0")
            if total_pnl != 0:
                contribution = (data["pnl"] / abs(total_pnl)) * Decimal("100")
            results.append(ProtocolPnL(
                protocol=key,
                pnl_usd=data["pnl"],
                gas_cost_usd=data["gas"],
                trade_count=data["count"],
                net_pnl_usd=net,
                contribution_pct=contribution,
            ))

        return results

    def get_attribution_by_chain(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[ChainPnL]:
        """Compute P&L attribution grouped by blockchain.

        Args:
            since: Start of analysis period.
            until: End of analysis period.

        Returns:
            List of ``ChainPnL`` entries, one per chain.
        """
        trades = self._get_trades(since=since, until=until)

        groups: dict[str, dict[str, Any]] = {}
        for trade in trades:
            key = trade.chain
            if key not in groups:
                groups[key] = {
                    "pnl": Decimal("0"),
                    "gas": Decimal("0"),
                    "count": 0,
                }
            groups[key]["pnl"] += self._trade_pnl(trade)
            groups[key]["gas"] += self._gas_cost_usd(trade)
            groups[key]["count"] += 1

        total_pnl = sum(g["pnl"] for g in groups.values())

        results: list[ChainPnL] = []
        for key, data in groups.items():
            net = data["pnl"] - data["gas"]
            contribution = Decimal("0")
            if total_pnl != 0:
                contribution = (data["pnl"] / abs(total_pnl)) * Decimal("100")
            results.append(ChainPnL(
                chain=key,
                pnl_usd=data["pnl"],
                gas_cost_usd=data["gas"],
                trade_count=data["count"],
                net_pnl_usd=net,
                contribution_pct=contribution,
            ))

        return results

    def get_time_series(
        self,
        period: str = "daily",
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[PeriodPnL]:
        """Compute P&L broken down by time period.

        Args:
            period: Aggregation period. One of ``"daily"``, ``"weekly"``,
                ``"monthly"``.
            since: Start of analysis period. Defaults to 30 days ago.
            until: End of analysis period. Defaults to now.

        Returns:
            List of ``PeriodPnL`` entries, one per period bucket.
        """
        if until is None:
            until = datetime.now(UTC)
        if since is None:
            since = until - timedelta(days=30)

        trades = self._get_trades(since=since, until=until)
        # Sort chronologically
        trades = sorted(trades, key=lambda t: t.timestamp)

        # Build period buckets
        buckets = self._build_period_buckets(period, since, until)

        # Assign trades to buckets
        for trade in trades:
            for bucket in buckets:
                if bucket["start"] <= _ensure_aware(trade.timestamp) < bucket["end"]:
                    bucket["pnl"] += self._trade_pnl(trade)
                    bucket["gas"] += self._gas_cost_usd(trade)
                    bucket["count"] += 1
                    break

        results: list[PeriodPnL] = []
        for bucket in buckets:
            net = bucket["pnl"] - bucket["gas"]
            results.append(PeriodPnL(
                period_start=bucket["start"],
                period_end=bucket["end"],
                period_label=bucket["label"],
                pnl_usd=bucket["pnl"],
                gas_cost_usd=bucket["gas"],
                trade_count=bucket["count"],
                net_pnl_usd=net,
            ))

        return results

    def _build_period_buckets(
        self, period: str, since: datetime, until: datetime
    ) -> list[dict[str, Any]]:
        """Build time period buckets for aggregation.

        Args:
            period: Aggregation period (daily, weekly, monthly).
            since: Start timestamp.
            until: End timestamp.

        Returns:
            List of bucket dicts with start, end, label, and accumulator fields.
        """
        buckets: list[dict[str, Any]] = []

        if period == "daily":
            delta = timedelta(days=1)
        elif period == "weekly":
            delta = timedelta(weeks=1)
        elif period == "monthly":
            delta = timedelta(days=30)
        else:
            delta = timedelta(days=1)

        current = since
        while current < until:
            bucket_end = min(current + delta, until)
            label = current.strftime("%Y-%m-%d")
            if period == "weekly":
                label = f"W{current.strftime('%Y-%m-%d')}"
            elif period == "monthly":
                label = current.strftime("%Y-%m")

            buckets.append({
                "start": current,
                "end": bucket_end,
                "label": label,
                "pnl": Decimal("0"),
                "gas": Decimal("0"),
                "count": 0,
            })
            current = bucket_end

        return buckets

    def export_csv(
        self,
        attribution_data: list[StrategyPnL]
        | list[ProtocolPnL]
        | list[ChainPnL]
        | list[PeriodPnL],
        output_path: str | None = None,
    ) -> str:
        """Export attribution data to CSV format.

        Automatically detects the data type and generates appropriate
        column headers.

        Args:
            attribution_data: List of attribution dataclass instances.
            output_path: Optional file path to write the CSV. If None,
                returns the CSV content as a string.

        Returns:
            The CSV content as a string.
        """
        if not attribution_data:
            return ""

        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")

        # Determine columns from the dataclass type
        first = attribution_data[0]
        if isinstance(first, StrategyPnL):
            headers = [
                "strategy", "pnl_usd", "gas_cost_usd", "trade_count",
                "net_pnl_usd", "contribution_pct",
            ]
        elif isinstance(first, ProtocolPnL):
            headers = [
                "protocol", "pnl_usd", "gas_cost_usd", "trade_count",
                "net_pnl_usd", "contribution_pct",
            ]
        elif isinstance(first, ChainPnL):
            headers = [
                "chain", "pnl_usd", "gas_cost_usd", "trade_count",
                "net_pnl_usd", "contribution_pct",
            ]
        elif isinstance(first, PeriodPnL):
            headers = [
                "period_start", "period_end", "period_label",
                "pnl_usd", "gas_cost_usd", "trade_count", "net_pnl_usd",
            ]
        else:
            headers = list(asdict(first).keys())

        writer.writerow(headers)

        for item in attribution_data:
            row = []
            d = asdict(item)
            for h in headers:
                val = d.get(h)
                if isinstance(val, Decimal):
                    row.append(str(val))
                elif isinstance(val, datetime):
                    row.append(val.isoformat())
                else:
                    row.append(val)
            writer.writerow(row)

        csv_content = output.getvalue()

        if output_path is not None:
            with open(output_path, "w", newline="") as f:
                f.write(csv_content)
            _logger.info(
                "CSV attribution report written",
                extra={"data": {"path": output_path}},
            )

        return csv_content

    def export_json(
        self,
        attribution_data: list[StrategyPnL]
        | list[ProtocolPnL]
        | list[ChainPnL]
        | list[PeriodPnL],
    ) -> str:
        """Export attribution data to JSON format.

        Args:
            attribution_data: List of attribution dataclass instances.

        Returns:
            JSON string representation of the attribution data.
        """
        if not attribution_data:
            return "[]"

        items: list[dict[str, Any]] = []
        for item in attribution_data:
            d = asdict(item)
            # Convert Decimal and datetime to serializable types
            for k, v in d.items():
                if isinstance(v, Decimal):
                    d[k] = str(v)
                elif isinstance(v, datetime):
                    d[k] = v.isoformat()
            items.append(d)

        return json.dumps(items, indent=2)
