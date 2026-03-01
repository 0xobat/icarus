"""Tax reporting engine for Canadian ACB (Adjusted Cost Base) method.

Processes all trades from the database to compute cost basis, realized
gains and losses, and DeFi-specific tax events (LP entry/exit, staking
rewards, yield farming, flash loan fees). Generates CSV reports with
full audit trail linking each tax event to its original transaction hash.

The ACB method:
  - For each asset, tracks total cost and total quantity held.
  - ACB per unit = total_cost / total_quantity.
  - On disposal: gain = proceeds - (ACB per unit * quantity disposed).
  - Gas fees are added to cost basis on acquisitions and subtracted
    from proceeds on disposals.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from db.repository import DatabaseRepository
from monitoring.logger import get_logger

_logger = get_logger("tax-engine", enable_file=False)

# Gas price constants
_WEI_PER_ETH = Decimal("1000000000000000000")
_DEFAULT_ETH_PRICE_USD = Decimal("2000")
_QUANTIZE_PRECISION = Decimal("0.000001")


def _to_decimal(value: Any) -> Decimal:
    """Convert a numeric value to Decimal with quantization.

    Ensures database floats don't carry precision artifacts by
    quantizing to 6 decimal places.

    Args:
        value: Numeric value to convert.

    Returns:
        Quantized Decimal.
    """
    return Decimal(str(value)).quantize(_QUANTIZE_PRECISION)

# DeFi action classifications
_ACQUISITION_ACTIONS = frozenset({
    "buy", "receive", "withdraw", "lp_exit", "stake_reward",
    "yield_reward", "harvest",
})
_DISPOSAL_ACTIONS = frozenset({
    "sell", "send", "supply", "lp_entry", "swap_out",
})
_SWAP_ACTIONS = frozenset({"swap"})
_INCOME_ACTIONS = frozenset({"stake_reward", "yield_reward", "harvest"})
_EXPENSE_ACTIONS = frozenset({"flash_loan_fee"})


@dataclass
class AssetCostBase:
    """Tracks ACB state for a single asset."""

    asset: str
    total_quantity: Decimal = Decimal("0")
    total_cost: Decimal = Decimal("0")

    @property
    def acb_per_unit(self) -> Decimal:
        """Compute adjusted cost base per unit.

        Returns:
            ACB per unit, or zero if no quantity held.
        """
        if self.total_quantity <= 0:
            return Decimal("0")
        return self.total_cost / self.total_quantity


@dataclass
class TaxEvent:
    """A single tax-relevant event with gain/loss calculation."""

    date: datetime
    event_type: str
    asset: str
    amount: Decimal
    proceeds_usd: Decimal
    cost_basis_usd: Decimal
    gain_loss_usd: Decimal
    tx_hash: str | None
    strategy: str
    protocol: str
    chain: str
    notes: str = ""


@dataclass
class TaxReport:
    """Aggregated tax report for a specific year."""

    year: int
    events: list[TaxEvent] = field(default_factory=list)
    total_gains: Decimal = Decimal("0")
    total_losses: Decimal = Decimal("0")
    net_gain_loss: Decimal = Decimal("0")
    total_income: Decimal = Decimal("0")
    total_expenses: Decimal = Decimal("0")


class TaxReportEngine:
    """Tax reporting engine using the Canadian ACB method.

    Processes trade data from ``DatabaseRepository`` to calculate cost
    basis, realized gains/losses, and DeFi-specific tax events. Supports
    CSV export with full audit trail.

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
        self._cost_bases: dict[str, AssetCostBase] = {}

    def _get_or_create_cost_base(self, asset: str) -> AssetCostBase:
        """Get or create cost base tracker for an asset.

        Args:
            asset: Asset symbol to track.

        Returns:
            The ``AssetCostBase`` instance for the given asset.
        """
        if asset not in self._cost_bases:
            self._cost_bases[asset] = AssetCostBase(asset=asset)
        return self._cost_bases[asset]

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

    def _classify_action(self, action: str) -> str:
        """Classify a trade action into a tax event type.

        Args:
            action: The trade action string.

        Returns:
            Tax event classification string.
        """
        if action in _ACQUISITION_ACTIONS:
            return "acquisition"
        if action in _DISPOSAL_ACTIONS:
            return "disposal"
        if action in _SWAP_ACTIONS:
            return "swap"
        if action in _INCOME_ACTIONS:
            return "income"
        if action in _EXPENSE_ACTIONS:
            return "expense"
        return "other"

    def process_trades(self, year: int | None = None) -> TaxReport:
        """Process all trades and compute tax events.

        Reads confirmed trades from the database, classifies them into
        tax events, and computes gains/losses using the ACB method.
        Optionally filters by tax year.

        Args:
            year: Tax year to filter trades. If None, processes all trades.

        Returns:
            A ``TaxReport`` with all computed tax events.
        """
        self._cost_bases.clear()

        trades = self._repo.get_trades(status="confirmed", limit=100000)
        # Sort chronologically (repo returns desc)
        trades = sorted(trades, key=lambda t: t.timestamp)

        report_year = year or datetime.now(UTC).year
        report = TaxReport(year=report_year)

        for trade in trades:
            events = self._process_single_trade(trade)
            for event in events:
                if year is not None:
                    # Filter to target year
                    if event.date.year != year:
                        continue

                report.events.append(event)

                if event.gain_loss_usd > 0:
                    report.total_gains += event.gain_loss_usd
                elif event.gain_loss_usd < 0:
                    report.total_losses += abs(event.gain_loss_usd)

                if event.event_type == "income":
                    report.total_income += event.proceeds_usd
                elif event.event_type == "expense":
                    report.total_expenses += event.proceeds_usd

        report.net_gain_loss = report.total_gains - report.total_losses

        _logger.info(
            "Tax report generated",
            extra={
                "data": {
                    "year": report_year,
                    "events": len(report.events),
                    "net_gain_loss": str(report.net_gain_loss),
                }
            },
        )
        return report

    def _process_single_trade(self, trade: Any) -> list[TaxEvent]:
        """Process a single trade into one or more tax events.

        Handles swaps as dual events (disposal + acquisition), DeFi
        income (staking/yield rewards), and standard buys/sells.

        Args:
            trade: A Trade ORM instance.

        Returns:
            List of ``TaxEvent`` instances generated from this trade.
        """
        events: list[TaxEvent] = []
        action = trade.action
        gas_cost = self._gas_cost_usd(trade)
        amount_in = _to_decimal(trade.amount_in) if trade.amount_in else Decimal("0")
        amount_out = _to_decimal(trade.amount_out) if trade.amount_out else Decimal("0")
        price = (
            _to_decimal(trade.price_at_execution)
            if trade.price_at_execution
            else Decimal("1")
        )

        if action == "swap":
            # Swap: disposal of asset_in, acquisition of asset_out
            # Disposal side
            disposal_event = self._create_disposal_event(
                trade=trade,
                asset=trade.asset_in,
                amount=amount_in,
                proceeds=amount_out * price,
                gas_cost=gas_cost,
                notes="Swap disposal",
            )
            events.append(disposal_event)

            # Acquisition side
            if trade.asset_out:
                acquisition_event = self._create_acquisition_event(
                    trade=trade,
                    asset=trade.asset_out,
                    amount=amount_out,
                    cost=amount_in * price,
                    gas_cost=Decimal("0"),  # Gas already counted on disposal side
                    notes="Swap acquisition",
                )
                events.append(acquisition_event)

        elif action in _INCOME_ACTIONS:
            # Income: staking rewards, yield farming
            income_value = amount_in * price
            # Add to cost base as acquisition at FMV
            cb = self._get_or_create_cost_base(trade.asset_in)
            cb.total_quantity += amount_in
            cb.total_cost += income_value

            events.append(TaxEvent(
                date=trade.timestamp,
                event_type="income",
                asset=trade.asset_in,
                amount=amount_in,
                proceeds_usd=income_value,
                cost_basis_usd=Decimal("0"),
                gain_loss_usd=Decimal("0"),
                tx_hash=trade.tx_hash,
                strategy=trade.strategy,
                protocol=trade.protocol,
                chain=trade.chain,
                notes=f"Income: {action}",
            ))

        elif action in _EXPENSE_ACTIONS:
            # Expense: flash loan fees
            expense_value = amount_in * price
            events.append(TaxEvent(
                date=trade.timestamp,
                event_type="expense",
                asset=trade.asset_in,
                amount=amount_in,
                proceeds_usd=expense_value,
                cost_basis_usd=Decimal("0"),
                gain_loss_usd=Decimal("0"),
                tx_hash=trade.tx_hash,
                strategy=trade.strategy,
                protocol=trade.protocol,
                chain=trade.chain,
                notes=f"Expense: {action}",
            ))

        elif action in _DISPOSAL_ACTIONS:
            disposal_event = self._create_disposal_event(
                trade=trade,
                asset=trade.asset_in,
                amount=amount_in,
                proceeds=amount_out * price if amount_out else amount_in * price,
                gas_cost=gas_cost,
                notes=f"Disposal: {action}",
            )
            events.append(disposal_event)

        elif action in _ACQUISITION_ACTIONS:
            cost = amount_in * price
            acquisition_event = self._create_acquisition_event(
                trade=trade,
                asset=trade.asset_in,
                amount=amount_in,
                cost=cost,
                gas_cost=gas_cost,
                notes=f"Acquisition: {action}",
            )
            events.append(acquisition_event)

        elif action == "lp_entry":
            # LP entry: disposal of both tokens
            disposal_event = self._create_disposal_event(
                trade=trade,
                asset=trade.asset_in,
                amount=amount_in,
                proceeds=amount_in * price,
                gas_cost=gas_cost,
                notes="LP entry disposal",
            )
            events.append(disposal_event)

        elif action == "lp_exit":
            # LP exit: acquisition of both tokens
            acquisition_event = self._create_acquisition_event(
                trade=trade,
                asset=trade.asset_in,
                amount=amount_in,
                cost=amount_in * price,
                gas_cost=gas_cost,
                notes="LP exit acquisition",
            )
            events.append(acquisition_event)

        else:
            # Unknown action — record as acquisition for safety
            cost = amount_in * price
            cb = self._get_or_create_cost_base(trade.asset_in)
            cb.total_quantity += amount_in
            cb.total_cost += cost + gas_cost

            events.append(TaxEvent(
                date=trade.timestamp,
                event_type="other",
                asset=trade.asset_in,
                amount=amount_in,
                proceeds_usd=Decimal("0"),
                cost_basis_usd=cost + gas_cost,
                gain_loss_usd=Decimal("0"),
                tx_hash=trade.tx_hash,
                strategy=trade.strategy,
                protocol=trade.protocol,
                chain=trade.chain,
                notes=f"Unclassified action: {action}",
            ))

        return events

    def _create_disposal_event(
        self,
        *,
        trade: Any,
        asset: str,
        amount: Decimal,
        proceeds: Decimal,
        gas_cost: Decimal,
        notes: str,
    ) -> TaxEvent:
        """Create a disposal tax event with ACB-based gain/loss calculation.

        Args:
            trade: The source Trade ORM instance.
            asset: Asset being disposed.
            amount: Quantity disposed.
            proceeds: Gross proceeds in USD.
            gas_cost: Gas cost to subtract from proceeds.
            notes: Description of the event.

        Returns:
            A ``TaxEvent`` with computed gain/loss.
        """
        cb = self._get_or_create_cost_base(asset)
        acb = cb.acb_per_unit
        cost_basis = acb * amount

        # Gas reduces proceeds on disposals
        net_proceeds = proceeds - gas_cost
        gain_loss = net_proceeds - cost_basis

        # Reduce holdings
        if cb.total_quantity >= amount:
            cb.total_cost -= cost_basis
            cb.total_quantity -= amount
        else:
            # Disposing more than held (shouldn't happen, but handle gracefully)
            cb.total_cost = Decimal("0")
            cb.total_quantity = Decimal("0")

        return TaxEvent(
            date=trade.timestamp,
            event_type="disposal",
            asset=asset,
            amount=amount,
            proceeds_usd=net_proceeds,
            cost_basis_usd=cost_basis,
            gain_loss_usd=gain_loss,
            tx_hash=trade.tx_hash,
            strategy=trade.strategy,
            protocol=trade.protocol,
            chain=trade.chain,
            notes=notes,
        )

    def _create_acquisition_event(
        self,
        *,
        trade: Any,
        asset: str,
        amount: Decimal,
        cost: Decimal,
        gas_cost: Decimal,
        notes: str,
    ) -> TaxEvent:
        """Create an acquisition tax event and update ACB.

        Gas fees are included in the cost basis for acquisitions.

        Args:
            trade: The source Trade ORM instance.
            asset: Asset being acquired.
            amount: Quantity acquired.
            cost: Acquisition cost in USD.
            gas_cost: Gas cost to add to cost basis.
            notes: Description of the event.

        Returns:
            A ``TaxEvent`` with zero gain/loss (acquisitions are non-taxable).
        """
        cb = self._get_or_create_cost_base(asset)
        total_cost = cost + gas_cost
        cb.total_quantity += amount
        cb.total_cost += total_cost

        return TaxEvent(
            date=trade.timestamp,
            event_type="acquisition",
            asset=asset,
            amount=amount,
            proceeds_usd=Decimal("0"),
            cost_basis_usd=total_cost,
            gain_loss_usd=Decimal("0"),
            tx_hash=trade.tx_hash,
            strategy=trade.strategy,
            protocol=trade.protocol,
            chain=trade.chain,
            notes=notes,
        )

    def get_cost_base(self, asset: str) -> AssetCostBase | None:
        """Return the current cost base for an asset.

        Args:
            asset: Asset symbol to look up.

        Returns:
            The ``AssetCostBase`` for the asset, or None if not tracked.
        """
        return self._cost_bases.get(asset)

    def generate_csv_report(
        self,
        year: int,
        output_path: str | None = None,
    ) -> str:
        """Generate a CSV tax report for a specific year.

        Processes all trades, filters to the target year, and writes
        a CSV with columns: date, type, asset, amount, proceeds_usd,
        cost_basis_usd, gain_loss_usd, tx_hash.

        Args:
            year: Tax year to report on.
            output_path: Optional file path to write the CSV. If None,
                returns the CSV content as a string.

        Returns:
            The CSV content as a string.
        """
        report = self.process_trades(year=year)

        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")

        # Header
        writer.writerow([
            "date", "type", "asset", "amount", "proceeds_usd",
            "cost_basis_usd", "gain_loss_usd", "tx_hash",
            "strategy", "protocol", "chain", "notes",
        ])

        # Data rows
        for event in report.events:
            writer.writerow([
                event.date.isoformat(),
                event.event_type,
                event.asset,
                str(event.amount),
                str(event.proceeds_usd),
                str(event.cost_basis_usd),
                str(event.gain_loss_usd),
                event.tx_hash or "",
                event.strategy,
                event.protocol,
                event.chain,
                event.notes,
            ])

        # Summary
        writer.writerow([])
        writer.writerow(["Summary"])
        writer.writerow(["Total Gains", str(report.total_gains)])
        writer.writerow(["Total Losses", str(report.total_losses)])
        writer.writerow(["Net Gain/Loss", str(report.net_gain_loss)])
        writer.writerow(["Total Income", str(report.total_income)])
        writer.writerow(["Total Expenses", str(report.total_expenses)])

        csv_content = output.getvalue()

        if output_path is not None:
            with open(output_path, "w", newline="") as f:
                f.write(csv_content)
            _logger.info(
                "CSV tax report written",
                extra={"data": {"path": output_path, "year": year}},
            )

        return csv_content

    def get_audit_trail(self, tx_hash: str) -> list[TaxEvent]:
        """Get all tax events linked to a specific transaction hash.

        Args:
            tx_hash: The transaction hash to search for.

        Returns:
            List of ``TaxEvent`` instances linked to the transaction.
        """
        # Process all trades first
        report = self.process_trades()
        return [e for e in report.events if e.tx_hash == tx_hash]
