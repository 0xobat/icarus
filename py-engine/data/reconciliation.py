"""On-chain position reconciliation — compare on-chain state against agent records."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

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
    MISSING_POSITION = "missing_position"
    UNEXPECTED_BALANCE = "unexpected_balance"
    UNRECORDED_TX = "unrecorded_tx"
    BALANCE_MISMATCH = "balance_mismatch"


class Severity(StrEnum):
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
