"""Tests for on-chain position reconciliation — DATA-004."""

from __future__ import annotations

from typing import Any

import pytest

from data.reconciliation import (
    AaveDeposit,
    AgentState,
    Discrepancy,
    DiscrepancyType,
    LPPosition,
    OnChainBalance,
    OnChainState,
    PositionDiscrepancy,
    PositionReconciler,
    Reconciler,
    ReconciliationResult,
    Severity,
    TokenBalance,
)

# ── Helpers ───────────────────────────────────────────


def _chain(
    balances: dict[str, float] | None = None,
    aave: dict[str, float] | None = None,
    lps: dict[str, float] | None = None,
) -> OnChainState:
    return OnChainState(
        wallet_address="0xtest",
        token_balances=[TokenBalance(t, b) for t, b in (balances or {}).items()],
        aave_deposits=[AaveDeposit(t, d) for t, d in (aave or {}).items()],
        lp_positions=[LPPosition(p, 0, 0, liq) for p, liq in (lps or {}).items()],
    )


def _agent(
    balances: dict[str, float] | None = None,
    aave: dict[str, float] | None = None,
    lps: dict[str, float] | None = None,
    pending_txs: list[dict[str, Any]] | None = None,
) -> AgentState:
    return AgentState(
        wallet_address="0xtest",
        token_balances=[TokenBalance(t, b) for t, b in (balances or {}).items()],
        aave_deposits=[AaveDeposit(t, d) for t, d in (aave or {}).items()],
        lp_positions=[LPPosition(p, 0, 0, liq) for p, liq in (lps or {}).items()],
        pending_txs=pending_txs or [],
    )


# ── Tests: No discrepancies ─────────────────────────


class TestMatchingState:
    def test_empty_states_match(self) -> None:
        rec = Reconciler()
        result = rec.reconcile(_chain(), _agent())
        assert result == []

    def test_identical_balances_match(self) -> None:
        rec = Reconciler()
        on_chain = _chain(balances={"ETH": 1.5, "USDC": 1000.0})
        agent = _agent(balances={"ETH": 1.5, "USDC": 1000.0})
        result = rec.reconcile(on_chain, agent)
        assert result == []

    def test_within_tolerance_matches(self) -> None:
        rec = Reconciler()
        # Tiny rounding difference within 0.01% tolerance
        on_chain = _chain(balances={"ETH": 1.50000001})
        agent = _agent(balances={"ETH": 1.5})
        result = rec.reconcile(on_chain, agent)
        assert result == []


# ── Tests: Missing position detection ────────────────


class TestMissingPosition:
    def test_agent_has_balance_not_on_chain(self) -> None:
        rec = Reconciler()
        on_chain = _chain(balances={})
        agent = _agent(balances={"ETH": 1.5})
        result = rec.reconcile(on_chain, agent)

        assert len(result) == 1
        assert result[0].type == DiscrepancyType.MISSING_POSITION
        assert result[0].severity == Severity.MANUAL_REVIEW
        assert result[0].token == "ETH"

    def test_agent_has_aave_deposit_not_on_chain(self) -> None:
        rec = Reconciler()
        on_chain = _chain(aave={})
        agent = _agent(aave={"USDC": 5000.0})
        result = rec.reconcile(on_chain, agent)

        assert len(result) == 1
        assert result[0].type == DiscrepancyType.MISSING_POSITION
        assert result[0].token == "aave:USDC"

    def test_agent_has_lp_not_on_chain(self) -> None:
        rec = Reconciler()
        on_chain = _chain(lps={})
        agent = _agent(lps={"ETH/USDC": 1000.0})
        result = rec.reconcile(on_chain, agent)

        assert len(result) == 1
        assert result[0].type == DiscrepancyType.MISSING_POSITION
        assert result[0].token == "lp:ETH/USDC"


# ── Tests: Unexpected balance detection ──────────────


class TestUnexpectedBalance:
    def test_on_chain_has_token_agent_doesnt(self) -> None:
        rec = Reconciler()
        on_chain = _chain(balances={"LINK": 100.0})
        agent = _agent(balances={})
        result = rec.reconcile(on_chain, agent)

        assert len(result) == 1
        assert result[0].type == DiscrepancyType.UNEXPECTED_BALANCE
        assert result[0].severity == Severity.MANUAL_REVIEW
        assert result[0].on_chain_value == 100.0

    def test_unexpected_aave_deposit(self) -> None:
        rec = Reconciler()
        on_chain = _chain(aave={"DAI": 2000.0})
        agent = _agent(aave={})
        result = rec.reconcile(on_chain, agent)

        assert len(result) == 1
        assert result[0].type == DiscrepancyType.UNEXPECTED_BALANCE
        assert result[0].token == "aave:DAI"

    def test_unexpected_lp_position(self) -> None:
        rec = Reconciler()
        on_chain = _chain(lps={"WBTC/ETH": 500.0})
        agent = _agent(lps={})
        result = rec.reconcile(on_chain, agent)

        assert len(result) == 1
        assert result[0].type == DiscrepancyType.UNEXPECTED_BALANCE
        assert result[0].token == "lp:WBTC/ETH"


# ── Tests: Balance mismatch / unrecorded TX ──────────


class TestBalanceMismatch:
    def test_token_balance_mismatch(self) -> None:
        rec = Reconciler()
        on_chain = _chain(balances={"ETH": 2.0})
        agent = _agent(balances={"ETH": 1.5})
        result = rec.reconcile(on_chain, agent)

        assert len(result) == 1
        assert result[0].type == DiscrepancyType.BALANCE_MISMATCH
        assert result[0].on_chain_value == 2.0
        assert result[0].agent_value == 1.5

    def test_pending_tx_makes_mismatch_auto_fixable(self) -> None:
        rec = Reconciler()
        on_chain = _chain(balances={"ETH": 2.0})
        agent = _agent(
            balances={"ETH": 1.5},
            pending_txs=[{"token": "ETH", "tx_hash": "0xabc"}],
        )
        result = rec.reconcile(on_chain, agent)

        assert len(result) == 1
        assert result[0].severity == Severity.AUTO_FIXABLE

    def test_pending_tx_makes_unexpected_auto_fixable(self) -> None:
        rec = Reconciler()
        on_chain = _chain(balances={"LINK": 50.0})
        agent = _agent(
            balances={},
            pending_txs=[{"token": "LINK", "tx_hash": "0xdef"}],
        )
        result = rec.reconcile(on_chain, agent)

        assert len(result) == 1
        assert result[0].severity == Severity.AUTO_FIXABLE

    def test_aave_interest_accrual_auto_fixable(self) -> None:
        """Aave deposit mismatches are auto-fixable (interest accrual)."""
        rec = Reconciler()
        on_chain = _chain(aave={"USDC": 5050.0})  # Accrued interest
        agent = _agent(aave={"USDC": 5000.0})
        result = rec.reconcile(on_chain, agent)

        assert len(result) == 1
        assert result[0].severity == Severity.AUTO_FIXABLE
        assert result[0].type == DiscrepancyType.BALANCE_MISMATCH


# ── Tests: Auto-reconciliation ───────────────────────


class TestAutoReconcile:
    def test_resolves_auto_fixable(self) -> None:
        rec = Reconciler()
        discrepancies = [
            Discrepancy(
                type=DiscrepancyType.BALANCE_MISMATCH,
                severity=Severity.AUTO_FIXABLE,
                token="ETH",
                details="Balance mismatch",
                on_chain_value=2.0,
                agent_value=1.5,
            ),
        ]

        remaining = rec.auto_reconcile(discrepancies)
        assert remaining == []
        assert discrepancies[0].resolved is True

    def test_keeps_manual_review(self) -> None:
        rec = Reconciler()
        discrepancies = [
            Discrepancy(
                type=DiscrepancyType.UNEXPECTED_BALANCE,
                severity=Severity.MANUAL_REVIEW,
                token="LINK",
                details="Unexpected balance",
            ),
        ]

        remaining = rec.auto_reconcile(discrepancies)
        assert len(remaining) == 1
        assert remaining[0].token == "LINK"
        assert remaining[0].resolved is False

    def test_mixed_discrepancies(self) -> None:
        rec = Reconciler()
        discrepancies = [
            Discrepancy(
                type=DiscrepancyType.BALANCE_MISMATCH,
                severity=Severity.AUTO_FIXABLE,
                token="aave:USDC",
                details="Interest accrual",
            ),
            Discrepancy(
                type=DiscrepancyType.MISSING_POSITION,
                severity=Severity.MANUAL_REVIEW,
                token="ETH",
                details="Missing position",
            ),
            Discrepancy(
                type=DiscrepancyType.BALANCE_MISMATCH,
                severity=Severity.AUTO_FIXABLE,
                token="ETH",
                details="Pending TX confirmed",
            ),
        ]

        remaining = rec.auto_reconcile(discrepancies)
        assert len(remaining) == 1
        assert remaining[0].token == "ETH"
        assert remaining[0].type == DiscrepancyType.MISSING_POSITION


# ── Tests: Alert logging ─────────────────────────────


class TestAlertLogging:
    def test_logs_discrepancies(self, capsys: pytest.CaptureFixture[str]) -> None:
        rec = Reconciler()
        on_chain = _chain(balances={"LINK": 100.0})
        agent = _agent(balances={})
        rec.reconcile(on_chain, agent)

        captured = capsys.readouterr()
        assert "reconciliation_discrepancy" in captured.out
        assert "reconciliation_alert" in captured.out
        assert "LINK" in captured.out

    def test_logs_ok_on_match(self, capsys: pytest.CaptureFixture[str]) -> None:
        rec = Reconciler()
        rec.reconcile(_chain(), _agent())

        captured = capsys.readouterr()
        assert "reconciliation_ok" in captured.out

    def test_auto_reconcile_logs(self, capsys: pytest.CaptureFixture[str]) -> None:
        rec = Reconciler()
        discrepancies = [
            Discrepancy(
                type=DiscrepancyType.BALANCE_MISMATCH,
                severity=Severity.AUTO_FIXABLE,
                token="ETH",
                details="test fix",
            ),
            Discrepancy(
                type=DiscrepancyType.MISSING_POSITION,
                severity=Severity.MANUAL_REVIEW,
                token="USDC",
                details="test review",
            ),
        ]
        rec.auto_reconcile(discrepancies)

        captured = capsys.readouterr()
        assert "auto_reconcile" in captured.out
        assert "manual_review_required" in captured.out


# ── Tests: Discrepancy model ────────────────────────


class TestDiscrepancyModel:
    def test_to_dict(self) -> None:
        d = Discrepancy(
            type=DiscrepancyType.BALANCE_MISMATCH,
            severity=Severity.AUTO_FIXABLE,
            token="ETH",
            details="test",
            on_chain_value=2.0,
            agent_value=1.5,
        )
        result = d.to_dict()
        assert result["type"] == "balance_mismatch"
        assert result["severity"] == "auto_fixable"
        assert result["token"] == "ETH"

    def test_utc_timestamp(self) -> None:
        d = Discrepancy(
            type=DiscrepancyType.MISSING_POSITION,
            severity=Severity.MANUAL_REVIEW,
            token="ETH",
            details="test",
        )
        assert "+00:00" in d.timestamp


# ── Tests: Fetch on-chain ────────────────────────────


class TestFetchOnChain:
    def test_uses_injected_function(self) -> None:
        expected = _chain(balances={"ETH": 5.0})

        def mock_fetch(addr: str) -> OnChainState:
            return expected

        rec = Reconciler(fetch_on_chain_fn=mock_fetch)
        result = rec.fetch_on_chain_state("0xtest")
        assert result.token_balances[0].balance == 5.0

    def test_raises_without_function(self) -> None:
        rec = Reconciler()
        with pytest.raises(RuntimeError, match="No on-chain fetch function"):
            rec.fetch_on_chain_state("0xtest")

    def test_configurable_interval(self) -> None:
        rec = Reconciler(interval_seconds=900)
        assert rec.interval_seconds == 900


# ══════════════════════════════════════════════════════
# PositionReconciler tests (DATA-004)
# ══════════════════════════════════════════════════════


class _MockProvider:
    """Mock on-chain provider returning preset balances."""

    def __init__(self, balances: list[dict[str, Any]]) -> None:
        self._balances = balances

    def get_token_balances(self, wallet_address: str) -> list[dict[str, Any]]:
        return self._balances


class _FakePosition:
    """Minimal stand-in for PortfolioPosition ORM objects."""

    def __init__(
        self,
        position_id: str,
        strategy: str,
        protocol: str,
        chain: str,
        asset: str,
        amount: float,
        current_value: float,
        status: str = "open",
    ) -> None:
        self.position_id = position_id
        self.strategy = strategy
        self.protocol = protocol
        self.chain = chain
        self.asset = asset
        self.amount = amount
        self.current_value = current_value
        self.status = status


def _bal(
    symbol: str,
    balance: float,
    addr: str = "0x0",
    protocol: str = "wallet",
) -> dict[str, Any]:
    """Build a token balance dict for _MockProvider."""
    return {
        "token_symbol": symbol,
        "balance": balance,
        "contract_address": addr,
        "protocol": protocol,
    }


class _MockRepository:
    """Mock DatabaseRepository tracking save_position calls."""

    def __init__(self, positions: list[_FakePosition] | None = None) -> None:
        self._positions = list(positions or [])
        self.saved: list[dict[str, Any]] = []

    def get_positions(self, *, status: str | None = None) -> list[_FakePosition]:
        if status is not None:
            return [p for p in self._positions if p.status == status]
        return list(self._positions)

    def save_position(self, pos_data: dict[str, Any]) -> None:
        self.saved.append(pos_data)


# ── PositionReconciler.query_onchain_balances ────────


class TestPositionQueryOnchain:
    def test_returns_balances_keyed_by_protocol_asset(self) -> None:
        provider = _MockProvider([
            _bal("USDC", 1000.0, "0xabc", "wallet"),
            _bal("USDC", 500.0, "0xdef", "aave_v3"),
        ])
        reconciler = PositionReconciler(provider)
        result = reconciler.query_onchain_balances("0xwallet")

        assert "wallet:USDC" in result
        assert "aave_v3:USDC" in result
        assert result["wallet:USDC"].balance == 1000.0
        assert result["aave_v3:USDC"].balance == 500.0

    def test_provider_override(self) -> None:
        default_provider = _MockProvider([])
        override_provider = _MockProvider([
            _bal("DAI", 200.0, "0x1", "wallet"),
        ])
        reconciler = PositionReconciler(default_provider)
        result = reconciler.query_onchain_balances("0xwallet", provider=override_provider)

        assert "wallet:DAI" in result
        assert len(result) == 1

    def test_no_provider_raises(self) -> None:
        reconciler = PositionReconciler()
        with pytest.raises(RuntimeError, match="No on-chain provider"):
            reconciler.query_onchain_balances("0xwallet")

    def test_empty_balances(self) -> None:
        provider = _MockProvider([])
        reconciler = PositionReconciler(provider)
        result = reconciler.query_onchain_balances("0xwallet")
        assert result == {}


# ── PositionReconciler.compare_positions ─────────────


class TestPositionCompare:
    def setup_method(self) -> None:
        self.reconciler = PositionReconciler()

    def test_no_discrepancies_when_matching(self) -> None:
        onchain = {
            "aave_v3:USDC": OnChainBalance("USDC", 1000.0, "0xabc", "aave_v3"),
        }
        db_positions = [{
            "position_id": "pos-1",
            "strategy": "LEND-001",
            "protocol": "aave_v3",
            "chain": "base",
            "asset": "USDC",
            "amount": "1000.0",
            "current_value": "1000.0",
            "status": "open",
        }]

        discrepancies = self.reconciler.compare_positions(onchain, db_positions)
        assert discrepancies == []

    def test_missing_db_position(self) -> None:
        onchain = {
            "aave_v3:USDC": OnChainBalance("USDC", 500.0, "0xabc", "aave_v3"),
        }

        discrepancies = self.reconciler.compare_positions(onchain, [])
        assert len(discrepancies) == 1
        assert discrepancies[0].discrepancy_type == "missing_db"
        assert discrepancies[0].actual_value == 500.0
        assert discrepancies[0].position_id is None

    def test_missing_onchain(self) -> None:
        db_positions = [{
            "position_id": "pos-1",
            "strategy": "LEND-001",
            "protocol": "aave_v3",
            "chain": "base",
            "asset": "USDC",
            "amount": "1000.0",
            "current_value": "1000.0",
            "status": "open",
        }]

        discrepancies = self.reconciler.compare_positions({}, db_positions)
        assert len(discrepancies) == 1
        assert discrepancies[0].discrepancy_type == "missing_onchain"
        assert discrepancies[0].position_id == "pos-1"

    def test_value_mismatch(self) -> None:
        onchain = {
            "aave_v3:USDC": OnChainBalance("USDC", 1200.0, "0xabc", "aave_v3"),
        }
        db_positions = [{
            "position_id": "pos-1",
            "strategy": "LEND-001",
            "protocol": "aave_v3",
            "chain": "base",
            "asset": "USDC",
            "amount": "1000.0",
            "current_value": "1000.0",
            "status": "open",
        }]

        discrepancies = self.reconciler.compare_positions(onchain, db_positions)
        assert len(discrepancies) == 1
        assert discrepancies[0].discrepancy_type == "value_mismatch"
        assert discrepancies[0].expected_value == 1000.0
        assert discrepancies[0].actual_value == 1200.0

    def test_within_tolerance_no_discrepancy(self) -> None:
        onchain = {
            "aave_v3:USDC": OnChainBalance("USDC", 1000.05, "0xabc", "aave_v3"),
        }
        db_positions = [{
            "position_id": "pos-1",
            "strategy": "LEND-001",
            "protocol": "aave_v3",
            "chain": "base",
            "asset": "USDC",
            "amount": "1000.0",
            "current_value": "1000.0",
            "status": "open",
        }]

        discrepancies = self.reconciler.compare_positions(onchain, db_positions)
        assert discrepancies == []

    def test_closed_positions_ignored(self) -> None:
        db_positions = [{
            "position_id": "pos-1",
            "strategy": "LEND-001",
            "protocol": "aave_v3",
            "chain": "base",
            "asset": "USDC",
            "amount": "1000.0",
            "current_value": "1000.0",
            "status": "closed",
        }]

        discrepancies = self.reconciler.compare_positions({}, db_positions)
        assert discrepancies == []

    def test_zero_onchain_balance_ignored(self) -> None:
        onchain = {
            "wallet:USDC": OnChainBalance("USDC", 0.0, "0xabc", "wallet"),
        }

        discrepancies = self.reconciler.compare_positions(onchain, [])
        assert discrepancies == []

    def test_multiple_discrepancies(self) -> None:
        onchain = {
            "aave_v3:USDC": OnChainBalance("USDC", 800.0, "0xabc", "aave_v3"),
            "aerodrome:USDC-DAI": OnChainBalance("USDC-DAI", 300.0, "0xdef", "aerodrome"),
        }
        db_positions = [
            {
                "position_id": "pos-1",
                "strategy": "LEND-001",
                "protocol": "aave_v3",
                "chain": "base",
                "asset": "USDC",
                "amount": "1000.0",
                "current_value": "1000.0",
                "status": "open",
            },
            {
                "position_id": "pos-2",
                "strategy": "LP-001",
                "protocol": "wallet",
                "chain": "base",
                "asset": "DAI",
                "amount": "500.0",
                "current_value": "500.0",
                "status": "open",
            },
        ]

        discrepancies = self.reconciler.compare_positions(onchain, db_positions)
        types = {d.discrepancy_type for d in discrepancies}
        assert "value_mismatch" in types
        assert "missing_db" in types
        assert "missing_onchain" in types


# ── PositionReconciler.reconcile ─────────────────────


class TestPositionReconcile:
    def setup_method(self) -> None:
        self.reconciler = PositionReconciler()
        self.repo = _MockRepository()

    def test_missing_onchain_closes_position(self) -> None:
        discrepancies = [
            PositionDiscrepancy("pos-1", 1000.0, 0.0, "USDC", "aave_v3", "missing_onchain"),
        ]

        result = self.reconciler.reconcile(discrepancies, self.repo)
        assert result.positions_closed == 1
        assert len(self.repo.saved) == 1
        assert self.repo.saved[0]["status"] == "closed"
        assert self.repo.saved[0]["position_id"] == "pos-1"

    def test_missing_db_creates_position(self) -> None:
        discrepancies = [
            PositionDiscrepancy(None, 0.0, 500.0, "USDbC", "aave_v3", "missing_db"),
        ]

        result = self.reconciler.reconcile(discrepancies, self.repo)
        assert result.positions_created == 1
        assert self.repo.saved[0]["status"] == "open"
        assert self.repo.saved[0]["amount"] == 500.0
        assert self.repo.saved[0]["position_id"].startswith("reconciled-")

    def test_value_mismatch_updates_position(self) -> None:
        discrepancies = [
            PositionDiscrepancy("pos-1", 1000.0, 1200.0, "USDC", "aave_v3", "value_mismatch"),
        ]

        result = self.reconciler.reconcile(discrepancies, self.repo)
        assert result.positions_updated == 1
        assert self.repo.saved[0]["amount"] == 1200.0
        assert self.repo.saved[0]["position_id"] == "pos-1"

    def test_mixed_discrepancies(self) -> None:
        discrepancies = [
            PositionDiscrepancy("pos-1", 1000.0, 0.0, "USDC", "aave_v3", "missing_onchain"),
            PositionDiscrepancy(None, 0.0, 300.0, "DAI", "wallet", "missing_db"),
            PositionDiscrepancy("pos-2", 500.0, 600.0, "USDbC", "aave_v3", "value_mismatch"),
        ]

        result = self.reconciler.reconcile(discrepancies, self.repo)
        assert result.discrepancies_found == 3
        assert result.positions_closed == 1
        assert result.positions_created == 1
        assert result.positions_updated == 1
        assert result.success is True
        assert len(self.repo.saved) == 3

    def test_empty_discrepancies(self) -> None:
        result = self.reconciler.reconcile([], self.repo)
        assert result.discrepancies_found == 0
        assert result.success is True
        assert len(self.repo.saved) == 0


# ── PositionReconciler.run (full orchestration) ──────


class TestPositionRun:
    def test_full_run_no_discrepancies(self) -> None:
        provider = _MockProvider([
            _bal("USDC", 1000.0, "0xabc", "aave_v3"),
        ])
        positions = [
            _FakePosition("pos-1", "LEND-001", "aave_v3", "base", "USDC", 1000.0, 1000.0),
        ]
        repo = _MockRepository(positions)
        reconciler = PositionReconciler(provider)

        result = reconciler.run("0xwallet", repo)
        assert result.discrepancies_found == 0
        assert result.success is True
        assert len(repo.saved) == 0

    def test_full_run_with_discrepancies(self) -> None:
        provider = _MockProvider([
            _bal("USDC", 800.0, "0xabc", "aave_v3"),
            _bal("DAI", 200.0, "0xdef", "wallet"),
        ])
        positions = [
            _FakePosition("pos-1", "LEND-001", "aave_v3", "base", "USDC", 1000.0, 1000.0),
        ]
        repo = _MockRepository(positions)
        reconciler = PositionReconciler(provider)

        result = reconciler.run("0xwallet", repo)
        assert result.discrepancies_found == 2
        assert result.positions_updated == 1
        assert result.positions_created == 1
        assert result.success is True

    def test_full_run_position_gone_onchain(self) -> None:
        provider = _MockProvider([])
        positions = [
            _FakePosition("pos-1", "LEND-001", "aave_v3", "base", "USDC", 1000.0, 1000.0),
        ]
        repo = _MockRepository(positions)
        reconciler = PositionReconciler(provider)

        result = reconciler.run("0xwallet", repo)
        assert result.discrepancies_found == 1
        assert result.positions_closed == 1
        assert repo.saved[0]["status"] == "closed"

    def test_full_run_with_provider_override(self) -> None:
        default_provider = _MockProvider([])
        override_provider = _MockProvider([
            _bal("USDC", 1000.0, "0xabc", "aave_v3"),
        ])
        positions = [
            _FakePosition("pos-1", "LEND-001", "aave_v3", "base", "USDC", 1000.0, 1000.0),
        ]
        repo = _MockRepository(positions)
        reconciler = PositionReconciler(default_provider)

        result = reconciler.run("0xwallet", repo, provider=override_provider)
        assert result.discrepancies_found == 0
        assert result.success is True

    def test_structured_logging(self, capsys: pytest.CaptureFixture[str]) -> None:
        provider = _MockProvider([
            _bal("USDC", 800.0, "0xabc", "aave_v3"),
        ])
        positions = [
            _FakePosition("pos-1", "LEND-001", "aave_v3", "base", "USDC", 1000.0, 1000.0),
        ]
        repo = _MockRepository(positions)
        reconciler = PositionReconciler(provider)

        reconciler.run("0xwallet", repo)
        captured = capsys.readouterr()
        assert "position_reconciliation_start" in captured.out
        assert "position_reconciliation_discrepancy" in captured.out
        assert "position_reconciliation_action" in captured.out
        assert "position_reconciliation_complete" in captured.out


# ── PositionReconciler data models ───────────────────


class TestPositionDataModels:
    def test_discrepancy_to_dict(self) -> None:
        d = PositionDiscrepancy(
            position_id="pos-1",
            expected_value=100.0,
            actual_value=200.0,
            asset="USDC",
            protocol="aave_v3",
            discrepancy_type="value_mismatch",
        )
        result = d.to_dict()
        assert result["position_id"] == "pos-1"
        assert result["discrepancy_type"] == "value_mismatch"
        assert "timestamp" in result

    def test_reconciliation_result_defaults(self) -> None:
        r = ReconciliationResult(
            discrepancies_found=0,
            positions_closed=0,
            positions_created=0,
            positions_updated=0,
            success=True,
        )
        assert r.discrepancies == []
        assert r.timestamp != ""
