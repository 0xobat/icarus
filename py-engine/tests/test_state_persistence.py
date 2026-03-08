"""Tests for HARNESS-001 — state persistence wiring in DecisionLoop."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from main import DecisionLoop

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_redis() -> MagicMock:
    redis = MagicMock()
    redis.connect = MagicMock()
    redis.disconnect = MagicMock()
    redis.publish = MagicMock()
    redis.subscribe = MagicMock()
    return redis


def _mock_db() -> tuple[MagicMock, MagicMock]:
    db_manager = MagicMock()
    db_manager.create_tables = MagicMock()
    db_manager.close = MagicMock()
    repository = MagicMock()
    repository.get_trades = MagicMock(return_value=[])
    repository.get_positions = MagicMock(return_value=[])
    return db_manager, repository


def _mock_state() -> MagicMock:
    state = MagicMock()
    state.save = MagicMock()
    return state


def _make_loop() -> DecisionLoop:
    redis = _mock_redis()
    db_manager, repository = _mock_db()
    state = _mock_state()
    loop = DecisionLoop(redis, db_manager, repository, state)
    # Mock external calls
    loop.price_feed.fetch_prices = MagicMock(return_value={
        "ETH": {"price_usd": 2000},
        "USDC": {"price_usd": 1},
    })
    loop.gas_monitor.update = MagicMock(return_value=SimpleNamespace(
        fast=50, standard=30, slow=20, timestamp="2025-01-01T00:00:00Z",
    ))
    loop.gas_monitor.get_rolling_average = MagicMock(return_value=Decimal("30"))
    loop.oracle_guard.check = MagicMock(return_value=SimpleNamespace(
        safe=True, deviations=[], stale=False, reason="ok",
    ))
    loop.oracle_guard.get_deviations = MagicMock(return_value={})
    return loop


def _make_event() -> dict:
    return {
        "version": "1.0.0",
        "eventType": "new_block",
        "chain": "ethereum",
        "timestamp": "2025-01-01T00:00:00Z",
        "correlationId": "test-cid",
    }


# ---------------------------------------------------------------------------
# Startup: positions loaded from PostgreSQL
# ---------------------------------------------------------------------------

class TestStartupLoadPositions:

    def test_tracker_has_repository_set(self) -> None:
        loop = _make_loop()
        assert loop.tracker._repository is not None

    def test_loads_open_positions_from_db(self) -> None:
        redis = _mock_redis()
        db_manager, repository = _mock_db()
        state = _mock_state()

        # Simulate open positions in the database
        mock_pos = MagicMock()
        mock_pos.position_id = "pos-1"
        mock_pos.strategy = "LEND-001"
        mock_pos.protocol = "aave_v3"
        mock_pos.chain = "base"
        mock_pos.asset = "USDC"
        mock_pos.entry_price = Decimal("1.0")
        mock_pos.entry_time = MagicMock()
        mock_pos.entry_time.isoformat.return_value = "2025-01-01T00:00:00+00:00"
        mock_pos.amount = Decimal("1000")
        mock_pos.current_value = Decimal("1000")
        mock_pos.unrealized_pnl = Decimal("0")
        mock_pos.status = "open"

        repository.get_positions = MagicMock(side_effect=lambda status=None: (
            [mock_pos] if status == "open" else []
        ))

        loop = DecisionLoop(redis, db_manager, repository, state)

        # Verify position was loaded into tracker
        positions = loop.tracker.query()
        assert len(positions) == 1
        assert positions[0].id == "pos-1"

    def test_startup_survives_db_failure(self) -> None:
        redis = _mock_redis()
        db_manager, repository = _mock_db()
        state = _mock_state()

        repository.get_positions = MagicMock(
            side_effect=Exception("DB connection failed"),
        )

        # Should not raise — falls back to empty tracker
        loop = DecisionLoop(redis, db_manager, repository, state)
        positions = loop.tracker.query()
        assert len(positions) == 0


# ---------------------------------------------------------------------------
# process_result: trade recording
# ---------------------------------------------------------------------------

class TestTradeRecording:

    def test_confirmed_result_records_trade(self) -> None:
        loop = _make_loop()
        loop.tx_failures.record_success = MagicMock()

        result = {
            "orderId": "order-1",
            "correlationId": "corr-1",
            "status": "confirmed",
            "strategy": "LEND-001",
            "protocol": "aave_v3",
            "chain": "base",
            "action": "supply",
            "params": {"tokenIn": "USDC", "amount": "1000"},
            "txHash": "0xabc123",
            "gasUsed": 150000,
            "position_id": "pos-1",
        }
        loop.process_result(result)

        loop.repository.record_trade.assert_called_once()
        trade_data = loop.repository.record_trade.call_args[0][0]
        assert trade_data["trade_id"] == "order-1"
        assert trade_data["correlation_id"] == "corr-1"
        assert trade_data["strategy"] == "LEND-001"
        assert trade_data["protocol"] == "aave_v3"
        assert trade_data["chain"] == "base"
        assert trade_data["action"] == "supply"
        assert trade_data["asset_in"] == "USDC"
        assert trade_data["amount_in"] == "1000"
        assert trade_data["tx_hash"] == "0xabc123"
        assert trade_data["gas_used"] == 150000
        assert trade_data["status"] == "confirmed"

    def test_failed_result_records_trade_with_error(self) -> None:
        loop = _make_loop()
        loop.tx_failures.record_failure = MagicMock()

        result = {
            "orderId": "order-2",
            "status": "failed",
            "reason": "out_of_gas",
            "error": "insufficient gas",
            "params": {},
        }
        loop.process_result(result)

        loop.repository.record_trade.assert_called_once()
        trade_data = loop.repository.record_trade.call_args[0][0]
        assert trade_data["status"] == "failed"
        assert trade_data["error_message"] == "insufficient gas"

    def test_trade_recording_failure_does_not_crash(self) -> None:
        loop = _make_loop()
        loop.tx_failures.record_success = MagicMock()
        loop.repository.record_trade = MagicMock(
            side_effect=Exception("DB write failed"),
        )

        result = {
            "orderId": "order-3",
            "status": "confirmed",
            "position_id": "pos-1",
            "action": "open",
        }
        # Should not raise
        loop.process_result(result)

    def test_trade_recorded_with_defaults_for_missing_fields(self) -> None:
        loop = _make_loop()
        loop.tx_failures.record_success = MagicMock()

        result = {
            "orderId": "order-4",
            "status": "confirmed",
            "position_id": "pos-1",
            "action": "open",
        }
        loop.process_result(result)

        trade_data = loop.repository.record_trade.call_args[0][0]
        assert trade_data["strategy"] == "unknown"
        assert trade_data["protocol"] == "unknown"
        assert trade_data["chain"] == "base"
        assert trade_data["asset_in"] == "unknown"
        assert trade_data["amount_in"] == "0"


# ---------------------------------------------------------------------------
# run_cycle: decision audit log
# ---------------------------------------------------------------------------

class TestDecisionAuditLog:

    def test_decision_recorded_on_adjust(self) -> None:
        loop = _make_loop()
        loop.synthesizer.synthesize = MagicMock(return_value=SimpleNamespace(
            to_dict=lambda: {
                "active_signals": [{
                    "type": "entry_met",
                    "urgency": "critical",
                    "strategy_id": "LEND-001",
                    "parameters": {
                        "chain": "base",
                        "protocol": "aave_v3",
                        "action": "supply",
                        "tokenIn": "USDC",
                        "amount": "1000",
                    },
                }],
            },
        ))

        orders = loop.run_cycle(_make_event())

        loop.repository.record_decision.assert_called_once()
        decision_data = loop.repository.record_decision.call_args[0][0]
        assert decision_data["correlation_id"] == "test-cid"
        assert decision_data["passed_verification"] == (len(orders) > 0)

    def test_decision_not_recorded_on_hold(self) -> None:
        loop = _make_loop()
        loop.synthesizer.synthesize = MagicMock(return_value=SimpleNamespace(
            to_dict=lambda: {"active_signals": []},
        ))

        loop.run_cycle(_make_event())

        # HOLD decisions return early before risk gate, no audit needed
        loop.repository.record_decision.assert_not_called()

    def test_decision_recording_failure_does_not_crash(self) -> None:
        loop = _make_loop()
        loop.repository.record_decision = MagicMock(
            side_effect=Exception("DB write failed"),
        )
        loop.synthesizer.synthesize = MagicMock(return_value=SimpleNamespace(
            to_dict=lambda: {
                "active_signals": [{
                    "type": "entry_met",
                    "urgency": "critical",
                    "strategy_id": "LEND-001",
                    "parameters": {
                        "chain": "base",
                        "protocol": "aave_v3",
                        "action": "supply",
                        "tokenIn": "USDC",
                        "amount": "1000",
                    },
                }],
            },
        ))

        # Should not raise
        orders = loop.run_cycle(_make_event())
        assert isinstance(orders, list)


# ---------------------------------------------------------------------------
# persist_state: positions synced to PostgreSQL
# ---------------------------------------------------------------------------

class TestPersistState:

    def test_syncs_positions_to_db(self) -> None:
        loop = _make_loop()
        loop.tracker.sync_all_to_db = MagicMock()

        loop.persist_state()

        loop.tracker.sync_all_to_db.assert_called_once()
        loop.state.save.assert_called_once()

    def test_persist_survives_sync_failure(self) -> None:
        loop = _make_loop()
        loop.tracker.sync_all_to_db = MagicMock(
            side_effect=Exception("DB sync failed"),
        )

        # Should not raise — state.save still called
        loop.persist_state()
        loop.state.save.assert_called_once()
