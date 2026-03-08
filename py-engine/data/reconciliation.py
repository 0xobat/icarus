"""On-chain position reconciliation — compare on-chain state against agent records."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

SERVICE_NAME = "py-engine"

# Default schedule interval
DEFAULT_RECONCILE_INTERVAL_SECONDS = int(
    os.environ.get("RECONCILE_INTERVAL_SECONDS", "1800")
)

# Balance comparison tolerance (0.01% to account for rounding)
BALANCE_TOLERANCE = 0.0001


def _log(event: str, message: str, **kwargs: Any) -> None:
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "service": SERVICE_NAME,
        "event": event,
        "message": message,
        **kwargs,
    }
    print(json.dumps(entry), flush=True)


# ── Data models ──────────────────────────────────────


class DiscrepancyType(StrEnum):
    """Enumeration of reconciliation discrepancy types."""

    MISSING_POSITION = "missing_position"
    UNEXPECTED_BALANCE = "unexpected_balance"
    UNRECORDED_TX = "unrecorded_tx"
    BALANCE_MISMATCH = "balance_mismatch"


class Severity(StrEnum):
    """Enumeration of discrepancy severity levels."""

    AUTO_FIXABLE = "auto_fixable"
    MANUAL_REVIEW = "manual_review"


@dataclass
class TokenBalance:
    """A token balance entry."""

    token: str
    balance: float


@dataclass
class AaveDeposit:
    """An Aave deposit position."""

    token: str
    deposited: float
    accrued_interest: float = 0.0


@dataclass
class LPPosition:
    """A liquidity pool position."""

    pool: str
    token0_amount: float
    token1_amount: float
    liquidity: float = 0.0


@dataclass
class OnChainState:
    """Snapshot of on-chain positions for a wallet."""

    wallet_address: str
    token_balances: list[TokenBalance] = field(default_factory=list)
    aave_deposits: list[AaveDeposit] = field(default_factory=list)
    lp_positions: list[LPPosition] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()


@dataclass
class AgentState:
    """Agent's internal record of positions (from agent-state.json)."""

    wallet_address: str
    token_balances: list[TokenBalance] = field(default_factory=list)
    aave_deposits: list[AaveDeposit] = field(default_factory=list)
    lp_positions: list[LPPosition] = field(default_factory=list)
    pending_txs: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Discrepancy:
    """A detected discrepancy between on-chain and agent state."""

    type: DiscrepancyType
    severity: Severity
    token: str
    details: str
    on_chain_value: float | None = None
    agent_value: float | None = None
    resolved: bool = False
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        """Return dictionary representation."""
        d = asdict(self)
        d["type"] = self.type.value
        d["severity"] = self.severity.value
        return d


# ── Reconciler ───────────────────────────────────────


class Reconciler:
    """Compares on-chain state against agent records and detects discrepancies."""

    def __init__(
        self,
        *,
        interval_seconds: int = DEFAULT_RECONCILE_INTERVAL_SECONDS,
        balance_tolerance: float = BALANCE_TOLERANCE,
        fetch_on_chain_fn: Any = None,
    ) -> None:
        self._interval = interval_seconds
        self._tolerance = balance_tolerance
        self._fetch_on_chain_fn = fetch_on_chain_fn

    @property
    def interval_seconds(self) -> int:
        """Return the reconciliation interval in seconds."""
        return self._interval

    def _balances_match(self, a: float, b: float) -> bool:
        """Compare two balances within tolerance."""
        if a == 0 and b == 0:
            return True
        denom = max(abs(a), abs(b))
        if denom == 0:
            return True
        return abs(a - b) / denom <= self._tolerance

    def fetch_on_chain_state(self, wallet_address: str) -> OnChainState:
        """Fetch current on-chain state. Uses injected function or raises."""
        if self._fetch_on_chain_fn is None:
            raise RuntimeError("No on-chain fetch function configured")
        return self._fetch_on_chain_fn(wallet_address)

    def reconcile(
        self, on_chain: OnChainState, agent_state: AgentState
    ) -> list[Discrepancy]:
        """Compare on-chain state against agent records. Returns all discrepancies."""
        discrepancies: list[Discrepancy] = []

        discrepancies.extend(self._reconcile_token_balances(on_chain, agent_state))
        discrepancies.extend(self._reconcile_aave_deposits(on_chain, agent_state))
        discrepancies.extend(self._reconcile_lp_positions(on_chain, agent_state))

        # Log all discrepancies
        for d in discrepancies:
            _log(
                "reconciliation_discrepancy",
                d.details,
                discrepancy_type=d.type.value,
                severity=d.severity.value,
                token=d.token,
                on_chain_value=d.on_chain_value,
                agent_value=d.agent_value,
            )

        if discrepancies:
            _log(
                "reconciliation_alert",
                f"Found {len(discrepancies)} discrepancies",
                total=len(discrepancies),
                auto_fixable=sum(
                    1 for d in discrepancies if d.severity == Severity.AUTO_FIXABLE
                ),
                manual_review=sum(
                    1 for d in discrepancies if d.severity == Severity.MANUAL_REVIEW
                ),
            )
        else:
            _log("reconciliation_ok", "On-chain state matches agent records")

        return discrepancies

    def _reconcile_token_balances(
        self, on_chain: OnChainState, agent_state: AgentState
    ) -> list[Discrepancy]:
        discrepancies: list[Discrepancy] = []

        chain_map = {b.token: b.balance for b in on_chain.token_balances}
        agent_map = {b.token: b.balance for b in agent_state.token_balances}

        # Check all on-chain tokens
        for token, chain_bal in chain_map.items():
            agent_bal = agent_map.get(token)
            if agent_bal is None:
                # On-chain balance exists but agent doesn't know about it
                if chain_bal > 0:
                    # Check if this might be from a pending TX
                    is_pending = any(
                        tx.get("token") == token for tx in agent_state.pending_txs
                    )
                    discrepancies.append(Discrepancy(
                        type=DiscrepancyType.UNEXPECTED_BALANCE,
                        severity=(
                            Severity.AUTO_FIXABLE if is_pending else Severity.MANUAL_REVIEW
                        ),
                        token=token,
                        details=(
                            f"Unexpected on-chain balance for {token}: "
                            f"{chain_bal} (not tracked by agent)"
                        ),
                        on_chain_value=chain_bal,
                        agent_value=0.0,
                    ))
            elif not self._balances_match(chain_bal, agent_bal):
                # Both exist but don't match
                is_pending = any(
                    tx.get("token") == token for tx in agent_state.pending_txs
                )
                discrepancies.append(Discrepancy(
                    type=DiscrepancyType.BALANCE_MISMATCH,
                    severity=(
                        Severity.AUTO_FIXABLE if is_pending else Severity.MANUAL_REVIEW
                    ),
                    token=token,
                    details=(
                        f"Balance mismatch for {token}: "
                        f"on-chain={chain_bal}, agent={agent_bal}"
                    ),
                    on_chain_value=chain_bal,
                    agent_value=agent_bal,
                ))

        # Check for positions agent thinks exist but don't on-chain
        for token, agent_bal in agent_map.items():
            if token not in chain_map and agent_bal > 0:
                discrepancies.append(Discrepancy(
                    type=DiscrepancyType.MISSING_POSITION,
                    severity=Severity.MANUAL_REVIEW,
                    token=token,
                    details=(
                        f"Agent records {token} balance of {agent_bal} "
                        f"but not found on-chain"
                    ),
                    on_chain_value=0.0,
                    agent_value=agent_bal,
                ))

        return discrepancies

    def _reconcile_aave_deposits(
        self, on_chain: OnChainState, agent_state: AgentState
    ) -> list[Discrepancy]:
        discrepancies: list[Discrepancy] = []

        chain_map = {d.token: d.deposited for d in on_chain.aave_deposits}
        agent_map = {d.token: d.deposited for d in agent_state.aave_deposits}

        for token, chain_dep in chain_map.items():
            agent_dep = agent_map.get(token)
            if agent_dep is None and chain_dep > 0:
                discrepancies.append(Discrepancy(
                    type=DiscrepancyType.UNEXPECTED_BALANCE,
                    severity=Severity.MANUAL_REVIEW,
                    token=f"aave:{token}",
                    details=(
                        f"Unexpected Aave deposit for {token}: "
                        f"{chain_dep} (not tracked)"
                    ),
                    on_chain_value=chain_dep,
                    agent_value=0.0,
                ))
            elif agent_dep is not None and not self._balances_match(chain_dep, agent_dep):
                # Interest accrual is auto-fixable
                discrepancies.append(Discrepancy(
                    type=DiscrepancyType.BALANCE_MISMATCH,
                    severity=Severity.AUTO_FIXABLE,
                    token=f"aave:{token}",
                    details=(
                        f"Aave deposit mismatch for {token}: "
                        f"on-chain={chain_dep}, agent={agent_dep}"
                    ),
                    on_chain_value=chain_dep,
                    agent_value=agent_dep,
                ))

        for token, agent_dep in agent_map.items():
            if token not in chain_map and agent_dep > 0:
                discrepancies.append(Discrepancy(
                    type=DiscrepancyType.MISSING_POSITION,
                    severity=Severity.MANUAL_REVIEW,
                    token=f"aave:{token}",
                    details=(
                        f"Agent records Aave deposit for {token} of {agent_dep} "
                        f"but not found on-chain"
                    ),
                    on_chain_value=0.0,
                    agent_value=agent_dep,
                ))

        return discrepancies

    def _reconcile_lp_positions(
        self, on_chain: OnChainState, agent_state: AgentState
    ) -> list[Discrepancy]:
        discrepancies: list[Discrepancy] = []

        chain_map = {p.pool: p for p in on_chain.lp_positions}
        agent_map = {p.pool: p for p in agent_state.lp_positions}

        for pool, chain_pos in chain_map.items():
            if pool not in agent_map and chain_pos.liquidity > 0:
                discrepancies.append(Discrepancy(
                    type=DiscrepancyType.UNEXPECTED_BALANCE,
                    severity=Severity.MANUAL_REVIEW,
                    token=f"lp:{pool}",
                    details=f"Unexpected LP position in {pool} (not tracked)",
                    on_chain_value=chain_pos.liquidity,
                    agent_value=0.0,
                ))

        for pool, agent_pos in agent_map.items():
            if pool not in chain_map and agent_pos.liquidity > 0:
                discrepancies.append(Discrepancy(
                    type=DiscrepancyType.MISSING_POSITION,
                    severity=Severity.MANUAL_REVIEW,
                    token=f"lp:{pool}",
                    details=f"Agent records LP in {pool} but not found on-chain",
                    on_chain_value=0.0,
                    agent_value=agent_pos.liquidity,
                ))

        return discrepancies

    # ── Auto-reconciliation ──────────────────────────────

    def auto_reconcile(
        self, discrepancies: list[Discrepancy]
    ) -> list[Discrepancy]:
        """Attempt to auto-fix simple discrepancies.

        Returns the list of remaining unresolved discrepancies.
        Auto-fixable cases: pending TXs confirmed, interest accrual drift.
        """
        remaining: list[Discrepancy] = []

        for d in discrepancies:
            if d.severity == Severity.AUTO_FIXABLE:
                _log(
                    "auto_reconcile",
                    f"Auto-resolving: {d.details}",
                    discrepancy_type=d.type.value,
                    token=d.token,
                )
                d.resolved = True
            else:
                _log(
                    "manual_review_required",
                    f"Requires manual review: {d.details}",
                    discrepancy_type=d.type.value,
                    token=d.token,
                    severity=d.severity.value,
                )
                remaining.append(d)

        return remaining


# ── Position Reconciler (DATA-004) ───────────────────


class OnChainProvider(Protocol):
    """Protocol for on-chain balance providers (mockable for tests)."""

    def get_token_balances(self, wallet_address: str) -> list[dict[str, Any]]:
        """Return ERC-20 token balances for a wallet.

        Each dict has keys: token_symbol, balance (float), contract_address,
        protocol (str, e.g. "wallet", "aave_v3", "aerodrome").
        """
        ...


@dataclass
class OnChainBalance:
    """A single on-chain balance entry."""

    token_symbol: str
    balance: float
    contract_address: str
    protocol: str  # "wallet", "aave_v3", "aerodrome"


@dataclass
class PositionDiscrepancy:
    """A discrepancy between on-chain and database position state."""

    position_id: str | None
    expected_value: float
    actual_value: float
    asset: str
    protocol: str
    discrepancy_type: str  # "missing_onchain", "missing_db", "value_mismatch"
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        """Return dictionary representation."""
        return asdict(self)


@dataclass
class ReconciliationResult:
    """Summary of a reconciliation run."""

    discrepancies_found: int
    positions_closed: int
    positions_created: int
    positions_updated: int
    success: bool
    discrepancies: list[PositionDiscrepancy] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()


class PositionReconciler:
    """Reconciles on-chain positions against PostgreSQL records.

    Trusts on-chain state as the source of truth. Used during startup
    recovery to ensure the database accurately reflects actual positions.

    Args:
        provider: An on-chain balance provider (Alchemy or mock).
        balance_tolerance: Relative tolerance for balance comparison (default 0.01%).
    """

    def __init__(
        self,
        provider: OnChainProvider | None = None,
        *,
        balance_tolerance: float = BALANCE_TOLERANCE,
    ) -> None:
        self._provider = provider
        self._tolerance = balance_tolerance

    def _balances_match(self, a: float, b: float) -> bool:
        """Compare two balances within tolerance."""
        if a == 0.0 and b == 0.0:
            return True
        denom = max(abs(a), abs(b))
        if denom == 0.0:
            return True
        return abs(a - b) / denom <= self._tolerance

    def query_onchain_balances(
        self, wallet_address: str, *, provider: OnChainProvider | None = None
    ) -> dict[str, OnChainBalance]:
        """Query on-chain balances via Alchemy Enhanced APIs.

        Args:
            wallet_address: The wallet address to query.
            provider: Optional override provider (for testing).

        Returns:
            Dict keyed by ``protocol:asset`` with OnChainBalance values.

        Raises:
            RuntimeError: If no provider is configured.
        """
        p = provider or self._provider
        if p is None:
            raise RuntimeError("No on-chain provider configured")

        raw_balances = p.get_token_balances(wallet_address)
        result: dict[str, OnChainBalance] = {}

        for entry in raw_balances:
            balance = OnChainBalance(
                token_symbol=entry["token_symbol"],
                balance=entry["balance"],
                contract_address=entry.get("contract_address", ""),
                protocol=entry.get("protocol", "wallet"),
            )
            key = f"{balance.protocol}:{balance.token_symbol}"
            result[key] = balance

        return result

    def compare_positions(
        self,
        onchain: dict[str, OnChainBalance],
        db_positions: list[dict[str, Any]],
    ) -> list[PositionDiscrepancy]:
        """Compare on-chain balances against database position records.

        Args:
            onchain: On-chain balances keyed by ``protocol:asset``.
            db_positions: List of position dicts from the repository.

        Returns:
            List of all discrepancies found.
        """
        discrepancies: list[PositionDiscrepancy] = []

        # Build lookup from DB positions by protocol:asset
        db_map: dict[str, dict[str, Any]] = {}
        for pos in db_positions:
            if pos.get("status") != "open":
                continue
            key = f"{pos['protocol']}:{pos['asset']}"
            db_map[key] = pos

        # Check on-chain balances against DB
        for key, chain_bal in onchain.items():
            if chain_bal.balance <= 0:
                continue
            db_pos = db_map.get(key)
            if db_pos is None:
                # On-chain balance exists but no DB record
                discrepancies.append(PositionDiscrepancy(
                    position_id=None,
                    expected_value=0.0,
                    actual_value=chain_bal.balance,
                    asset=chain_bal.token_symbol,
                    protocol=chain_bal.protocol,
                    discrepancy_type="missing_db",
                ))
            elif not self._balances_match(
                chain_bal.balance, float(db_pos["amount"])
            ):
                discrepancies.append(PositionDiscrepancy(
                    position_id=db_pos["position_id"],
                    expected_value=float(db_pos["amount"]),
                    actual_value=chain_bal.balance,
                    asset=chain_bal.token_symbol,
                    protocol=chain_bal.protocol,
                    discrepancy_type="value_mismatch",
                ))

        # Check DB positions that have no on-chain balance
        for key, db_pos in db_map.items():
            if key not in onchain or onchain[key].balance <= 0:
                discrepancies.append(PositionDiscrepancy(
                    position_id=db_pos["position_id"],
                    expected_value=float(db_pos["amount"]),
                    actual_value=0.0,
                    asset=db_pos["asset"],
                    protocol=db_pos["protocol"],
                    discrepancy_type="missing_onchain",
                ))

        return discrepancies

    def reconcile(
        self,
        discrepancies: list[PositionDiscrepancy],
        repository: Any,
    ) -> ReconciliationResult:
        """Trust on-chain state and update database to match.

        Args:
            discrepancies: List of detected discrepancies.
            repository: DatabaseRepository instance with save_position().

        Returns:
            ReconciliationResult summarizing all actions taken.
        """
        closed = 0
        created = 0
        updated = 0

        for d in discrepancies:
            _log(
                "position_reconciliation_discrepancy",
                f"Discrepancy: {d.discrepancy_type} for {d.protocol}:{d.asset}",
                discrepancy_type=d.discrepancy_type,
                position_id=d.position_id,
                expected_value=d.expected_value,
                actual_value=d.actual_value,
                asset=d.asset,
                protocol=d.protocol,
            )

            if d.discrepancy_type == "missing_onchain":
                # Position exists in DB but not on-chain — close it
                repository.save_position({
                    "position_id": d.position_id,
                    "strategy": "unknown",
                    "protocol": d.protocol,
                    "chain": "base",
                    "asset": d.asset,
                    "entry_price": 0,
                    "amount": 0,
                    "current_value": 0,
                    "status": "closed",
                    "close_time": datetime.now(UTC),
                })
                closed += 1
                _log(
                    "position_reconciliation_action",
                    f"Closed DB position {d.position_id} (not found on-chain)",
                    action="close",
                    position_id=d.position_id,
                )

            elif d.discrepancy_type == "missing_db":
                # On-chain balance exists but no DB record — create one
                new_id = f"reconciled-{uuid.uuid4().hex[:12]}"
                repository.save_position({
                    "position_id": new_id,
                    "strategy": "unknown",
                    "protocol": d.protocol,
                    "chain": "base",
                    "asset": d.asset,
                    "entry_price": 1.0,
                    "amount": d.actual_value,
                    "current_value": d.actual_value,
                    "status": "open",
                })
                created += 1
                _log(
                    "position_reconciliation_action",
                    f"Created DB position {new_id} from on-chain balance",
                    action="create",
                    position_id=new_id,
                    amount=d.actual_value,
                )

            elif d.discrepancy_type == "value_mismatch":
                # Both exist but amounts differ — update DB to match on-chain
                repository.save_position({
                    "position_id": d.position_id,
                    "strategy": "unknown",
                    "protocol": d.protocol,
                    "chain": "base",
                    "asset": d.asset,
                    "entry_price": 0,
                    "amount": d.actual_value,
                    "current_value": d.actual_value,
                    "status": "open",
                })
                updated += 1
                _log(
                    "position_reconciliation_action",
                    f"Updated position {d.position_id}: "
                    f"{d.expected_value} → {d.actual_value}",
                    action="update",
                    position_id=d.position_id,
                    old_amount=d.expected_value,
                    new_amount=d.actual_value,
                )

        result = ReconciliationResult(
            discrepancies_found=len(discrepancies),
            positions_closed=closed,
            positions_created=created,
            positions_updated=updated,
            success=True,
            discrepancies=discrepancies,
        )

        _log(
            "position_reconciliation_complete",
            f"Reconciliation complete: {len(discrepancies)} discrepancies, "
            f"{closed} closed, {created} created, {updated} updated",
            discrepancies_found=len(discrepancies),
            positions_closed=closed,
            positions_created=created,
            positions_updated=updated,
        )

        return result

    def run(
        self,
        wallet_address: str,
        repository: Any,
        *,
        provider: OnChainProvider | None = None,
    ) -> ReconciliationResult:
        """Orchestrate full reconciliation: query → compare → reconcile.

        Args:
            wallet_address: The wallet address to reconcile.
            repository: DatabaseRepository with get_positions(), save_position().
            provider: Optional override provider.

        Returns:
            ReconciliationResult summarizing the run.
        """
        _log(
            "position_reconciliation_start",
            f"Starting position reconciliation for {wallet_address}",
            wallet_address=wallet_address,
        )

        # 1. Query on-chain balances
        onchain = self.query_onchain_balances(wallet_address, provider=provider)

        # 2. Get DB positions
        db_positions_orm = repository.get_positions(status="open")
        db_positions = [
            {
                "position_id": p.position_id,
                "strategy": p.strategy,
                "protocol": p.protocol,
                "chain": p.chain,
                "asset": p.asset,
                "amount": str(p.amount),
                "current_value": str(p.current_value),
                "status": p.status,
            }
            for p in db_positions_orm
        ]

        # 3. Compare
        discrepancies = self.compare_positions(onchain, db_positions)

        # 4. Reconcile
        if discrepancies:
            return self.reconcile(discrepancies, repository)

        _log(
            "position_reconciliation_complete",
            "No discrepancies found — on-chain state matches database",
            wallet_address=wallet_address,
        )

        return ReconciliationResult(
            discrepancies_found=0,
            positions_closed=0,
            positions_created=0,
            positions_updated=0,
            success=True,
        )
