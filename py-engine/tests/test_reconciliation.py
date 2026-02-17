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
    OnChainState,
    Reconciler,
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
