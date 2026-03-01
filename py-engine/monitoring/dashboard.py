"""Performance dashboard for real-time portfolio monitoring.

Provides a ``PerformanceDashboard`` that reads from ``DatabaseRepository`` to
compute portfolio summaries, Sharpe ratios, strategy attribution, gas cost
analysis, and drawdown tracking. All financial calculations use ``Decimal``
for precision. Computed metrics are stored in the ``StrategyPerformance``
table for historical tracking.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from db.repository import DatabaseRepository
from monitoring.logger import get_logger

_logger = get_logger("dashboard", enable_file=False)

# Gas price constants (wei -> ETH -> USD)
_WEI_PER_ETH = Decimal("1000000000000000000")
_DEFAULT_ETH_PRICE_USD = Decimal("2000")


@dataclass
class PortfolioSummary:
    """Current portfolio state including value, P&L, and APY."""

    total_value_usd: Decimal
    cumulative_pnl_usd: Decimal
    annualized_return_pct: Decimal
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class StrategyAttribution:
    """P&L contribution from a single strategy."""

    strategy: str
    pnl_usd: Decimal
    return_pct: Decimal
    trade_count: int
    gas_cost_usd: Decimal
    contribution_pct: Decimal


@dataclass
class GasSummary:
    """Gas cost analysis across all strategies."""

    total_gas_cost_usd: Decimal
    gas_per_strategy: dict[str, Decimal]
    gas_as_pct_of_returns: Decimal


@dataclass
class DrawdownInfo:
    """Current and historical worst drawdown from peak."""

    current_drawdown_pct: Decimal
    peak_value_usd: Decimal
    current_value_usd: Decimal
    worst_drawdown_pct: Decimal
    worst_drawdown_timestamp: datetime | None


class PerformanceDashboard:
    """Performance dashboard that computes metrics from database records.

    Reads portfolio snapshots, trades, and strategy performance from the
    ``DatabaseRepository``. All computations use ``Decimal`` for precision.
    Supports Sharpe ratio over configurable rolling windows, strategy
    attribution, gas cost tracking, and drawdown analysis.

    Args:
        repository: The database repository for all data access.
        eth_price_usd: ETH price in USD for gas cost calculations.
    """

    def __init__(
        self,
        repository: DatabaseRepository,
        eth_price_usd: Decimal | None = None,
    ) -> None:
        self._repo = repository
        self._eth_price_usd = eth_price_usd or _DEFAULT_ETH_PRICE_USD
        self._cached_summary: PortfolioSummary | None = None
        self._cached_attribution: list[StrategyAttribution] | None = None
        self._cached_gas: GasSummary | None = None
        self._cached_drawdown: DrawdownInfo | None = None

    def get_portfolio_summary(self) -> PortfolioSummary:
        """Compute current portfolio value, cumulative P&L, and APY.

        Reads the latest and earliest portfolio snapshots to calculate
        cumulative P&L and annualized return. If no snapshots exist,
        returns a zeroed summary.

        Returns:
            A ``PortfolioSummary`` with current value, P&L, and APY.
        """
        if self._cached_summary is not None:
            return self._cached_summary

        latest = self._repo.get_latest_snapshot()
        if latest is None:
            return PortfolioSummary(
                total_value_usd=Decimal("0"),
                cumulative_pnl_usd=Decimal("0"),
                annualized_return_pct=Decimal("0"),
            )

        # Get all snapshots to find the first one
        snapshots = self._repo.get_snapshots(limit=10000)
        if not snapshots:
            return PortfolioSummary(
                total_value_usd=Decimal("0"),
                cumulative_pnl_usd=Decimal("0"),
                annualized_return_pct=Decimal("0"),
            )

        # Earliest snapshot is the last in desc-ordered list
        earliest = snapshots[-1]
        current_value = Decimal(str(latest.total_value_usd))
        initial_value = Decimal(str(earliest.total_value_usd))

        cumulative_pnl = current_value - initial_value

        # Calculate APY
        apy = self._calculate_apy(
            initial_value, current_value, earliest.timestamp, latest.timestamp
        )

        summary = PortfolioSummary(
            total_value_usd=current_value,
            cumulative_pnl_usd=cumulative_pnl,
            annualized_return_pct=apy,
            timestamp=latest.timestamp,
        )
        self._cached_summary = summary
        return summary

    def get_sharpe_ratio(self, window: str = "30d") -> Decimal:
        """Calculate annualized Sharpe ratio over a rolling window.

        Uses daily returns from portfolio snapshots. The risk-free rate
        is assumed to be zero for simplicity.

        Args:
            window: Rolling window period. Supported values: ``"7d"``,
                ``"30d"``, ``"all"``.

        Returns:
            The annualized Sharpe ratio as a ``Decimal``. Returns zero
            if insufficient data.
        """
        window_days = self._parse_window(window)

        if window_days is not None:
            since = datetime.now(UTC) - timedelta(days=window_days)
            snapshots = self._repo.get_snapshots(since=since, limit=10000)
        else:
            snapshots = self._repo.get_snapshots(limit=10000)

        if len(snapshots) < 2:
            return Decimal("0")

        # Snapshots are desc-ordered, reverse for chronological order
        snapshots = list(reversed(snapshots))

        # Calculate daily returns
        returns: list[float] = []
        for i in range(1, len(snapshots)):
            prev_val = float(snapshots[i - 1].total_value_usd)
            curr_val = float(snapshots[i].total_value_usd)
            if prev_val > 0:
                daily_return = (curr_val - prev_val) / prev_val
                returns.append(daily_return)

        if not returns:
            return Decimal("0")

        mean_return = sum(returns) / len(returns)
        if len(returns) < 2:
            return Decimal("0")

        variance = sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)
        std_dev = math.sqrt(variance)

        if std_dev == 0:
            return Decimal("0")

        # Annualize: Sharpe = (mean_daily / std_daily) * sqrt(365)
        sharpe = (mean_return / std_dev) * math.sqrt(365)
        return Decimal(str(round(sharpe, 6)))

    def get_strategy_attribution(self) -> list[StrategyAttribution]:
        """Compute P&L attribution per strategy.

        Aggregates trade data by strategy to determine each strategy's
        contribution to total portfolio returns.

        Returns:
            List of ``StrategyAttribution`` entries, one per strategy.
        """
        if self._cached_attribution is not None:
            return self._cached_attribution

        trades = self._repo.get_trades(status="confirmed", limit=10000)

        # Group by strategy
        strategy_data: dict[str, dict[str, Any]] = {}
        for trade in trades:
            strat = trade.strategy
            if strat not in strategy_data:
                strategy_data[strat] = {
                    "pnl_usd": Decimal("0"),
                    "gas_cost_usd": Decimal("0"),
                    "trade_count": 0,
                    "total_in": Decimal("0"),
                    "total_out": Decimal("0"),
                }

            sd = strategy_data[strat]
            sd["trade_count"] += 1

            amount_in = Decimal(str(trade.amount_in)) if trade.amount_in else Decimal("0")
            amount_out = Decimal(str(trade.amount_out)) if trade.amount_out else Decimal("0")
            sd["total_in"] += amount_in
            sd["total_out"] += amount_out

            # Calculate gas cost in USD
            if trade.gas_used is not None and trade.gas_price_wei is not None:
                gas_cost_eth = (
                    Decimal(str(trade.gas_used))
                    * Decimal(str(trade.gas_price_wei))
                    / _WEI_PER_ETH
                )
                gas_cost_usd = gas_cost_eth * self._eth_price_usd
                sd["gas_cost_usd"] += gas_cost_usd

        # Calculate P&L per strategy
        total_pnl = Decimal("0")
        for strat_name, sd in strategy_data.items():
            sd["pnl_usd"] = sd["total_out"] - sd["total_in"]
            total_pnl += sd["pnl_usd"]

        # Build attribution list
        attributions: list[StrategyAttribution] = []
        for strat_name, sd in strategy_data.items():
            return_pct = Decimal("0")
            if sd["total_in"] > 0:
                return_pct = (sd["pnl_usd"] / sd["total_in"]) * Decimal("100")

            contribution_pct = Decimal("0")
            if total_pnl != 0:
                contribution_pct = (sd["pnl_usd"] / abs(total_pnl)) * Decimal("100")

            attributions.append(
                StrategyAttribution(
                    strategy=strat_name,
                    pnl_usd=sd["pnl_usd"],
                    return_pct=return_pct,
                    trade_count=sd["trade_count"],
                    gas_cost_usd=sd["gas_cost_usd"],
                    contribution_pct=contribution_pct,
                )
            )

        self._cached_attribution = attributions
        return attributions

    def get_gas_summary(self) -> GasSummary:
        """Compute gas cost analysis across all strategies.

        Aggregates gas costs from all confirmed trades and calculates
        gas as a percentage of total returns.

        Returns:
            A ``GasSummary`` with total gas, per-strategy gas, and
            gas as percentage of returns.
        """
        if self._cached_gas is not None:
            return self._cached_gas

        trades = self._repo.get_trades(status="confirmed", limit=10000)

        total_gas_cost = Decimal("0")
        gas_per_strategy: dict[str, Decimal] = {}

        for trade in trades:
            if trade.gas_used is not None and trade.gas_price_wei is not None:
                gas_cost_eth = (
                    Decimal(str(trade.gas_used))
                    * Decimal(str(trade.gas_price_wei))
                    / _WEI_PER_ETH
                )
                gas_cost_usd = gas_cost_eth * self._eth_price_usd
                total_gas_cost += gas_cost_usd

                strat = trade.strategy
                if strat not in gas_per_strategy:
                    gas_per_strategy[strat] = Decimal("0")
                gas_per_strategy[strat] += gas_cost_usd

        # Calculate gas as % of returns
        total_returns = Decimal("0")
        for trade in trades:
            amount_out = Decimal(str(trade.amount_out)) if trade.amount_out else Decimal("0")
            amount_in = Decimal(str(trade.amount_in)) if trade.amount_in else Decimal("0")
            total_returns += amount_out - amount_in

        gas_pct = Decimal("0")
        if total_returns > 0:
            gas_pct = (total_gas_cost / total_returns) * Decimal("100")

        summary = GasSummary(
            total_gas_cost_usd=total_gas_cost,
            gas_per_strategy=gas_per_strategy,
            gas_as_pct_of_returns=gas_pct,
        )
        self._cached_gas = summary
        return summary

    def get_drawdown_info(self) -> DrawdownInfo:
        """Compute current and historical worst drawdown.

        Reads all portfolio snapshots to find the worst peak-to-trough
        drawdown. Current drawdown is taken from the latest snapshot.

        Returns:
            A ``DrawdownInfo`` with current and worst-ever drawdown.
        """
        if self._cached_drawdown is not None:
            return self._cached_drawdown

        snapshots = self._repo.get_snapshots(limit=10000)

        if not snapshots:
            return DrawdownInfo(
                current_drawdown_pct=Decimal("0"),
                peak_value_usd=Decimal("0"),
                current_value_usd=Decimal("0"),
                worst_drawdown_pct=Decimal("0"),
                worst_drawdown_timestamp=None,
            )

        latest = snapshots[0]
        current_dd = Decimal(str(latest.drawdown_from_peak))
        current_value = Decimal(str(latest.total_value_usd))
        peak_value = Decimal(str(latest.peak_value_usd))

        # Find worst historical drawdown
        worst_dd = Decimal("0")
        worst_dd_timestamp: datetime | None = None

        for snap in snapshots:
            dd = Decimal(str(snap.drawdown_from_peak))
            if dd > worst_dd:
                worst_dd = dd
                worst_dd_timestamp = snap.timestamp

        info = DrawdownInfo(
            current_drawdown_pct=current_dd,
            peak_value_usd=peak_value,
            current_value_usd=current_value,
            worst_drawdown_pct=worst_dd,
            worst_drawdown_timestamp=worst_dd_timestamp,
        )
        self._cached_drawdown = info
        return info

    def refresh_metrics(self) -> None:
        """Recompute all metrics from the database and update caches.

        Clears all cached results and recomputes portfolio summary,
        strategy attribution, gas summary, and drawdown info. Also
        stores computed strategy performance in the database.
        """
        self._cached_summary = None
        self._cached_attribution = None
        self._cached_gas = None
        self._cached_drawdown = None

        # Recompute all metrics
        summary = self.get_portfolio_summary()
        attributions = self.get_strategy_attribution()
        gas = self.get_gas_summary()
        drawdown = self.get_drawdown_info()

        # Persist strategy performance to database
        for attr in attributions:
            try:
                self._repo.record_strategy_performance({
                    "strategy": attr.strategy,
                    "period": "snapshot",
                    "pnl_usd": str(attr.pnl_usd),
                    "return_pct": str(attr.return_pct),
                    "gas_cost_usd": str(attr.gas_cost_usd),
                    "trade_count": attr.trade_count,
                })
            except Exception:
                _logger.exception(
                    "Failed to persist strategy performance",
                    extra={"data": {"strategy": attr.strategy}},
                )

        _logger.info(
            "Metrics refreshed",
            extra={
                "data": {
                    "total_value_usd": str(summary.total_value_usd),
                    "strategies": len(attributions),
                    "total_gas_usd": str(gas.total_gas_cost_usd),
                    "current_drawdown": str(drawdown.current_drawdown_pct),
                }
            },
        )

    def _calculate_apy(
        self,
        initial_value: Decimal,
        current_value: Decimal,
        start_time: datetime,
        end_time: datetime,
    ) -> Decimal:
        """Calculate annualized percentage yield.

        Args:
            initial_value: Starting portfolio value.
            current_value: Current portfolio value.
            start_time: When tracking began.
            end_time: Current time.

        Returns:
            Annualized return as a percentage ``Decimal``.
        """
        if initial_value <= 0:
            return Decimal("0")

        elapsed = end_time - start_time
        days_elapsed = max(Decimal(str(elapsed.total_seconds())) / Decimal("86400"), Decimal("1"))

        total_return = (current_value - initial_value) / initial_value

        try:
            # APY = ((1 + total_return) ^ (365 / days)) - 1
            base = float(Decimal("1") + total_return)
            exponent = float(Decimal("365") / days_elapsed)
            if base <= 0:
                return Decimal("0")
            apy = Decimal(str(base ** exponent - 1)) * Decimal("100")
            return apy.quantize(Decimal("0.000001"))
        except (OverflowError, InvalidOperation):
            return Decimal("0")

    @staticmethod
    def _parse_window(window: str) -> int | None:
        """Parse a window string like '7d' or '30d' into days.

        Args:
            window: Window string. ``"all"`` returns None (no filter).

        Returns:
            Number of days, or None for all-time.
        """
        if window == "all":
            return None
        if window.endswith("d"):
            try:
                return int(window[:-1])
            except ValueError:
                return 30
        return 30
